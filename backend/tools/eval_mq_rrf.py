"""Multi-Query + RRF 전후 비교 (2026-11-16, ADLC v0.8 6.18) — 평가 도구(제품 코드 아님).

A = 기존 단일 쿼리 검색(기준선) · B = Multi-Query + RRF. 같은 질의 세트(가상 지원자 20명의 그래프 1-A 입력)로 비교한다.
제품 함수를 그대로 부른다: question_graph.start · extract_keywords · _multi_queries · _knn_fused · vectorstore.knn ·
internal_search.search · internal_search.search_multi · generate_set. 제품 코드는 고치지 않는다.

정답(관련성) = 풀링 판정: 질의마다 A·B 상위 5개를 합친 후보를 로컬 qwen3:4b(temperature 0, num_ctx 고정)가 0/1/2로 판정.
  Recall@5 = 상위 5개에 든 관련(≥1) 항목 / 후보 풀 안의 관련 항목 전체(pooled recall)
  MRR@5    = 첫 관련 항목 순위의 역수(5위 안, 없으면 0)
  nDCG@5   = 등급 이득(2^g − 1) 기준, 이상적 순서 = 후보 풀 등급 내림차순
  관련 항목이 하나도 없는 질의는 평균에서 빼고 개수를 따로 적는다.

    python -m tools.eval_mq_rrf retrieval --gen /tmp/qperf/gen.json --out /tmp/mq/mq_retrieval.json
    python -m tools.eval_mq_rrf fallback  --gen /tmp/qperf/gen.json --out /tmp/mq/mq_fallback.json
    python -m tools.eval_mq_rrf pipeline  --gen /tmp/qperf/gen.json --pids P03,P05,P09,P15 --out /tmp/mq/mq_pipeline.json
    python -m tools.eval_mq_rrf pipeline3 --gen /tmp/qperf/gen.json --pids P01,P03,P05,P09,P11,P15 --out /tmp/mq/dual_pipeline.json  # A/B/C
"""
import argparse
import json
import math
import os
import re
import statistics as st
import time

JUDGE_CTX = 4096
JUDGE_MODEL = None       # None = config.LLM_MODEL(qwen3:4b)


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))], 2)


def lat(xs):
    return {"n": len(xs), "mean_ms": round(st.mean(xs), 2) if xs else None, "p50_ms": pct(xs, 50), "p95_ms": pct(xs, 95)}


def _timed(fn, *a, **kw):
    t0 = time.perf_counter()
    r = fn(*a, **kw)
    return r, (time.perf_counter() - t0) * 1000


def _state(QG, p):
    """시뮬레이션 때와 같은 그래프 1-A 입력(start → extract_keywords → A5 결과 반영). A5 모델 호출은 하지 않고 저장된 결과를 쓴다."""
    s = QG.start({"application": {"job_id": p["job_id"], "resume_snapshot": p["snapshot"]}})
    s["use_llm"] = True
    s.update(QG.extract_keywords(s))
    kr = p.get("keyword_report") or {}
    s["keyword_report"] = kr
    removed = {re.sub(r"\s+", "", x["term"].lower()) for x in kr.get("removed", []) or []}
    s["candidates"] = [c for c in s["candidates"] if re.sub(r"\s+", "", c["label"].lower()) not in removed] or s["candidates"]
    return s


def metrics(ranked, grades, k=5):
    """ranked: 키 목록(순위순), grades: {키: 0|1|2} (후보 풀 전체)."""
    rel = {x for x, g in grades.items() if g >= 1}
    if not rel:
        return None
    top = ranked[:k]
    rec = len([x for x in top if x in rel]) / len(rel)
    mrr = next((1 / (i + 1) for i, x in enumerate(top) if x in rel), 0.0)
    dcg = sum((2 ** grades.get(x, 0) - 1) / math.log2(i + 2) for i, x in enumerate(top))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return {"recall@5": rec, "mrr@5": mrr, "ndcg@5": dcg / idcg if idcg else 0.0}


def avg(rows, key):
    xs = [r[key] for r in rows if r]
    return round(st.mean(xs), 4) if xs else None


JUDGE_SCHEMA = {"type": "object", "properties": {"grade": {"type": "integer", "minimum": 0, "maximum": 2},
                                                 "reason": {"type": "string"}}, "required": ["grade", "reason"]}
