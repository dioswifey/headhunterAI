"""가상 지원자 20명 면접 시뮬레이션 (2026-11-16, 사용자 지시) — 제품 코드는 고치지 않고 그대로 부른다.

모든 지원자는 헤드헌터랩(서울·경기·인천) 공고 9개 중 하나에 지원한 **가상 인물**이다(실제 지원자 데이터 없음).
DB에는 아무것도 쓰지 않는다(eval_rag_questions·eval_reports와 같은 방식 — 공고 SELECT만).

  gen      그래프 1-A GENERATION(질문 생성, 사내문서 RAG 4-B·A15·A16 포함)을 지원자마다 실행 → 8문항
  judge    그래프 1-B 꼬리질문 판단 judge_detail(A3)을 답변 순서대로 실행(전체 4개 상한·질문당 1개는 제품 코드 그대로)
           → 꼬리질문이 나온 이유 / 안 나온 이유를 기록(모델 선평가 값 포함)
  evaluate 그래프 2 노드(evaluate_blocks → compute → summarize → decide)를 메모리에서 실행 → 항목 점수·면접 점수·AI 판정
  ragcheck 앞에서 고른 3명 세트를 eval_rag_questions 형식 파일로 내보냄 → `eval_rag_questions judge`로 질문 품질 판정
  metabench 사내문서 메타데이터(공개 범위·상태·원본 해시)가 줄이는 검색 시간·임베딩 호출 수를 실측
  excel    (호스트) 위 결과 JSON → documents/03_QA_doc/headhunterAI_가상지원자20_면접시뮬레이션.xlsx

    docker exec -w /srv AI-Interview-project-api python -m tools.sim_applicants gen --resumes /tmp/sim/resumes.json --out /tmp/sim/gen.json
    docker exec -w /srv AI-Interview-project-api python -m tools.sim_applicants judge --gen /tmp/sim/gen.json --answers /tmp/sim/answers.json --out /tmp/sim/judged.json
    docker exec -w /srv AI-Interview-project-api python -m tools.sim_applicants evaluate --gen … --answers … --judged … --followup-answers … --out /tmp/sim/eval.json
    python backend/tools/sim_applicants.py excel --dir <결과 폴더> --out documents/03_QA_doc/…xlsx
"""
import argparse
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SPEECH_SYL_PER_SEC = 4.5      # 면접 답변 발화 속도 가정(한국어 음절/초, 쉼 포함). 60초 ≈ 270음절
CAT_KO = {"personality": "인성", "resume": "이력서 기반", "job": "직무 이해도", "company": "기업(사내문서 RAG)", "followup": "꼬리질문"}
LEVEL_KO = {"H": "높음", "M": "중간", "L": "낮음"}


def _load(p, default=None):
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


def _dump(p, obj):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, p)


def syllables(text):
    """발화 길이 추정: 한글 음절 1 + 영문·숫자는 글자 2개당 1음절."""
    t = text or ""
    return len(re.findall(r"[가-힣]", t)) + len(re.findall(r"[A-Za-z0-9]", t)) / 2


def speak_sec(text):
    return round(syllables(text) / SPEECH_SYL_PER_SEC, 1)


