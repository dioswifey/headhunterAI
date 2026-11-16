"""검색 쿼리 성능 테스트 (2026-11-16) — 질문 은행(Redis) · 사내문서(Chroma) 다수 질의 측정.

제품 코드가 아니라 평가 도구다. 제품 함수(`vectorstore.knn`, `internal_store`, `internal_search.search`,
`question_graph.start`·`_redis_query_vector`, `question_chain.QUERY_INSTRUCT`)를 **고치지 않고 그대로** 부른다.
모델 호출은 로컬 임베딩(`llm.embed`, qwen3-embedding:0.6b)뿐이다. 생성 모델은 부르지 않는다.

    # api 컨테이너 안 (backend/tools를 /srv/tools로 복사한 뒤)
    python -m tools.eval_query_perf run --gen /tmp/qperf/gen.json --answers /tmp/qperf/answers.json --out /tmp/qperf/query_perf.json
    # 호스트 (openpyxl)
    python backend/tools/eval_query_perf.py excel --json <query_perf.json> --out <xlsx>

질의 세트
  R1 질문 은행 model_answer → 그 문항            (번역 완료 문항 중 model_answer가 있는 것)
  R2 시뮬레이션 출제 원문(orig) → 은행 문항 ID    (gen_method = redis:<id>)
  R3 지원자 답변 → 그 질문의 은행 문항 ID        (answers.json)
  C1 제품 질의(공고 + A5 키워드, 그래프 1-A 조립 그대로) → 사내문서
  C2 시뮬레이션 기업 질문 문장 → 사내문서
  C3 지원자의 기업 질문 답변 → 사내문서
  C4 사내문서 '문서 제목 + 섹션 제목' → 그 조각(정답 있는 대리 세트, LLM 없이 만든 것)
조건: 지시문(QUERY_INSTRUCT) 붙임/안 붙임 · Redis 사전 필터 있음/없음 · Chroma where 있음/없음 · k(1·4·8, 12)
"""
import argparse
import json
import os
import random
import statistics as st
import sys
import time

DATE = "2026-11-16"


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))
    return round(xs[i], 2)


def lat(xs):
    return {"n": len(xs), "mean_ms": round(st.mean(xs), 2) if xs else None, "p50_ms": pct(xs, 50), "p95_ms": pct(xs, 95)}


def rank_of(ids, target):
    return ids.index(target) + 1 if target in ids else None


def ir_metrics(ranks, n_total):
    """ranks: 정답 순위(없으면 None) 목록. 반환 Recall@1·4·8 · MRR(@검색 깊이)."""
    def rec(k):
        return round(sum(1 for r in ranks if r and r <= k) / n_total, 4) if n_total else None
    mrr = round(sum(1 / r for r in ranks if r) / n_total, 4) if n_total else None
    return {"n": n_total, "recall@1": rec(1), "recall@4": rec(4), "recall@8": rec(8), "mrr": mrr,
            "not_found_in_depth": sum(1 for r in ranks if not r)}