JUDGE_SYSTEM = """너는 AI 1차 면접의 검색 결과를 판정하는 평가자다. 지원 공고와 지원자 이력서 키워드, 그리고 검색된 항목 하나가 주어진다.
검색 항목은 질문 은행의 면접 질문이거나 회사 사내문서 조각이다. 이 항목이 **이 공고·이 지원자의 면접 질문을 만드는 근거로** 얼마나 적합한지 판정한다.
- 2: 공고의 직무·업무 또는 지원자 경험과 직접 관련되어 바로 근거로 쓸 수 있다
- 1: 부분적으로 관련된다(직무 일반·회사 일반 수준)
- 0: 이 공고·지원자와 관련이 없다(다른 직무·무관한 사내 업무 등)
인성 질문은 직무와 무관하게 누구에게나 물을 수 있으면 1, 공고 업무 상황과 특히 잘 맞으면 2, 이 직무에 어색하면 0.
grade(0~2)와 reason(한 문장)만 JSON으로 답한다."""


def run_retrieval(a):
    from app import config, db, internal_store, llm, vectorstore
    from app.agents import internal_forbid, internal_search
    from app.agents import question_chain as QC
    from app.agents import question_graph as QG

    db.pool.open()
    config.RAG_MULTI_QUERY = True
    gen = json.load(open(a.gen, encoding="utf-8"))
    cterms = internal_forbid.chunk_terms(internal_forbid.load_terms())
    r = vectorstore.client()
    judge_cache, n_judge, judge_ms = {}, 0, []

    def judge(pctx, kind, key, text):
        nonlocal n_judge
        ck = (pctx["pid"], kind, key)
        if ck in judge_cache:
            return judge_cache[ck]
        user = (f"[공고] {pctx['company']} · {pctx['title']} ({pctx['role']})\n주요 업무: {pctx['duties'][:400]}\n"
                f"자격 요건: {pctx['req'][:300]}\n[지원자 이력서 키워드] {', '.join(pctx['kws'][:12])}\n\n"
                f"[검색 항목 — {kind}]\n{text[:900]}")
        t0 = time.perf_counter()
        try:
            out = llm.chat_json(JUDGE_SYSTEM, user, JUDGE_SCHEMA, timeout=90, temperature=0, model=JUDGE_MODEL,
                                max_tokens=160, num_ctx=JUDGE_CTX)
            g = int(out.get("grade", 0))
        except llm.LLMError:
            g = None
        judge_ms.append((time.perf_counter() - t0) * 1000)
        n_judge += 1
        judge_cache[ck] = g
        return g

    rows, lat_rec = [], {k: [] for k in ("embed_A", "embed_B", "redis_A", "redis_B5", "redis_B16", "chroma_A", "chroma_B",
                                         "stage_A", "stage_B")}
    counts = []
    for p in gen["people"]:
        s = _state(QG, p)
        job, role = s["job"], p["role_code"]
        kws = (s.get("keyword_report") or {}).get("keywords") or []
        pctx = {"pid": p["pid"], "company": p["company"], "title": p["job_title"], "role": job.get("role_name") or role,
                "duties": job.get("duties") or "", "req": job.get("requirements") or "", "kws": kws}
        qs = QG._multi_queries(s)
        vA, tA = _timed(llm.embed, [QC.QUERY_INSTRUCT + qs[0]])
        vB, tB = _timed(llm.embed, [QC.QUERY_INSTRUCT + q for q in qs])
        lat_rec["embed_A"].append(tA)
        lat_rec["embed_B"].append(tB)
        stage_a, stage_b = tA, tB
        cnt = {"pid": p["pid"], "queries": len(qs), "query_chars": [len(q) for q in qs], "embed_requests_A": 1,
               "embed_inputs_A": 1, "embed_requests_B": 1, "embed_inputs_B": len(qs)}
        for cat, roles in (("personality", ["common", role]), ("job", [role]), ("company", ["common", role])):
            kw = dict(role_codes=roles, categories=[cat], translated_only=True)
            hA, t = _timed(vectorstore.knn, vA[0], k=16, **kw)
            lat_rec["redis_A"].append(t)
            stage_a += t
            if not hA:
                cnt[f"redis_{cat}"] = "0 hits (은행에 문항 없음)"
                continue
            per_query = [len(vectorstore.knn(v, k=16, **kw)) for v in vB]
            hB16, t = _timed(QG._knn_fused, vB, roles, cat, 16)
            lat_rec["redis_B16"].append(t)
            stage_b += t
            hB5, t = _timed(QG._knn_fused, vB, roles, cat, 5)
            lat_rec["redis_B5"].append(t)
            cnt[f"redis_{cat}"] = {"per_query_hits": per_query, "sum_before_dedupe": sum(per_query), "fused_unique_k16": len(hB16),
                                   "fused_unique_k5": len(hB5)}
            texts = {h["doc_id"]: f"{h.get('question_ko') or h.get('question')}\n(평가 의도: {h.get('intent_ko') or h.get('competency') or ''})"
                     for h in hA + hB16 + hB5}
            rk = {"A": [h["doc_id"] for h in hA], "B16": [h["doc_id"] for h in hB16], "B5": [h["doc_id"] for h in hB5]}
            pool = list(dict.fromkeys(rk["A"][:5] + rk["B16"][:5] + rk["B5"][:5]))
            grades = {d: judge(pctx, f"질문 은행 {cat}", d, texts[d]) for d in pool}
            grades = {d: g for d, g in grades.items() if g is not None}
            rows.append({"pid": p["pid"], "store": "redis", "cat": cat, "pool": len(pool),
                         "relevant": sum(1 for g in grades.values() if g >= 1), "top5": {k: v[:5] for k, v in rk.items()},
                         "grades": grades, **{f"m_{k}": metrics(v, grades) for k, v in rk.items()}})
        # 사내문서
        rA, t = _timed(internal_search.search, vA[0], role_code=role, forbid_terms=cterms)
        lat_rec["chroma_A"].append(t)
        stage_a += t
        rB, t = _timed(internal_search.search_multi, vB, role_code=role, forbid_terms=cterms)
        lat_rec["chroma_B"].append(t)
        stage_b += t
        lat_rec["stage_A"].append(stage_a)
        lat_rec["stage_B"].append(stage_b)
        cnt["chroma"] = {"A_ranked": len(rA["ranked"]), "B_per_query": rB["mq"]["per_query"],
                         "B_sum_before_dedupe": sum(rB["mq"]["per_query"]), "B_fused_unique": rB["mq"]["fused"],
                         "A_refs": rA["refs"], "B_refs": rB["refs"]}
        counts.append(cnt)
        ids = list(dict.fromkeys(rA["ranked"][:5] + rB["ranked"][:5]))
        got = internal_store.collection(internal_store.PUBLIC).get(ids=ids, include=["documents", "metadatas"]) if ids else {"ids": []}
        txt = {i: f"{(m or {}).get('doc_title', '')} > {(m or {}).get('sec_title', '')}\n{doc}"
               for i, doc, m in zip(got["ids"], got.get("documents") or [], got.get("metadatas") or [])}
        grades = {i: judge(pctx, "사내문서 조각", i, txt.get(i, "")) for i in ids}
        grades = {i: g for i, g in grades.items() if g is not None}
        rk = {"A": rA["ranked"], "B": rB["ranked"]}
        rows.append({"pid": p["pid"], "store": "chroma", "cat": "internal", "pool": len(ids),
                     "relevant": sum(1 for g in grades.values() if g >= 1), "top5": {k: v[:5] for k, v in rk.items()},
                     "final_refs": {"A": rA["refs"], "B": rB["refs"]}, "grades": grades,
                     **{f"m_{k}": metrics(v, grades) for k, v in rk.items()}})

    summary = {}
    for store, cat, variants in (("redis", "personality", ("A", "B5", "B16")), ("redis", "job", ("A", "B5", "B16")),
                                 ("redis", "company", ("A", "B5", "B16")), ("chroma", "internal", ("A", "B"))):
        sel = [x for x in rows if x["store"] == store and x["cat"] == cat]
        ev = [x for x in sel if x["relevant"] > 0]
        summary[f"{store}/{cat}"] = {"queries": len(sel), "queries_with_relevant": len(ev),
                                     "queries_no_relevant": len(sel) - len(ev),
                                     "pool_mean": round(st.mean([x["pool"] for x in sel]), 2) if sel else None,
                                     **{v: {m: avg([x[f"m_{v}"] for x in ev], m) for m in ("recall@5", "mrr@5", "ndcg@5")} for v in variants}}
        if store == "redis":
            summary[f"{store}/{cat}"]["same_top5_set_A_vs_B16"] = sum(1 for x in sel if set(x["top5"]["A"]) == set(x["top5"]["B16"]))
            summary[f"{store}/{cat}"]["same_top5_order_A_vs_B16"] = sum(1 for x in sel if x["top5"]["A"] == x["top5"]["B16"])
        else:
            summary[f"{store}/{cat}"]["same_final_refs"] = sum(1 for x in sel if x["final_refs"]["A"] == x["final_refs"]["B"])
            summary[f"{store}/{cat}"]["final_refs_mean"] = {"A": round(st.mean([len(x["final_refs"]["A"]) for x in sel]), 2),
                                                            "B": round(st.mean([len(x["final_refs"]["B"]) for x in sel]), 2)}
    out = {"meta": {"date": "2026-11-16", "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"), "people": len(gen["people"]),
                    "embed_model": config.EMBED_MODEL, "judge_model": JUDGE_MODEL or config.LLM_MODEL, "judge_num_ctx": JUDGE_CTX,
                    "judge_calls": n_judge, "judge_latency": lat(judge_ms), "rrf_k": config.RAG_RRF_K, "mq_n": config.RAG_MQ_N,
                    "redis_k_A": 16, "redis_k_B": [5, 16], "chroma_k": internal_search.K, "metric_k": 5},
           "summary": summary, "latency": {k: lat(v) for k, v in lat_rec.items()}, "counts": counts, "rows": rows}
    json.dump(out, open(a.out, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(out["latency"], ensure_ascii=False))
    print(f"판정 {n_judge}회")


def run_fallback(a):
    """오류 대체 경로 검증. 모델(생성)은 부르지 않는다. 임베딩은 실제 호출."""
    from app import config, db, llm, vectorstore
    from app.agents import internal_search, rag_fusion
    from app.agents import question_graph as QG

    db.pool.open()
    gen = json.load(open(a.gen, encoding="utf-8"))
    p = next(x for x in gen["people"] if x["role_code"] == "ai_ax")
    res = []

    def case(name, ok, detail):
        res.append({"case": name, "pass": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name, detail)

    # 1) RRF 계산·중복 제거·결정성
    L1 = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    L2 = [{"id": "b"}, {"id": "b"}, {"id": "d"}, {"id": "a"}]
    f = rag_fusion.rrf([L1, L2], key=lambda x: x["id"], k=60)
    exp_b = 1 / 62 + 1 / 61
    exp_a = 1 / 61 + 1 / 64           # L2의 두 번째 b는 버리되 순위는 원래 위치 기준 → a는 L2에서 4위
    case("rrf 공식·목록 내 중복 제거", f[0]["id"] == "b" and abs(f[0]["rrf_score"] - round(exp_b, 6)) < 1e-6
         and abs(next(x for x in f if x["id"] == "a")["rrf_score"] - round(exp_a, 6)) < 1e-6 and len(f) == 4,
         {x["id"]: x["rrf_score"] for x in f})
    f2 = rag_fusion.rrf([L2, L1], key=lambda x: x["id"], k=60)
    case("rrf 결정성(목록 순서를 바꿔도 같은 점수)", {x["id"]: x["rrf_score"] for x in f} == {x["id"]: x["rrf_score"] for x in f2}, None)
    case("rrf limit", len(rag_fusion.rrf([L1, L2], key=lambda x: x["id"], limit=2)) == 2, None)

    s = _state(QG, p)
    # 2) 스위치 꺼짐 = 기존 단일 쿼리
    config.RAG_MULTI_QUERY = False
    v, rep = QG._query_vectors(s)
    case("스위치 0 → baseline 단일 쿼리", rep["mode"] == "baseline" and len(v) == 1, rep)
    config.RAG_MULTI_QUERY = True
    v, rep = QG._query_vectors(s)
    case("스위치 1 → multi 3쿼리 묶음 임베딩 1회", rep["mode"] == "multi" and len(v) == 3 and rep["embed_calls"] == 1, rep)
    vecs = v
    # 3) 묶음 임베딩 실패 → 단일 쿼리 fallback
    orig_embed = llm.embed

    def bad_batch(texts, *x, **k):
        if len(texts) > 1:
            raise llm.LLMError("forced")
        return orig_embed(texts, *x, **k)
    llm.embed = bad_batch
    try:
        v, rep = QG._query_vectors(s)
        case("묶음 임베딩 실패 → 단일 쿼리 fallback", rep["mode"] == "fallback" and len(v) == 1, rep)
    finally:
        llm.embed = orig_embed
    # 4) Redis 일부 쿼리 실패 → 나머지로 RRF
    orig_knn = vectorstore.knn
    calls = {"n": 0}

    def knn_fail_second(*x, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("forced")
        return orig_knn(*x, **k)
    vectorstore.knn = knn_fail_second
    try:
        h = QG._knn_fused(vecs, ["ai_ax"], "job", 16)
        case("Redis 쿼리 1개 실패 → 나머지 2개로 RRF", len(h) > 0 and all("rrf_score" in x for x in h), {"hits": len(h)})
    finally:
        vectorstore.knn = orig_knn
    # 5) Redis 전부 실패 → retrieve가 DB 은행으로
    vectorstore.knn = lambda *x, **k: (_ for _ in ()).throw(ConnectionError("forced"))
    try:
        out = QG.retrieve(s)
        case("Redis 전부 실패 → 카테고리별 DB 은행 대체", "redis 오류" in out["log"][-1] and all(len(v_) > 0 for k_, v_ in out["picks"].items() if k_ != "resume_bank"),
             out["log"][-1])
    finally:
        vectorstore.knn = orig_knn
    # 6) Chroma RRF 실패 → 단일 쿼리 검색
    ret = QG.retrieve(s)
    s2 = {**s, **ret}
    orig_multi = internal_search.search_multi
    internal_search.search_multi = lambda *x, **k: (_ for _ in ()).throw(RuntimeError("forced"))
    try:
        o = QG.retrieve_internal(s2)
        case("사내문서 RRF 실패 → 단일 쿼리 검색으로 대체", "RRF 실패 → 단일" in o["log"][-1] or "관문 닫힘" in o["log"][-1], o["log"][-1])
    finally:
        internal_search.search_multi = orig_multi
    o = QG.retrieve_internal(s2)
    case("사내문서 정상 → RRF 3/3", "RRF 3/3" in o["log"][-1] or "관문 닫힘" in o["log"][-1], o["log"][-1])
    # 7) 사내문서 RRF 결과도 공개판·상태 재확인(B-09) 통과
    case("RRF 결과 조각 공개판·상태 재확인", all(h["disclosure"] == "public" for h in o.get("internal_hits") or []),
         [h["ref"] for h in o.get("internal_hits") or []])
    # 8) retrieve 결과 State
    case("retrieve State: query_vector = query_vectors[0]", ret["query_vector"] == ret["query_vectors"][0] and len(ret["query_vectors"]) == 3,
         ret["mq_report"])
    json.dump({"pid": p["pid"], "cases": res, "passed": sum(x["pass"] for x in res), "total": len(res)},
              open(a.out, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"{sum(x['pass'] for x in res)}/{len(res)} 통과")


def run_pipeline(a):
    """전체 질문 생성(그래프 1-A GENERATION) 지연을 A/B로 잰다. 생성 모델 호출이 들어간다."""
    from app import config, db, llm
    from app.agents import question_graph as QG

    db.pool.open()
    gen = json.load(open(a.gen, encoding="utf-8"))
    pids = a.pids.split(",")
    calls = {"chat": 0, "embed_req": 0, "embed_inputs": 0}
    oc, oe = llm.chat_json, llm.embed

    def cc(*x, **k):
        calls["chat"] += 1
        return oc(*x, **k)

    def ce(texts, *x, **k):
        calls["embed_req"] += 1
        calls["embed_inputs"] += len(texts)
        return oe(texts, *x, **k)
    llm.chat_json, llm.embed = cc, ce
    runs = []
    for mode in ("A", "B"):
        config.RAG_MULTI_QUERY = mode == "B"
        for pid in pids:
            p = next(x for x in gen["people"] if x["pid"] == pid)
            app_ = {"id": f"eval-{pid}", "job_id": p["job_id"], "resume_snapshot": p["snapshot"]}
            for k_ in calls:
                calls[k_] = 0
            t0 = time.perf_counter()
            err = None
            try:
                qs, info = QG.generate_set(app_, use_llm=True)
            except Exception as e:  # noqa: BLE001
                qs, info, err = [], {"log": []}, f"{type(e).__name__}: {e}"
            sec = time.perf_counter() - t0
            comp = [q for q in qs if q.get("category") == "company"]
            runs.append({"mode": mode, "pid": pid, "seconds": round(sec, 1), "questions": len(qs), "error": err,
                         "chat_calls": calls["chat"], "embed_requests": calls["embed_req"], "embed_inputs": calls["embed_inputs"],
                         "company_rag": bool(comp and "|internal:" in (comp[0].get("gen_method") or "")),
                         "log": [l for l in info.get("log", []) if l.startswith(("retrieve", "generate", "guard"))]})
            print(runs[-1]["mode"], pid, runs[-1]["seconds"], "s", runs[-1]["questions"], "문항", runs[-1]["chat_calls"], "chat",
                  runs[-1]["embed_requests"], "embed", runs[-1]["log"][:2])
    llm.chat_json, llm.embed = oc, oe
    summ = {m: {"runs": len([r for r in runs if r["mode"] == m]),
                "seconds": lat([r["seconds"] * 1000 for r in runs if r["mode"] == m]),
                "questions_ok": sum(1 for r in runs if r["mode"] == m and r["questions"] == 8),
                "errors": sum(1 for r in runs if r["mode"] == m and r["error"]),
                "chat_calls_mean": round(st.mean([r["chat_calls"] for r in runs if r["mode"] == m]), 2),
                "embed_requests_mean": round(st.mean([r["embed_requests"] for r in runs if r["mode"] == m]), 2),
                "embed_inputs_mean": round(st.mean([r["embed_inputs"] for r in runs if r["mode"] == m]), 2),
                "company_rag": sum(1 for r in runs if r["mode"] == m and r["company_rag"])} for m in ("A", "B")}
    json.dump({"pids": pids, "summary": summ, "runs": runs}, open(a.out, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    print(json.dumps(summ, ensure_ascii=False, indent=1))


QJ_SCHEMA = {"type": "object", "properties": {
    "job_relevance": {"type": "integer", "minimum": 1, "maximum": 5},
    "resume_consistency": {"type": "integer", "minimum": 1, "maximum": 5},
    "fabricated_experience": {"type": "boolean"},
    "doc_faithfulness": {"type": "integer", "minimum": 0, "maximum": 5},
    "reason": {"type": "string"}}, "required": ["job_relevance", "resume_consistency", "fabricated_experience", "doc_faithfulness", "reason"]}
QJ_SYSTEM = """너는 AI 1차 면접 질문을 채점하는 독립 심사관이다. 질문을 만든 쪽이 아니며 점수를 후하게 주지 않는다.
공고, 지원자 이력서(지원자가 확인한 내용), 질문 한 개가 주어진다. 질문이 회사 사내문서를 근거로 만들어졌으면 그 사내문서 조각도 주어진다.
1. job_relevance(1~5): 이 공고 직무의 역량·업무와 직접 관련되는가. 5 = 정확히 맞음 / 3 = 어느 직무에나 쓸 일반 질문 / 1 = 무관
2. resume_consistency(1~5): 질문이 지원자에 대해 전제하는 내용이 이력서와 맞는가. 이력서에 없는 경험·역할·성과를 지원자가 한 것처럼 전제하면 낮게.
   지원자 경험을 전제하지 않는 질문(가정 상황·지식 질문)은 5. 5 = 모순 없음 / 3 = 애매한 전제 / 1 = 이력서에 없는 경험을 전제
3. fabricated_experience(true/false): 이력서에 없는 경험을 지원자가 한 것처럼 전제하거나, 회사 사내문서의 업무·프로젝트를 지원자가 이미 한 일처럼 말하면 true
4. doc_faithfulness(0~5): 사내문서 조각이 주어졌을 때만 — 질문에 쓴 회사 업무 맥락이 조각 내용과 맞는가(5 = 조각에 실제로 있는 내용, 1 = 조각과 다르거나 지어냄). 조각이 없으면 0
reason은 가장 낮은 점수의 이유 한 문장. JSON만 답한다."""


def run_pipeline3(a):
    """A/B/C 전체 질문 생성 비교(사용자 지시 2026-11-16, ADLC 6.18.5). 생성·판정 모델 호출이 들어간다(GPU).
    A = 단일 쿼리 Redis RAG(RAG_MULTI_QUERY=0, INTERNAL_DOCS_RAG=0, RAG_DUAL_JOB=0)
    B = Multi-Query + RRF Redis RAG(1, 0, 0)
    C = Multi-Query + RRF Redis + Chroma Dual RAG(1, 1, 1 — 사내문서 근거를 직무 2·기업 1 문항에)
    토큰 = Ollama 응답의 prompt_eval_count·eval_count(/api/chat) · prompt_eval_count(/api/embed)를 httpx 응답에서 센다(제품 코드 무변경).
    노드 지연 = retrieve·retrieve_internal·generate를 시간 재는 래퍼로 감싼 그래프를 다시 컴파일해 잰다(벽시계, 병렬 포함)."""
    import httpx
    from app import config, db, llm
    from app import questions as Q
    from app.agents import internal_forbid
    from app.agents import question_graph as QG

    db.pool.open()
    gen = json.load(open(a.gen, encoding="utf-8"))
    pids = a.pids.split(",")
    terms = internal_forbid.load_terms()
    tok = {"chat": 0, "embed_req": 0, "embed_inputs": 0, "chat_in": 0, "chat_out": 0, "embed_in": 0}
    judging = {"on": False}
    orig_post = httpx.post

    def post(url, *x, **k):
        r = orig_post(url, *x, **k)
        if judging["on"]:
            return r
        try:
            d = r.json()
        except Exception:  # noqa: BLE001
            return r
        if url.endswith("/api/chat"):
            tok["chat"] += 1
            tok["chat_in"] += int(d.get("prompt_eval_count") or 0)
            tok["chat_out"] += int(d.get("eval_count") or 0)
        elif url.endswith("/api/embed"):
            tok["embed_req"] += 1
            tok["embed_inputs"] += len((k.get("json") or {}).get("input") or [])
            tok["embed_in"] += int(d.get("prompt_eval_count") or 0)
        return r
    llm.httpx.post = post

    timing, stash = {}, {}

    def timed(name, fn):
        def w(s):
            t0 = time.perf_counter()
            out = fn(s)
            timing[name] = timing.get(name, 0) + (time.perf_counter() - t0) * 1000
            if name == "retrieve_internal":
                stash["hits"] = [h["text"] for h in out.get("internal_hits") or []]
            return out
        return w
    orig = {n: getattr(QG, n) for n in ("retrieve", "retrieve_internal", "generate")}
    for n, f in orig.items():
        setattr(QG, n, timed(n, f))
    QG.GENERATION = QG._build_generation()

    modes = {"A": (False, False, False), "B": (True, False, False), "C": (True, True, True)}
    runs = []
    for mode, (mq, idocs, dual) in modes.items():
        config.RAG_MULTI_QUERY, config.INTERNAL_DOCS_RAG, config.RAG_DUAL_JOB = mq, idocs, dual
        for pid in pids:
            p = next(x for x in gen["people"] if x["pid"] == pid)
            app_ = {"id": f"eval-{pid}", "job_id": p["job_id"], "resume_snapshot": p["snapshot"]}
            for k_ in tok:
                tok[k_] = 0
            timing.clear()
            stash.clear()
            t0 = time.perf_counter()
            err = None
            try:
                qs, info = QG.generate_set(app_, use_llm=True)
            except Exception as e:  # noqa: BLE001
                qs, info, err = [], {"log": []}, f"{type(e).__name__}: {e}"
            sec = time.perf_counter() - t0
            leaks = [q["category"] for q in qs if internal_forbid.find_terms(q["text"], terms) or internal_forbid.masked_hits(q["text"])]
            runs.append({"mode": mode, "pid": pid, "role": p["role_code"], "seconds": round(sec, 1), "questions": len(qs), "error": err,
                         "chat_calls": tok["chat"], "embed_requests": tok["embed_req"], "embed_inputs": tok["embed_inputs"],
                         "tokens_chat_in": tok["chat_in"], "tokens_chat_out": tok["chat_out"], "tokens_embed_in": tok["embed_in"],
                         "node_ms": {k: round(v, 1) for k, v in timing.items()},
                         "search_ms": round(timing.get("retrieve", 0) + timing.get("retrieve_internal", 0), 1),
                         "dual_report": info.get("dual_report", {}), "mq_report": info.get("mq_report", {}),
                         "internal_refs": info.get("internal_refs", []), "guard_report": info.get("guard_report", {}),
                         "rule_leaks": leaks, "hits": stash.get("hits", []),
                         "questions_out": [{"category": q["category"], "text": q["text"], "gen_method": q.get("gen_method")} for q in qs],
                         "log": [x for x in info.get("log", []) if x.startswith(("retrieve", "generate", "guard"))]})
            r = runs[-1]
            print(mode, pid, r["seconds"], "s", r["questions"], "문항", r["chat_calls"], "chat", r["embed_requests"], "embed",
                  r["search_ms"], "ms", r["dual_report"], flush=True)
    for n, f in orig.items():
        setattr(QG, n, f)
    QG.GENERATION = QG._build_generation()

    # 질문 품질 판정(생성과 같은 로컬 qwen3:4b, temperature 0 — 자기 평가 편향 가능성은 결과에 적는다)
    judging["on"] = True
    n_j = 0
    for r in runs:
        p = next(x for x in gen["people"] if x["pid"] == r["pid"])
        job = QG.start({"application": {"job_id": p["job_id"], "resume_snapshot": p["snapshot"]}})["job"]
        resume = Q._snapshot_text(p["snapshot"])[:1500]
        for q in r["questions_out"]:
            if q["category"] not in ("resume", "job", "company"):
                continue
            used_doc = "|internal:" in (q.get("gen_method") or "")
            docs = ("\n\n[사내문서 조각]\n" + "\n---\n".join(h[:600] for h in r["hits"])) if used_doc and r["hits"] else ""
            user = (f"[공고] {job.get('company_name')} · {job.get('title') or ''} ({job.get('role_name')})\n주요 업무: {(job.get('duties') or '')[:400]}\n"
                    f"자격 요건: {(job.get('requirements') or '')[:300]}\n\n[지원자 이력서]\n{resume}{docs}\n\n[질문 — {q['category']}]\n{q['text']}")
            try:
                out = llm.chat_json(QJ_SYSTEM, user, QJ_SCHEMA, timeout=120, temperature=0, max_tokens=220, num_ctx=6144)
            except llm.LLMError:
                out = None
            n_j += 1
            q["judge"] = out
            q["used_doc"] = used_doc
        print("judged", r["mode"], r["pid"], flush=True)
    llm.httpx.post = orig_post

    def m(rs, f):
        xs = [f(r) for r in rs]
        xs = [x for x in xs if x is not None]
        return round(st.mean(xs), 3) if xs else None

    summ = {}
    for mode in modes:
        rs = [r for r in runs if r["mode"] == mode]
        qq = [q for r in rs for q in r["questions_out"] if q.get("judge")]
        doc_q = [q for q in qq if q.get("used_doc")]

        def cats(c, qq=qq):
            return [q for q in qq if q["category"] == c]
        summ[mode] = {
            "runs": len(rs), "errors": sum(1 for r in rs if r["error"]), "questions_ok": sum(1 for r in rs if r["questions"] == 8),
            "total_seconds": lat([r["seconds"] * 1000 for r in rs]), "search_ms": lat([r["search_ms"] for r in rs]),
            "retrieve_ms": lat([r["node_ms"].get("retrieve", 0) for r in rs]),
            "retrieve_internal_ms": lat([r["node_ms"].get("retrieve_internal", 0) for r in rs]),
            "generate_ms": lat([r["node_ms"].get("generate", 0) for r in rs]),
            "chat_calls_mean": m(rs, lambda r: r["chat_calls"]), "embed_requests_mean": m(rs, lambda r: r["embed_requests"]),
            "embed_inputs_mean": m(rs, lambda r: r["embed_inputs"]),
            "tokens_chat_in_mean": m(rs, lambda r: r["tokens_chat_in"]), "tokens_chat_out_mean": m(rs, lambda r: r["tokens_chat_out"]),
            "tokens_embed_in_mean": m(rs, lambda r: r["tokens_embed_in"]),
            "dual_modes": {k: sum(1 for r in rs if r["dual_report"].get("mode") == k) for k in ("dual", "redis_only", "chroma_only", "safe")},
            "job_rag_questions": sum(r["dual_report"].get("job_rag", 0) for r in rs),
            "company_rag_questions": sum(r["dual_report"].get("company_rag", 0) for r in rs),
            "guard_blocked": sum((r["guard_report"] or {}).get("blocked", 0) for r in rs),
            "rule_leaks": sum(len(r["rule_leaks"]) for r in rs),
            "judged": len(qq),
            "job_relevance": {c: m(cats(c), lambda q: q["judge"]["job_relevance"]) for c in ("resume", "job", "company")},
            "resume_consistency": {c: m(cats(c), lambda q: q["judge"]["resume_consistency"]) for c in ("resume", "job", "company")},
            "job_relevance_all": m(qq, lambda q: q["judge"]["job_relevance"]),
            "resume_consistency_all": m(qq, lambda q: q["judge"]["resume_consistency"]),
            "fabricated_experience": sum(1 for q in qq if q["judge"]["fabricated_experience"]),
            "doc_questions": len(doc_q),
            "doc_faithfulness": m(doc_q, lambda q: q["judge"]["doc_faithfulness"] or None),
        }
    out = {"meta": {"measured_at": time.strftime("%Y-%m-%d %H:%M:%S"), "pids": pids, "llm_model": config.LLM_MODEL,
                    "embed_model": config.EMBED_MODEL, "judge_model": config.LLM_MODEL, "judge_calls": n_j,
                    "modes": {k: {"RAG_MULTI_QUERY": v[0], "INTERNAL_DOCS_RAG": v[1], "RAG_DUAL_JOB": v[2]} for k, v in modes.items()},
                    "rrf_k": config.RAG_RRF_K, "mq_n": config.RAG_MQ_N, "review": config.QGRAPH_REVIEW},
           "summary": summ, "runs": runs}
    json.dump(out, open(a.out, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(json.dumps(summ, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("retrieval", "fallback", "pipeline", "pipeline3"):
        p = sp.add_parser(name)
        p.add_argument("--gen", required=True)
        p.add_argument("--out", required=True)
        if name in ("pipeline", "pipeline3"):
            p.add_argument("--pids", required=True)
    a = ap.parse_args()
    {"retrieval": run_retrieval, "fallback": run_fallback, "pipeline": run_pipeline, "pipeline3": run_pipeline3}[a.cmd](a)


if __name__ == "__main__":
    main()