# ============================ 1. gen ============================
def cmd_gen(a):
    from app import config, db
    from app.agents import question_graph as G
    from app import questions as Q
    from tools import eval_rag_questions as E
    people = _load(a.resumes)
    out = _load(a.out, {"meta": {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "gen_model": config.LLM_MODEL,
                                 "embed_model": config.EMBED_MODEL, "prompt_version": config.VERSIONS.get("prompt"),
                                 "internal_docs_rag": config.INTERNAL_DOCS_RAG}, "people": []})
    done = {p["pid"] for p in out["people"]}
    jobs = {j["id"]: j for j in db.all_("""SELECT j.*, c.name company_name, r.name role_name FROM jobs j JOIN companies c ON c.id=j.company_id
                                           JOIN job_roles r ON r.code=j.role_code""")}
    order = {c: i for i, (c, _) in enumerate(Q.PLAN)}
    for k, p in enumerate(people):
        if p["pid"] in done or (a.only and p["pid"] not in a.only.split(",")):
            continue
        job = jobs[p["job_id"]]
        random.seed(5000 + k)
        app = {"id": -(k + 1), "job_id": job["id"], "resume_snapshot": p["snapshot"]}
        t = time.time()
        err = None
        try:
            st = G.GENERATION.invoke({"application": app, "use_llm": True}, {"run_name": "sim_applicants", "tags": ["sim"]})
        except Exception as e:  # noqa: BLE001
            st, err = {}, f"{type(e).__name__}: {e}"
        sec = round(time.time() - t, 1)
        finals = st.get("final_questions") or []
        drafts = sorted(st.get("drafts") or [], key=lambda d: order[d["category"]])
        qs = []
        for i, f in enumerate(finals):
            d = drafts[i] if i < len(drafts) else {}
            qs.append({"qno": f"Q{i + 1}", "qid": f"{p['pid']}-Q{i + 1}", "category": f["category"], "eval_items": f.get("eval_items"),
                       "text": f["text"], "orig": d.get("orig") or "", "intent": d.get("intent") or "",
                       "gen_method": f.get("gen_method") or "", "resume_ref": f.get("resume_ref"), "bank_id": f.get("bank_id"),
                       "guard": d.get("guard"), "doc_refs": d.get("doc_refs") or [], "philosophy": d.get("philosophy"),
                       "fallback": E._is_fallback(d) if d else None, "rule_grounded": E._rule_grounded(d, job, p["snapshot"]) if d else None,
                       # reject = 이력서 질문이 템플릿으로 떨어진 사유(2026-11-18 관측용). 지원자 문자열이 섞일 수
                       # 있어 이 측정 산출물에만 남는다 — 제품 경로(DB·로그)에는 들어가지 않는다.
                       "reject": d.get("reject"),
                       "rule_one_sentence": Q.one_sentence(f["text"]), "rule_blocklist": bool(Q.BLOCKLIST.search(f["text"] or "")),
                       "length": len(f["text"] or ""), "retrieval_fallback": E._is_retrieval_fallback(d) if d else None})
        picks = []
        for key, items in (st.get("picks") or {}).items():
            for b in items or []:
                if b:
                    # iq_id = 사내문서 질문(A17) ID. 2026-11-18 추가 — 이게 없으면 참고 문항이 사내문서였는지
                    # Redis였는지 gen.json만으로 가를 수 없다(doc_id는 양쪽 다 있다, M-55 분해용).
                    picks.append({"key": key, "category": "job" if key in ("resume_fallback", "resume_bank") else key,
                                  "source": E._pick_source(b), "doc_id": b.get("doc_id"), "iq_id": b.get("iq_id"),
                                  "sim": b.get("sim"), "text": b.get("text"), "intent": b.get("subcategory") or ""})
        hits = [{k2: h.get(k2) for k2 in ("ref", "doc_id", "sec", "doc_title", "sec_title", "disclosure", "dept", "sim")}
                for h in (st.get("internal_hits") or [])]
        out["people"].append({"pid": p["pid"], "label": p.get("label"), "profile": p.get("profile"), "job_id": job["id"],
                              "company": job["company_name"], "role_code": job["role_code"], "role_name": job.get("role_name"),
                              "job_title": job["title"], "snapshot": p["snapshot"], "seconds": sec, "error": err,
                              "log": st.get("log", []), "keyword_report": st.get("keyword_report", {}),
                              "internal_hits": hits, "internal_refs": st.get("internal_refs", []),
                              "guard_report": st.get("guard_report", {}), "dual_report": st.get("dual_report", {}),
                              # ②(2026-11-23) 백업 층 검사 기록 — 몇 건을 검사해 몇 건을 뒤로 보냈는지
                              "backup_report": st.get("backup_report", {}),
                              "questions": qs, "picks": picks})
        _dump(a.out, out)
        n_int = sum(1 for q in qs if "|internal:" in (q["gen_method"] or ""))
        print(f"{p['pid']} {job['company_name']}/{job['role_code']}: {len(qs)}문항 {sec}s 사내문서근거 {n_int} {err or ''}", flush=True)