# ======================= 측정 (컨테이너 안) =======================
def run(a):
    from app import db, internal_store, llm, vectorstore
    from app.agents import internal_forbid, internal_search
    from app.agents import question_chain as QC
    from app.agents import question_graph as QG

    random.seed(20261116)
    db.pool.open()
    r = vectorstore.client()
    out = {"meta": {"date": DATE, "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"), "embed_model": None,
                    "query_instruct": QC.QUERY_INSTRUCT, "redis_index": vectorstore.INDEX,
                    "chroma_public": internal_store.count(internal_store.PUBLIC),
                    "chroma_restricted": internal_store.count(internal_store.RESTRICTED),
                    "internal_search": {"K": internal_search.K, "MIN_SIM": internal_search.MIN_SIM,
                                        "TOP_N": internal_search.TOP_N, "MAX_PER_DOC": internal_search.MAX_PER_DOC,
                                        "ALLOWED_STATUS": list(internal_search.ALLOWED_STATUS)},
                    "generation_model_calls": 0},
           "sets": {}, "results": {}, "latency": {}, "details": {}}
    from app import config
    out["meta"]["embed_model"] = f"{config.EMBED_MODEL} · {config.EMBED_DIM}차원"

    emb_cache = {}

    def embed_all(texts, instruct):
        key = [(t, instruct) for t in texts]
        todo = [t for t in dict.fromkeys(texts) if (t, instruct) not in emb_cache]
        for i in range(0, len(todo), 16):
            chunk = todo[i:i + 16]
            vecs = llm.embed([(QC.QUERY_INSTRUCT + t) if instruct else t for t in chunk])
            for t, v in zip(chunk, vecs):
                emb_cache[(t, instruct)] = v
        return [emb_cache[k] for k in key]

    # ---------- 질의 세트 R1~R3 ----------
    docs = {}
    for k in r.scan_iter(match="qb:*", count=500):
        if k.startswith(b"qb:__"):
            continue
        f = r.hmget(k, "role_code", "category", "translated", "model_answer", "expected_keywords", "question_ko")
        docs[k.decode()[3:]] = {"role": (f[0] or b"").decode(), "cat": (f[1] or b"").decode(), "tr": (f[2] or b"").decode(),
                                "ma": (f[3] or b"").decode(), "ek": (f[4] or b"").decode(), "q": (f[5] or b"").decode()}
    R1 = [{"qid": d, "text": v["ma"][:1500], "target": d, "role": v["role"], "cat": v["cat"]}
          for d, v in sorted(docs.items()) if v["ma"].strip() and v["tr"] == "1"]
    n_ek = sum(1 for v in docs.values() if v["ek"].strip())

    gen = json.load(open(a.gen, encoding="utf-8"))
    ans = json.load(open(a.answers, encoding="utf-8"))
    R2, R3 = [], []
    for p in gen["people"]:
        for q in p["questions"]:
            gm = q.get("gen_method") or ""
            if not gm.startswith("redis:"):
                continue
            target = gm[len("redis:"):].split("|")[0]
            if target not in docs:
                continue
            roles = [p["role_code"]] if q["category"] == "job" else ["common", p["role_code"]]
            base = {"target": target, "cat": q["category"], "roles": roles, "pid": p["pid"], "qno": q["qno"]}
            R2.append({**base, "qid": q["qid"], "text": q["orig"]})
            at = ((ans.get(p["pid"]) or {}).get("answers") or {}).get(q["qno"])
            if at:
                R3.append({**base, "qid": q["qid"] + "-ans", "text": at[:1500]})
    out["sets"] = {
        "R1": {"desc": "질문 은행 model_answer → 그 문항 (번역 완료 · model_answer 있음)", "n": len(R1),
               "note": f"expected_keywords가 채워진 문항 {n_ek}개 → 키워드 질의 세트는 만들 수 없음"},
        "R2": {"desc": "시뮬레이션 출제 원문(orig) → 은행 문항 ID (gen_method redis:<id>)", "n": len(R2),
               "unique_targets": len({x['target'] for x in R2})},
        "R3": {"desc": "지원자 답변(answers.json) → 그 질문의 은행 문항 ID", "n": len(R3)}}

    def redis_eval(name, items, instruct, filtered, depth=8):
        vecs = embed_all([x["text"] for x in items], instruct)
        ranks, lats, det = [], [], []
        for x, v in zip(items, vecs):
            if filtered:
                roles = x.get("roles") or ([x["role"]] if x["cat"] == "job" else ["common", "ai_ax"])
                kw = dict(role_codes=roles, categories=[x["cat"]], translated_only=True)
            else:
                kw = {}
            t0 = time.perf_counter()
            hits = vectorstore.knn(v, k=depth, r=r, **kw)
            lats.append((time.perf_counter() - t0) * 1000)
            ids = [h["doc_id"] for h in hits]
            rk = rank_of(ids, x["target"])
            ranks.append(rk)
            det.append({"set": name, "qid": x["qid"], "target": x["target"], "rank": rk, "top1": ids[0] if ids else None,
                        "top1_sim": hits[0]["sim"] if hits else None,
                        "target_sim": next((h["sim"] for h in hits if h["doc_id"] == x["target"]), None),
                        "instruct": instruct, "filtered": filtered, "query_head": x["text"][:80]})
        return ir_metrics(ranks, len(items)), lat(lats), det

    for name, items in (("R1", R1), ("R2", R2), ("R3", R3)):
        for instruct in (True, False):
            for filtered in (True, False):
                cond = f"{name}|instruct={int(instruct)}|filter={int(filtered)}"
                m, l, det = redis_eval(name, items, instruct, filtered)
                out["results"][cond] = m
                out["latency"]["redis_knn|" + cond] = l
                if instruct and filtered:       # 제품과 같은 조건만 상세에 남긴다
                    out["details"].setdefault("redis", []).extend(det)

    # ---------- 임베딩 지연 (단건 호출, 제품 retrieve와 같은 모양) ----------
    el = []
    for x in random.sample(R2 + R3, min(30, len(R2 + R3))):
        t0 = time.perf_counter()
        llm.embed([QC.QUERY_INSTRUCT + x["text"]])                # 단건 호출 시간(제품 retrieve와 같은 모양)
        el.append((time.perf_counter() - t0) * 1000)
    out["latency"]["embed_single_query"] = lat(el)

    # ---------- 사내문서 질의 세트 C1~C4 ----------
    terms = internal_forbid.load_terms()
    cterms = internal_forbid.chunk_terms(terms)
    where = {"$and": [{"disclosure": {"$eq": "public"}}, {"status": {"$in": list(internal_search.ALLOWED_STATUS)}}]}
    coll = internal_store.collection(internal_store.PUBLIC)

    C1, C2, C3 = [], [], []
    for p in gen["people"]:
        s = QG.start({"application": {"job_id": p["job_id"], "resume_snapshot": p["snapshot"]}})
        s.update({"keyword_report": p.get("keyword_report") or {}, "keywords": []})
        kws = (s["keyword_report"] or {}).get("keywords") or []
        qtext = QC._query({"job": s["job"], "resume_keywords": kws[:12]})
        base = {"pid": p["pid"], "role": p["role_code"], "company": p["company"], "sim_refs": p.get("internal_refs") or []}
        C1.append({**base, "qid": p["pid"] + "-query", "text": qtext, "product_vector": True})
        cq = [q for q in p["questions"] if q["category"] == "company"]
        if cq:
            C2.append({**base, "qid": cq[0]["qid"], "text": cq[0]["text"]})
            at = ((ans.get(p["pid"]) or {}).get("answers") or {}).get(cq[0]["qno"])
            if at:
                C3.append({**base, "qid": cq[0]["qid"] + "-ans", "text": at[:1500]})
    allc = internal_store.chunks(internal_store.PUBLIC)
    pool = [c for c in allc if c["metadata"].get("status") in internal_search.ALLOWED_STATUS]
    C4 = []
    for c in random.sample(pool, min(120, len(pool))):
        m = c["metadata"]
        C4.append({"qid": c["id"], "text": f"{m.get('doc_title', '')} {m.get('sec_title', '')}".strip(),
                   "target": f"{m.get('doc_id')}#{m.get('sec', '')}", "role": None})
    out["sets"].update({
        "C1": {"desc": "제품 질의(그래프 1-A `question_chain._query`: 공고 직무명·업무·요건 + A5 키워드 12개) → 사내문서", "n": len(C1)},
        "C2": {"desc": "시뮬레이션 기업 질문 문장 → 사내문서", "n": len(C2)},
        "C3": {"desc": "지원자의 기업 질문 답변 → 사내문서", "n": len(C3)},
        "C4": {"desc": "사내문서 '문서 제목 + 섹션 제목' → 그 조각(doc_id#sec 일치, LLM 없이 만든 대리 정답 세트)",
               "n": len(C4), "sampled_from": len(pool)}})

    def chroma_eval(name, items, instruct):
        vecs = embed_all([x["text"] for x in items], instruct)
        top1, passrate, any_pass, nrefs, ranks, repro = [], [], [], [], [], []
        lw, ln, ls, det = [], [], [], []
        for x, v in zip(items, vecs):
            t0 = time.perf_counter()
            hw = internal_store.query_public(v, k=12, where=where)
            lw.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            rn = coll.query(query_embeddings=[v], n_results=12, include=["metadatas", "distances"])
            ln.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            res = internal_search.search(v, role_code=x.get("role"), forbid_terms=cterms)
            ls.append((time.perf_counter() - t0) * 1000)
            sims = [h["sim"] for h in hw]
            top1.append(sims[0] if sims else 0)
            passrate.append(sum(s >= internal_search.MIN_SIM for s in sims) / 12)
            any_pass.append(any(s >= internal_search.MIN_SIM for s in sims))
            nrefs.append(len(res["refs"]))
            ids_w = [f"{h['metadata']['doc_id']}#{h['metadata'].get('sec', '')}" for h in hw]
            ids_n = [f"{m['doc_id']}#{m.get('sec', '')}" for m in rn["metadatas"][0]]
            same_top = ids_w[:4] == ids_n[:4]
            rk = None
            if x.get("target"):
                rk = rank_of(list(dict.fromkeys(ids_w)), x["target"])
                ranks.append(rk)
            if x.get("sim_refs") is not None and name == "C1":
                repro.append(res["refs"] == x["sim_refs"])
            det.append({"set": name, "qid": x["qid"], "instruct": instruct, "top1_sim": round(top1[-1], 4),
                        "pass_ge_0.55_of_12": sum(s >= internal_search.MIN_SIM for s in sims), "final_refs": res["refs"],
                        "top4_same_with_without_where": same_top, "target": x.get("target"), "rank": rk,
                        "sim_refs": x.get("sim_refs") if name in ("C1", "C2", "C3") else None, "query_head": x["text"][:80]})
        m = {"n": len(items), "top1_sim_p50": pct(top1, 50), "top1_sim_min": round(min(top1), 4) if top1 else None,
             "top1_sim_max": round(max(top1), 4) if top1 else None,
             "share_of_k12_ge_MIN_SIM": round(st.mean(passrate), 4) if passrate else None,
             "queries_with_any_ge_MIN_SIM": round(sum(any_pass) / len(any_pass), 4) if any_pass else None,
             "final_refs_mean": round(st.mean(nrefs), 2) if nrefs else None,
             "queries_with_0_refs": sum(1 for n in nrefs if n == 0)}
        if ranks:
            m.update({k: v for k, v in ir_metrics(ranks, len(ranks)).items() if k != "n"})
        if repro:
            m["same_refs_as_simulation"] = f"{sum(repro)}/{len(repro)}"
        return m, {"where": lat(lw), "no_where": lat(ln), "search_full": lat(ls)}, det

    for name, items in (("C1", C1), ("C2", C2), ("C3", C3), ("C4", C4)):
        for instruct in (True, False):
            cond = f"{name}|instruct={int(instruct)}"
            m, l, det = chroma_eval(name, items, instruct)
            out["results"][cond] = m
            for kk, vv in l.items():
                out["latency"][f"chroma_{kk}|{cond}"] = vv
            if instruct:
                out["details"].setdefault("chroma", []).extend(det)

    out["meta"]["embeddings_called"] = len(emb_cache) + len(el)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"저장: {a.out} · 임베딩 {out['meta']['embeddings_called']}회 · 생성 모델 0회")