# ============================ 2. judge ============================
def _skip_reason(att, q, answer, rec):
    from app import config, evaluation
    from app.agents import question_rules as QR
    if q["parent_id"] is not None:
        return "꼬리질문에는 다시 꼬리질문을 달지 않음(질문당 1개)"
    if att["followups_used"] >= config.MAX_FOLLOWUPS:
        return f"전체 꼬리질문 {config.MAX_FOLLOWUPS}개를 이미 다 씀"
    if evaluation.NO_CONTENT.match(answer) or len(answer.strip()) < 5:
        return "내용 없는 답변(모르겠습니다 등) → 꼬리질문 생략, 1점 처리"
    # 2026-11-23: F5(비답변) 차단은 `question_rules.non_answer`가 **모델 호출 전에** 하는데 이 함수에 분기가
    # 없어서 "판단 없음"으로 찍혔다(P3 측정에서 Q4·Q6·Q8). 제품 동작은 정상이고 라벨만 틀렸던 것이라 분기를 더한다.
    bad, why = QR.non_answer(answer)
    if bad:
        return f"F5 비답변 차단({why}) → 모델을 부르지 않고 꼬리질문 생략"
    if rec.get("error"):
        return f"모델 선평가 실패/시간 초과({rec['error']}) → 생략(INT-06)"
    r = rec.get("result") or {}
    if r:
        trig = []
        if not r.get("addresses_question") or r.get("logic", 5) <= 2 or r.get("relevance", 5) <= 2:
            trig.append("off")
        if r.get("inconsistent_with_resume"):
            trig.append("resume")
        if r.get("generic_only"):
            trig.append("generic")
        if trig:
            return "조건은 걸렸지만 꼬리질문 문장 검사 탈락(빈 문장·150자 초과·차단 목록·힌트 금지)"
        return (f"조건 미충족 — 질문에 답함={r.get('addresses_question')} · 논리 {r.get('logic')} · 관련성 {r.get('relevance')}"
                f" · 이력서와 다름={r.get('inconsistent_with_resume')} · 일반론={r.get('generic_only')}"
                + (f" · 구체성 {r.get('specificity')}" if "specificity" in r else ""))
    return "판단 없음"


def cmd_judge(a):
    from app import config, llm
    from app.agents import question_graph as G
    gen, answers = _load(a.gen), _load(a.answers)
    out = _load(a.out, {"meta": {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "model": config.LLM_MODEL,
                                 "followup_timeout": config.FOLLOWUP_TIMEOUT, "max_followups": config.MAX_FOLLOWUPS}, "people": {}})
    real = llm.chat_json
    rec = {}

    def spy(*args, **kw):
        rec.clear()
        t = time.time()
        try:
            r = real(*args, **kw)
            rec.update({"result": r, "sec": round(time.time() - t, 2)})
            return r
        except Exception as e:
            rec.update({"error": f"{type(e).__name__}", "sec": round(time.time() - t, 2)})
            raise
    llm.chat_json = spy
    try:
        try:   # 모델을 미리 올려 둔다(첫 호출 적재 시간이 FOLLOWUP_TIMEOUT을 먹지 않게 — 실제 면접에서는 질문 생성이 먼저 올려 둠)
            real("한 단어로 답하라.", "준비", {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]}, timeout=120)
        except Exception:
            pass
        for p in gen["people"]:
            if p["pid"] in out["people"] or (a.only and p["pid"] not in a.only.split(",")):
                continue
            ans = answers.get(p["pid"]) or {}
            att = {"id": -1, "followups_used": 0}
            rows = []
            for q in p["questions"]:
                text = (ans.get("answers") or {}).get(q["qno"], "")
                qd = {"parent_id": None, "text": q["text"], "resume_ref": q.get("resume_ref"), "gen_method": q.get("gen_method"),
                      "category": q["category"]}
                rec.clear()
                d = G.judge_detail(att, qd, text)
                fu = d["followup"]
                row = {"qno": q["qno"], "followups_used_before": att["followups_used"], "keyword_hits": d["keyword_hits"],
                       "specificity": d["specificity"], "model": rec.get("result"), "model_error": rec.get("error"),
                       "model_sec": rec.get("sec")}
                if fu:
                    att["followups_used"] += 1
                    row.update({"followup": fu[0], "reason": fu[1]})
                else:
                    row.update({"followup": None, "skip_reason": _skip_reason(att, qd, text, rec)})
                rows.append(row)
            out["people"][p["pid"]] = rows
            _dump(a.out, out)
            print(p["pid"], "꼬리질문", sum(1 for r in rows if r.get("followup")), flush=True)
    finally:
        llm.chat_json = real


# ============================ 3. evaluate ============================
def cmd_evaluate(a):
    from app import db, evaluation as ev
    from app.agents import evaluation_graph as eg
    gen, answers, judged = _load(a.gen), _load(a.answers), _load(a.judged)
    fans = _load(a.followup_answers, {}) or {}
    out = _load(a.out, {"meta": {"started": time.strftime("%Y-%m-%d %H:%M:%S")}, "people": {}})
    for p in gen["people"]:
        if p["pid"] in out["people"] or (a.only and p["pid"] not in a.only.split(",")):
            continue
        crit = dict(db.one("SELECT * FROM eval_criteria WHERE job_id=%s", p["job_id"]) or {})
        crit = {k: v for k, v in crit.items() if k.startswith("w_") or k in ("pass_score", "coding_gate_mode", "decision_mode")}
        crit.update({"coding_enabled": False, "decision_mode": "auto"})      # 면접만 채점(코딩테스트 제외)
        jrows = {r["qno"]: r for r in judged["people"].get(p["pid"], [])}
        ans = (answers.get(p["pid"]) or {}).get("answers") or {}
        fa = fans.get(p["pid"]) or {}
        blocks = []
        for i, q in enumerate(p["questions"]):
            qid = 1000 * int(p["pid"][1:]) + i + 1
            text = ans.get(q["qno"], "")
            base = {"id": qid, "seq": i + 1, "parent_id": None, "text": q["text"], "category": q["category"], "eval_items": q["eval_items"],
                    "answer_text": text, "answer_status": "answered" if text.strip() else "no_answer", "resume_ref": q.get("resume_ref")}
            fus = []
            jr = jrows.get(q["qno"]) or {}
            if jr.get("followup"):
                ft = fa.get(q["qno"], "")
                fus.append({"id": qid + 500, "seq": i + 1, "parent_id": qid, "text": jr["followup"], "category": "followup",
                            "eval_items": q["eval_items"], "answer_text": ft, "answer_status": "answered" if ft.strip() else "no_answer",
                            "resume_ref": None})
            blocks.append({"q": base, "followups": fus})
        t = time.time()
        s = {"attempt_id": -1, "att": {"resume_snapshot": p["snapshot"]}, "criteria": crit, "blocks": blocks}
        try:
            s.update(eg.evaluate_blocks(s))
            s.update(eg.compute(s))
            s.update(eg.summarize(s))
            s.update(eg.decide(s))
            rec = {"ok": True, "criteria": crit, "scores_by_q": {str(k): v for k, v in s["scores_by_q"].items()},
                   "item_scores": s["item_scores"], "interview": s["interview"], "total": s["total"], "result": s["result"],
                   "summary": s["summary"], "sec": round(time.time() - t, 1)}
        except Exception as e:  # noqa: BLE001
            rec = {"ok": False, "error": f"{type(e).__name__}: {e}", "sec": round(time.time() - t, 1)}
        out["people"][p["pid"]] = rec
        _dump(a.out, out)
        print(p["pid"], rec.get("interview"), rec.get("result"), rec.get("error") or "", flush=True)


# ============================ 4. ragcheck (3명 질문 품질) ============================
def cmd_ragcheck(a):
    gen = _load(a.gen)
    pick = a.pids.split(",")
    sets = []
    for p in gen["people"]:
        if p["pid"] not in pick:
            continue
        sets.append({"set_id": p["pid"], "job_id": p["job_id"], "company": p["company"], "role_code": p["role_code"],
                     "job_title": p["job_title"], "resume": p["pid"], "snapshot": p["snapshot"], "seconds": p["seconds"], "error": p["error"],
                     "log": p["log"], "keyword_report": p["keyword_report"], "resume_fallback": "", "resume_bank_slots": 0,
                     "resume_bank_filled": 0, "questions": p["questions"], "picks": p["picks"]})
    _dump(a.out, {"meta": gen["meta"], "sets": sets})
    print("세트", len(sets), "→", a.out)