# ======================= 엑셀 (호스트) =======================
def excel(a):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    d = json.load(open(a.json, encoding="utf-8"))
    wb = openpyxl.Workbook()
    head = PatternFill("solid", fgColor="111111")
    hf = Font(bold=True, color="FFFFFF")

    def sheet(title, header, rows, widths=None):
        ws = wb.create_sheet(title)
        ws.append(header)
        for c in ws[1]:
            c.fill, c.font = head, hf
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for row in rows:
            ws.append([json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in row])
        for i, w in enumerate(widths or [18] * len(header), 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        return ws

    m = d["meta"]
    ws = wb.active
    ws.title = "개요"
    rows = [("문서", f"headhunterAI 검색 쿼리 성능 테스트 ({m['date']})"),
            ("측정 시각", m["measured_at"]),
            ("목적", "그래프 1-A가 쓰는 검색(질문 은행 Redis · 사내문서 Chroma)이 다수 질의에서 정답을 얼마나 위에 올리는지, 유사도 하한·필터·지시문 조건에 따라 어떻게 달라지는지 측정"),
            ("방법", "제품 함수를 고치지 않고 그대로 호출(vectorstore.knn · internal_store.query_public · internal_search.search · question_graph.start · question_chain._query). 스크립트 backend/tools/eval_query_perf.py"),
            ("모델 호출", f"임베딩 {m['embed_model']} {m.get('embeddings_called')}회 · 생성 모델 {m['generation_model_calls']}회"),
            ("질의 지시문", m["query_instruct"].replace("\n", " ")),
            ("저장소", f"Redis {m['redis_index']} · Chroma public {m['chroma_public']} / restricted {m['chroma_restricted']}조각"),
            ("사내문서 검색 상수", json.dumps(m["internal_search"], ensure_ascii=False)),
            ("데이터", "질의 원천 = 질문 은행 문항, 가상 지원자 20명 시뮬레이션(gen.json·answers.json, 실제 지원자 데이터 0건), 가상 회사 사내문서"),
            ("주의", "R2는 출제 원문 = 은행 문항 문장이라 거의 정답(상한 확인용). C4는 섹션 제목을 질의로 쓴 대리 정답 세트로, 실제 면접 질의보다 쉽다. 사람이 고른 정답 골든셋(G-검색)은 아직 없음"),
            ("시트", "질의 세트 · 결과 요약 · 질의별 상세 · 조건 비교")]
    for r_ in rows:
        ws.append(r_)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 18, 120
    for row in ws.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        row[0].font = Font(bold=True)

    sheet("질의 세트", ["세트", "설명", "질의 수 n", "비고"],
          [(k, v["desc"], v["n"], v.get("note") or (f"정답 문항 {v['unique_targets']}개" if v.get("unique_targets") else (f"표본 모집단 {v['sampled_from']}조각" if v.get("sampled_from") else "")))
           for k, v in d["sets"].items()], [8, 70, 10, 40])

    res, L = d["results"], d["latency"]
    rows = []
    for name in ("R1", "R2", "R3"):
        k = f"{name}|instruct=1|filter=1"
        x, l = res[k], L["redis_knn|" + k]
        rows.append((name, "질문 은행 Redis (제품 조건: 지시문 + 사전 필터)", x["n"], x["recall@1"], x["recall@4"], x["recall@8"], x["mrr"],
                     x["not_found_in_depth"], l["p50_ms"], l["p95_ms"], ""))
    for name in ("C1", "C2", "C3", "C4"):
        k = f"{name}|instruct=1"
        x = res[k]
        lw, ln = L[f"chroma_where|{k}"], L[f"chroma_no_where|{k}"]
        extra = f"top1 유사도 p50 {x['top1_sim_p50']} · k12 중 ≥0.55 비율 {x['share_of_k12_ge_MIN_SIM']} · ≥0.55 하나라도 있는 질의 {x['queries_with_any_ge_MIN_SIM']} · 최종 조각 평균 {x['final_refs_mean']} · 0조각 질의 {x['queries_with_0_refs']}"
        if x.get("same_refs_as_simulation"):
            extra += f" · 시뮬레이션과 같은 조각 {x['same_refs_as_simulation']}"
        rows.append((name, f"사내문서 Chroma (지시문, where 있음 p50/p95 · 없음 p50 {ln['p50_ms']})", x["n"], x.get("recall@1"), x.get("recall@4"),
                     x.get("recall@8"), x.get("mrr"), x.get("not_found_in_depth"), lw["p50_ms"], lw["p95_ms"], extra))
    rows.append(("임베딩", "단건 질의 임베딩 호출(제품 retrieve와 같은 모양)", L["embed_single_query"]["n"], None, None, None, None, None,
                 L["embed_single_query"]["p50_ms"], L["embed_single_query"]["p95_ms"], "Ollama HTTP 왕복 포함"))
    sheet("결과 요약", ["세트", "대상 · 조건", "n", "Recall@1", "Recall@4", "Recall@8", "MRR", "깊이 안에 정답 없음", "지연 p50 ms", "지연 p95 ms", "사내문서 지표"],
          rows, [8, 44, 6, 10, 10, 10, 8, 12, 11, 11, 80])

    det = []
    for x in d["details"].get("redis", []):
        det.append(("Redis", x["set"], x["qid"], x["target"], x["rank"], x["top1"], x["top1_sim"], x["target_sim"], None, None, x["query_head"]))
    for x in d["details"].get("chroma", []):
        det.append(("Chroma", x["set"], x["qid"], x.get("target"), x.get("rank"), ", ".join(x["final_refs"]), x["top1_sim"], None,
                    x["pass_ge_0.55_of_12"], ", ".join(x["sim_refs"]) if x.get("sim_refs") else None, x["query_head"]))
    sheet("질의별 상세", ["저장소", "세트", "질의 ID", "정답", "정답 순위(8위 안)", "1위 문항 / 최종 조각", "1위 유사도", "정답 유사도",
                         "k12 중 ≥0.55", "시뮬레이션 때 조각", "질의 앞부분"], det, [8, 6, 22, 24, 10, 40, 10, 10, 10, 40, 60])

    rows = []
    for name in ("R1", "R2", "R3"):
        for ins in (1, 0):
            for flt in (1, 0):
                k = f"{name}|instruct={ins}|filter={flt}"
                x, l = res[k], L["redis_knn|" + k]
                rows.append(("Redis", name, "붙임" if ins else "안 붙임", "있음(직무·카테고리·번역)" if flt else "없음(전체 235)",
                             x["recall@1"], x["recall@4"], x["recall@8"], x["mrr"], l["p50_ms"], l["p95_ms"], None, None))
    for name in ("C1", "C2", "C3", "C4"):
        for ins in (1, 0):
            k = f"{name}|instruct={ins}"
            x = res[k]
            lw, ln, ls = L[f"chroma_where|{k}"], L[f"chroma_no_where|{k}"], L[f"chroma_search_full|{k}"]
            rows.append(("Chroma", name, "붙임" if ins else "안 붙임", "where 있음(제품)", x.get("recall@1"), x.get("recall@4"), x.get("recall@8"),
                         x.get("mrr"), lw["p50_ms"], lw["p95_ms"], ln["p50_ms"], f"top1 p50 {x['top1_sim_p50']} · ≥0.55 질의 {x['queries_with_any_ge_MIN_SIM']} · search() p50 {ls['p50_ms']} ms"))
    sheet("조건 비교", ["저장소", "세트", "지시문", "필터", "Recall@1", "Recall@4", "Recall@8", "MRR", "지연 p50 ms", "지연 p95 ms",
                       "where 없음 p50 ms", "비고"], rows, [8, 6, 9, 22, 10, 10, 10, 8, 11, 11, 14, 60])
    wb.save(a.out)
    print(f"저장: {a.out}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("run")
    p.add_argument("--gen", required=True)
    p.add_argument("--answers", required=True)
    p.add_argument("--out", required=True)
    p = sp.add_parser("excel")
    p.add_argument("--json", required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    (run if a.cmd == "run" else excel)(a)


if __name__ == "__main__":
    sys.exit(main())