# ============================ 5. metabench ============================
def cmd_metabench(a):
    from app import config, internal_store, llm
    from app.agents import internal_search
    qs = ["고객사 온보딩 일정이 밀렸을 때 어떻게 조율하나요", "RAG 답변 품질을 어떻게 평가하나요", "장애 회고에서 재발 방지 대책",
          "영업 파이프라인 단계별 전환율", "신규 채용 직무의 역할 정의", "제안서 가격 산정 기준", "데이터 수집 플랫폼 설계",
          "물류 자동화 프로젝트 성과 측정", "상담 품질 지표 정의", "제조 비전 검사 불량 기준"]
    vecs = llm.embed(qs)
    pub, res = internal_store.PUBLIC, getattr(internal_store, "RESTRICTED", None)
    where = {"$and": [{"disclosure": {"$eq": "public"}}, {"status": {"$in": list(internal_search.ALLOWED_STATUS)}}]}
    names = [c.name for c in internal_store.client().list_collections()]
    counts = {n: internal_store.count(n) for n in names}

    def timed(fn, rounds=5):
        ts = []
        for _ in range(rounds):
            for v in vecs:
                t = time.perf_counter()
                fn(v)
                ts.append((time.perf_counter() - t) * 1000)
        ts.sort()
        return {"mean_ms": round(sum(ts) / len(ts), 2), "p50_ms": round(ts[len(ts) // 2], 2), "p95_ms": round(ts[int(len(ts) * .95)], 2), "n": len(ts)}

    k = internal_search.K
    with_meta = timed(lambda v: internal_store.collection(pub).query(query_embeddings=[v], n_results=k, where=where))
    no_filter_pub = timed(lambda v: internal_store.collection(pub).query(query_embeddings=[v], n_results=k))
    both = None
    if res and res in names:
        def q2(v):
            internal_store.collection(pub).query(query_embeddings=[v], n_results=k)
            internal_store.collection(res).query(query_embeddings=[v], n_results=k)
        both = timed(q2)
    # 검색 후 버려야 하는 조각(메타데이터 없이 전부 한 곳에 있을 때): 비공개·open 조각이 상위 k에 섞인 비율
    leak = 0
    total = 0
    if res and res in names:
        for v in vecs:
            r1 = internal_store.collection(pub).query(query_embeddings=[v], n_results=k, include=["distances", "metadatas"])
            r2 = internal_store.collection(res).query(query_embeddings=[v], n_results=k, include=["distances", "metadatas"])
            merged = sorted([(d, "pub") for d in r1["distances"][0]] + [(d, "res") for d in r2["distances"][0]])[:k]
            leak += sum(1 for _, s in merged if s == "res")
            total += k
    # 원본 해시 비교: 바뀐 문서만 다시 임베딩
    hashes = internal_store.doc_hashes(pub)
    out = {"measured": time.strftime("%Y-%m-%d %H:%M:%S"), "collections": counts, "k": k,
           "where": "disclosure=public AND status in " + ",".join(internal_search.ALLOWED_STATUS),
           "search_with_metadata_filter": with_meta, "search_public_no_filter": no_filter_pub,
           "search_all_chunks_public_plus_restricted": both,
           "restricted_share_in_topk_without_split": round(leak / total, 3) if total else None,
           "docs_with_hash": len(hashes), "embed_model": config.EMBED_MODEL}
    _dump(a.out, out)
    print(json.dumps(out, ensure_ascii=False, indent=1))


# ============================ 6. excel (호스트) ============================
def cmd_excel(a):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    d = a.dir
    gen, answers, judged = _load(os.path.join(d, "gen.json")), _load(os.path.join(d, "answers.json")), _load(os.path.join(d, "judged.json"))
    fans = _load(os.path.join(d, "followup_answers.json"), {}) or {}
    ev = _load(os.path.join(d, "eval.json"), {"people": {}})
    rq = _load(os.path.join(d, "ragcheck_judged.json"), {})
    bench = _load(os.path.join(d, "metabench.json"), {})
    notes = _load(os.path.join(d, "notes.json"), {})
    wb = Workbook()
    HEAD = PatternFill("solid", fgColor="D9D9D9")
    thin = Side(style="thin", color="999999")
    BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
    WRAP = Alignment(wrap_text=True, vertical="top")

    def sheet(title, header, rows, widths, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill, c.alignment, c.border = Font(bold=True), HEAD, Alignment(wrap_text=True, vertical="center"), BOX
        for r in rows:
            ws.append(r)
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment, c.border = WRAP, BOX
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        return ws

    people = gen["people"]
    J = judged["people"]
    E = ev.get("people", {})

    # 0 개요
    ov = notes.get("overview", [])
    ws = sheet("0_개요", ["항목", "내용"], ov, [26, 120], first=True)

    # 1 지원자
    rows = []
    for p in people:
        s = p["snapshot"]
        car = "\n".join(f"{c.get('company')} · {c.get('role')} · {c.get('period')} — {c.get('detail')}" for c in s.get("career", []))
        prj = "\n".join(f"{c.get('name')} · {c.get('role')} · {c.get('period')} — {c.get('detail')}" for c in s.get("projects", []))
        edu = "\n".join(f"{e.get('school')} {e.get('major')} {e.get('degree', '')} {e.get('period', '')}" for e in s.get("education", []))
        r = E.get(p["pid"], {})
        nfu = sum(1 for x in J.get(p["pid"], []) if x.get("followup"))
        rows.append([p["pid"], s["basic"]["name"], p.get("label"), f"{p['company']} / {p['job_title']}", p["role_code"],
                     LEVEL_KO.get(p.get("profile"), ""), edu, car or "(경력 없음)", prj, ", ".join(s.get("skills", [])),
                     ", ".join(c["name"] for c in s.get("certificates", [])) or "(없음)", nfu, r.get("interview"),
                     {"pass": "합격", "fail": "불합격"}.get(r.get("result"), r.get("error", ""))])
    sheet("1_지원자_이력서", ["ID", "이름(가상)", "요약", "지원 공고(헤드헌터랩)", "직무", "답변 수준(설계)", "학력", "경력", "프로젝트",
                         "보유 기술", "자격증", "꼬리질문 수", "AI 면접 점수(100)", "AI 판정"], rows,
          [6, 14, 18, 34, 9, 10, 26, 48, 48, 26, 14, 8, 10, 10])

    # 2 질문·답변·꼬리질문 (지원자별 전부)
    rows = []
    for p in people:
        jr = {x["qno"]: x for x in J.get(p["pid"], [])}
        A = (answers.get(p["pid"]) or {})
        lv = A.get("levels") or {}
        sc = E.get(p["pid"], {}).get("scores_by_q", {})
        for i, q in enumerate(p["questions"]):
            ans = (A.get("answers") or {}).get(q["qno"], "")
            j = jr.get(q["qno"], {})
            qs = sc.get(str(1000 * int(p["pid"][1:]) + i + 1), {})
            score_txt = " · ".join(f"{k} {v['score']}" for k, v in qs.items())
            src = q["gen_method"]
            if "|internal:" in src:
                src = src.replace("|internal:", "\n사내문서 근거: ")
            fq, fa = j.get("followup"), (fans.get(p["pid"]) or {}).get(q["qno"], "")
            m = j.get("model") or {}
            judge_txt = (f"질문에 답함={m.get('addresses_question')} · 논리 {m.get('logic')} · 관련성 {m.get('relevance')} · "
                         f"이력서와 다름={m.get('inconsistent_with_resume')} · 일반론={m.get('generic_only')}"
                         + (f" · 구체성 {m.get('specificity')}" if m.get('specificity') is not None else "")
                         + (f" · 기대키워드 적중 {j.get('keyword_hits')}" if j.get('keyword_hits') is not None else "")) if m else (j.get("model_error") or "(판단 생략 — 모델 호출 전 규칙에서 종료)")
            rows.append([p["pid"], q["qno"], CAT_KO.get(q["category"], q["category"]), q["text"], src,
                         (q.get("resume_ref") or {}).get("quote", "") if isinstance(q.get("resume_ref"), dict) else "",
                         LEVEL_KO.get(lv.get(q["qno"]), ""), ans, len(ans), speak_sec(ans), judge_txt,
                         "예" if fq else "아니오", j.get("reason") or j.get("skip_reason", ""), fq or "", fa, speak_sec(fa) if fa else "",
                         score_txt])
    sheet("2_질문_답변_꼬리질문", ["ID", "번호", "종류", "맞춤 질문(그래프 1-A 생성)", "출처(gen_method)", "근거 이력서 문구",
                              "답변 수준(설계)", "지원자 답변(텍스트)", "글자 수", "예상 발화(초)", "꼬리질문 판단 A3 선평가값",
                              "꼬리질문", "나온 이유 / 안 나온 이유", "꼬리질문 문장", "꼬리질문 답변", "꼬리 답변 발화(초)", "항목 점수(0~5)"], rows,
          [6, 5, 10, 48, 24, 28, 8, 70, 7, 8, 40, 7, 40, 40, 60, 8, 34])

    # 3 꼬리질문 요약
    rows = []
    for p in people:
        js = J.get(p["pid"], [])
        fus = [x for x in js if x.get("followup")]
        if fus:
            why = " / ".join(f"{x['qno']}: {x['reason']}" for x in fus)
        else:
            skips = [f"{x['qno']}: {x.get('skip_reason')}" for x in js]
            why = "꼬리질문 0개 — " + " / ".join(skips)
        rows.append([p["pid"], LEVEL_KO.get(p.get("profile"), ""), len(fus), "\n".join(f"[{x['qno']}] {x['followup']}" for x in fus), why])
    sheet("3_꼬리질문_요약", ["ID", "답변 수준(설계)", "꼬리질문 수(최대 4)", "나온 꼬리질문", "판단 근거(질문별)"], rows, [6, 10, 10, 70, 110])

    # 4 사내문서 RAG
    rows = []
    for p in people:
        cq = next((q for q in p["questions"] if q["category"] == "company"), None)
        hits = "\n".join(f"{h['ref']} · {h['doc_title']} > {h['sec_title']} (유사도 {h['sim']})" for h in p.get("internal_hits", []))
        g = p.get("guard_report") or {}
        rows.append([p["pid"], p["company"], cq["text"] if cq else "", cq.get("orig", "") if cq else "", hits or "(근거 0조각)",
                     ", ".join(cq.get("doc_refs") or []) or "(근거 미사용)" if cq else "", (cq.get("guard") or "(검사 없음)") if cq else "",
                     f"검사 {g.get('checked', 0)} · 차단 {g.get('blocked', 0)} · 사유 {g.get('reasons', {})} · 모델 {g.get('model')}",
                     next((l for l in p.get("log", []) if l.startswith("retrieve_internal")), "")])
    sheet("4_사내문서RAG_기업질문", ["ID", "지원 기업", "기업 질문(최종)", "은행 원문", "검색된 사내문서 조각(public)", "질문이 쓴 근거",
                               "누출 관문 A16", "guard_report", "retrieve_internal 로그"], rows, [6, 14, 50, 36, 60, 24, 10, 36, 50])

    # 5 질문 품질 3명
    rows = []
    qmap = {q["qid"]: q for p in people for q in p["questions"]}
    keys = ("clarity", "job_relevance", "answerability", "one_sentence", "unbiased")
    for qid, v in (rq.get("quality") or {}).items():
        q = qmap.get(qid, {})
        sc = [v.get(k) for k in keys]
        mean = round(sum(sc) / len(sc), 2) if all(isinstance(x, (int, float)) for x in sc) else None
        rows.append([qid, CAT_KO.get(q.get("category"), q.get("category")), q.get("text", ""), q.get("gen_method", ""), *sc,
                     "예" if v.get("grounded") else "아니오", ", ".join(v.get("ungrounded_facts") or []), mean, v.get("reason", "")])
    sheet("5_질문품질_3명", ["질문 ID", "종류", "질문", "출처", "명확성", "직무 관련성", "답변 가능성", "한 문장", "편향 없음", "근거 있음",
                          "근거 없는 표현", "루브릭 평균(5)", "판정 이유"], rows, [11, 10, 50, 22, 7, 7, 7, 7, 7, 7, 24, 9, 60])

    # 6 채점 결과
    rows = []
    for p in people:
        r = E.get(p["pid"], {})
        it = r.get("item_scores", {})
        sm = r.get("summary", {})
        rows.append([p["pid"], LEVEL_KO.get(p.get("profile"), ""), *[it.get(k) for k in ("expertise", "logic", "relevance", "delivery", "fit")],
                     r.get("interview"), (r.get("criteria") or {}).get("pass_score"), {"pass": "합격", "fail": "불합격"}.get(r.get("result"), r.get("error", "")),
                     "\n".join(sm.get("strengths", [])), "\n".join(sm.get("improvements", [])), sm.get("summary", "")])
    sheet("6_채점_합불", ["ID", "답변 수준(설계)", "직무 전문성", "논리성", "관련성·구체성", "전달력", "조직 적합성", "AI 면접 점수(100)",
                       "합격 기준", "AI 판정", "강점", "개선점", "요약"], rows, [6, 9, 8, 8, 8, 8, 8, 10, 8, 9, 40, 40, 60])

    # 7 메타데이터·벡터DB
    sheet("7_메타데이터_벡터DB", ["항목", "내용"], notes.get("metadata", []) + [["실측 원본(JSON)", json.dumps(bench, ensure_ascii=False, indent=1)]], [30, 130])
    sheet("8_발견결함", ["ID", "영역", "위치", "증상", "후보 조치"], notes.get("defects", []), [6, 18, 26, 80, 50])
    TYPE_KO = {"1": "① 이력서·경험 전제 환각", "2": "② 오타·고유명사", "3": "③ 접두어 찌꺼기", "4": "④ 직무 무관",
               "5": "⑤ 섞임·한 문장 아님", "6": "⑥ 정답 힌트", "7": "⑦ 기타"}
    issues = _load(os.path.join(d, "question_issues.json"), []) or []
    rows = []
    for it in issues:
        t = str(it.get("type", ""))
        key = next((k for k in TYPE_KO if t.startswith(k) or t.startswith(TYPE_KO[k][:1])), "")
        rows.append([it.get("pid"), it.get("qno"), CAT_KO.get(it.get("category"), it.get("category")), TYPE_KO.get(key, t),
                     it.get("gen_method", ""), it.get("text", ""), it.get("note", "")])
    sheet("9_질문결함_전수", ["ID", "번호", "종류", "결함 유형", "출처", "질문", "설명"], rows, [6, 8, 10, 22, 22, 60, 60])
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    wb.save(a.out)
    print("저장", a.out)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("gen"); g.add_argument("--resumes", required=True); g.add_argument("--out", required=True); g.add_argument("--only", default="")
    j = sp.add_parser("judge"); j.add_argument("--gen", required=True); j.add_argument("--answers", required=True); j.add_argument("--out", required=True); j.add_argument("--only", default="")
    e = sp.add_parser("evaluate"); e.add_argument("--gen", required=True); e.add_argument("--answers", required=True); e.add_argument("--judged", required=True)
    e.add_argument("--followup-answers", default=""); e.add_argument("--out", required=True); e.add_argument("--only", default="")
    r = sp.add_parser("ragcheck"); r.add_argument("--gen", required=True); r.add_argument("--pids", required=True); r.add_argument("--out", required=True)
    m = sp.add_parser("metabench"); m.add_argument("--out", required=True)
    x = sp.add_parser("excel"); x.add_argument("--dir", required=True); x.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd in ("gen", "evaluate"):
        from app import db
        db.pool.open(wait=True)            # CLI 실행: 앱 시작 때 여는 연결 풀을 직접 연다(읽기만 한다)
    {"gen": cmd_gen, "judge": cmd_judge, "evaluate": cmd_evaluate, "ragcheck": cmd_ragcheck, "metabench": cmd_metabench, "excel": cmd_excel}[a.cmd](a)


if __name__ == "__main__":
    main()
