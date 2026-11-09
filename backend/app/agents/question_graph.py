"""그래프 1 — 질문 생성 + 면접 루프 (ADLC v0.5 6.1 · 6.1-B, LangGraph).

A. 질문 생성 그래프 GENERATION (환경 점검 중 questions.pregenerate가 부름, 결과 → applications.pregen_questions)
   start → extract_keywords → evaluate_trust(A5, ⟲1) → retrieve → generate(A2) → evaluate_philosophy(A6, ⟲2)
         → evaluate_answerability(A7) → finalize → END
B. 면접 한 걸음 그래프 INTERVIEW_STEP (답변이 들어올 때마다 routes_applicant._save_answer가 부름)
   record_answer → judge_followup(A3) ─(꼬리질문)→ make_followup → next_question → END
                                      └(없음)──────────────────→ next_question → END
   답변은 HTTP 요청마다 따로 오므로 루프의 '대기'(ask_question)는 화면이 맡고, 그래프는 답변 1개마다 한 번 돈다.
   State는 매번 DB(attempt_questions·attempts)에서 다시 만든다 → 체크포인터를 쓰지 않는다(ADLC 6.1-B 승인 필요 항목 회피).

규칙(ADLC 0~2장): 모델 호출은 llm.chat_json만. 점수·합불·응시 상태 전이는 코드. 어떤 노드가 실패해도 규칙·템플릿 경로로 끝까지 간다.
꼬리질문 판단(judge_followup)은 ADLC/followup에서 새로 설계하는 꼬리질문 에이전트로 바꿀 자리다(노드 계약: judge(att, q, answer) → (질문, 사유) | None).
"""
import random
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .. import config, db, llm, prompts, questions as Q

REVIEW = config.QGRAPH_REVIEW          # 0이면 A5·A6 모델 검수를 건너뛴다(규칙 검사는 그대로)
MIN_KEYWORDS = 2
RESUME_BANK_MIN_SIM = 0.60            # 이력서 항목 ↔ 은행 참고 문항 유사도 하한(2026-11-09 측정: 관련 0.65~0.75 · 무관 0.53~0.56)
PERSONAL = re.compile(Q.BLOCKLIST.pattern[:-1] + r"|복무|군필|전역|주소|생년)")   # 인적 정보 키워드는 모델이 남겨도 코드가 뺀다


# ======================= A. 질문 생성 =======================
class GenState(TypedDict, total=False):
    application: dict
    use_llm: bool
    job: dict
    snapshot: dict
    candidates: list            # 이력서 질문 후보(경력·프로젝트·학력 항목)
    keywords: list              # [{term, section, source}]
    keyword_report: dict        # A5 결과 {quality, quantity_ok, removed, added, rounds, passed}
    resume_fallback: str        # 비어 있지 않으면 이력서 질문 2개 → 직무 질문(RSQ-04) 사유
    picks: dict                 # category → 질문 은행 행 목록
    drafts: list                # 문항 초안 (finalize 전)
    final_questions: list
    log: list                   # 노드별 처리 기록(문장 원문 없이 종류·개수만)


def _snapshot_squash(snapshot):
    return re.sub(r"\s+", "", Q._snapshot_text(snapshot))


def start(s: GenState) -> GenState:
    app = s["application"]
    job = db.one("""SELECT j.*, c.name company_name, r.name role_name FROM jobs j JOIN companies c ON c.id=j.company_id
                    JOIN job_roles r ON r.code=j.role_code WHERE j.id=%s""", app["job_id"])
    return {"job": job, "snapshot": app["resume_snapshot"] or {}, "log": []}


def extract_keywords(s: GenState) -> GenState:
    """코드: 확인 완료 이력서에서 질문 후보 항목과 키워드(이름·역할·기술)·근거 원문을 뽑는다."""
    cands = Q.resume_candidates(s["snapshot"])
    kws, seen = [], set()
    for c in cands:
        it = c["item"]
        for term in [c["label"], it.get("role", "")] + list(it.get("tech", []) or []):
            t = (term or "").strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                kws.append({"term": t, "section": c["section"], "source": (it.get("detail") or it.get("source") or "")[:160]})
    log = s["log"] + [f"extract_keywords: 후보 {len(cands)} · 키워드 {len(kws)}"]
    out = {"candidates": cands, "keywords": kws, "log": log, "resume_fallback": ""}
    if not cands:
        out["resume_fallback"] = "이력서에 경력·프로젝트·학력 내용이 없어 직무 질문으로 대체"
    return out


TRUST_SCHEMA = {"type": "object", "properties": {
    "quality": {"type": "integer", "minimum": 1, "maximum": 5}, "quantity_ok": {"type": "boolean"},
    "removed": {"type": "array", "items": {"type": "object", "properties": {"term": {"type": "string"}, "reason": {"type": "string"}},
                                           "required": ["term", "reason"]}},
    "added": {"type": "array", "items": {"type": "object", "properties": {"term": {"type": "string"}, "source": {"type": "string"}},
                                         "required": ["term", "source"]}},
    "keywords": {"type": "array", "items": {"type": "string"}}},
    "required": ["quality", "quantity_ok", "removed", "added", "keywords"]}


def evaluate_trust(s: GenState) -> GenState:
    """A5 키워드 신뢰도(prompts/evaluate/keyword_trust.md). 통과 = 최종 키워드가 모두 원문에 있음 · 양 충분.
    1회 재검수. 끝내 키워드가 부족하면 이력서 질문을 직무 질문으로 대체(RSQ-04). 품질 점수만 낮으면 기록만 남긴다."""
    if s["resume_fallback"] or not (s["use_llm"] and REVIEW):
        return {"keyword_report": {"skipped": True}}
    squash = _snapshot_squash(s["snapshot"])
    system = prompts.load("evaluate/keyword_trust", min_keywords=MIN_KEYWORDS)
    body = "키워드: " + ", ".join(k["term"] for k in s["keywords"]) + "\n\n이력서 원문:\n" + Q._snapshot_text(s["snapshot"])[:2500]
    report, problem = {}, ""
    for rnd in range(2):
        try:
            r = llm.chat_json(system, body + (f"\n\n이전 결과의 문제: {problem}" if problem else ""), TRUST_SCHEMA, timeout=90, temperature=0)
        except llm.LLMError:
            report = {"error": "llm"}
            break
        final = [k for k in r["keywords"] if k and re.sub(r"\s+", "", k.lower()) in squash and not PERSONAL.search(k)]
        ungrounded = [k for k in r["keywords"] if k and k not in final]
        report = {"quality": r["quality"], "quantity_ok": r["quantity_ok"] and len(final) >= MIN_KEYWORDS,
                  "removed": r["removed"][:20], "added": [a for a in r["added"] if re.sub(r"\s+", "", a["term"].lower()) in squash][:20],
                  "keywords": final, "rounds": rnd + 1}
        if report["quantity_ok"] and not ungrounded:
            break
        problem = ("원문에 없는 키워드: " + ", ".join(ungrounded[:5])) if ungrounded else f"근거 키워드가 {MIN_KEYWORDS}개 미만"
    out = {"keyword_report": report, "log": s["log"] + [f"evaluate_trust: {report.get('quality', '-')}점 · {report.get('rounds', 0)}회"]}
    if report.get("error"):
        return out                                        # 모델 실패 → 검수 없이 기존 후보로 진행
    removed = {re.sub(r"\s+", "", x["term"].lower()) for x in report.get("removed", [])}
    cands = [c for c in s["candidates"] if re.sub(r"\s+", "", c["label"].lower()) not in removed] or s["candidates"]
    out["candidates"] = cands
    if not report.get("quantity_ok"):
        out["resume_fallback"] = "이력서에서 질문 근거가 될 키워드가 부족해 직무 질문으로 대체(A5 검수)"
    return out


def _group(b):
    return b.get("dup_group") or (f"id{b['id']}" if b.get("id") else b.get("doc_id"))


def _redis_query_vector(s):
    """검색어 = 직무 이름·주요 업무·자격 요건 + A5를 통과한 이력서 키워드(없으면 추출 키워드). 임베딩 1번."""
    from . import question_chain as QC
    kws = (s.get("keyword_report") or {}).get("keywords") or [k["term"] for k in s.get("keywords", [])]
    query = QC._query({"job": s["job"], "resume_keywords": kws[:12]})
    return llm.embed([QC.QUERY_INSTRUCT + query])[0]


def _pick_redis(vector, role_code, cat, n, used):
    """Redis 질문 은행(번역 에이전트를 거친 한·영 문항)에서 가까운 순으로 후보 n×2개 → 그중 n개를 무작위로.
    직무 질문은 지원 직무 문항만, 인성·기업 질문은 공통 + 지원 직무. 모자라면 None(→ DB 은행)."""
    from .. import vectorstore
    roles = [role_code] if cat == "job" else ["common", role_code]
    hits = vectorstore.knn(vector, k=max(16, n * 6), role_codes=roles, categories=[cat], translated_only=True)
    pool, seen = [], set(used)
    for h in hits:
        b = {"id": None, "doc_id": h["doc_id"], "text": (h.get("question_ko") or h.get("question") or "").strip(),
             "subcategory": h.get("intent_ko") or h.get("competency") or "", "dup_group": h.get("dup_group") or "",
             "competency": h.get("competency") or "", "sim": h.get("sim")}
        g = b["dup_group"] or b["competency"] or b["doc_id"]
        if not b["text"] or g in seen or Q.BLOCKLIST.search(b["text"]):
            continue
        seen.add(g)
        b["dup_group"] = g
        pool.append(b)
        if len(pool) >= n * 2:
            break
    if len(pool) < n:
        return None
    keep = set(random.sample(range(len(pool)), n))
    return [b for i, b in enumerate(pool) if i in keep]


def retrieve(s: GenState) -> GenState:
    """질문 은행에서 카테고리별 문항(QST-01). 이력서 대체 시 직무 문항을 더 뽑는다.
    1순위 = Redis 질문 은행(questionbank/ 원본 → 번역 에이전트 → Redis, 이력서 키워드·공고로 벡터 검색).
    Redis·임베딩 실패, 모델 미사용, 해당 카테고리 문항 부족이면 그 카테고리만 DB 질문 은행(적게 쓴 순·dup_group 중복 제거)."""
    used, picks, src = set(), {}, {}
    need = dict(Q.PLAN)
    vector, role = None, s["job"]["role_code"]
    if s["use_llm"]:
        try:
            vector = _redis_query_vector(s)
        except Exception as e:                      # Redis·임베딩 모델이 없어도 면접은 진행
            src["error"] = type(e).__name__
    order = (["resume_fallback"] if s["resume_fallback"] else []) + ["personality", "job", "company"]
    for key in order:
        cat, n = ("job", Q.RESUME_N) if key == "resume_fallback" else (key, need[key])
        got = None
        if vector is not None:
            try:
                got = _pick_redis(vector, role, cat, n, used)
            except Exception as e:
                src["error"] = type(e).__name__
        src[key] = "redis" if got else "db"
        if not got:   # 인성 대체 = character_assessment md(없으면 DB), 나머지 = DB 은행
            got = Q.pick_character(n, used, s["snapshot"]) if cat == "personality" else Q.pick_bank(cat, n, used)
            if cat == "personality" and any(b.get("char_id") for b in got):
                src[key] = "md"
        if cat == "personality":
            got = [_enrich_character(b) for b in got]
        picks[key] = got
        used |= {_group(b) for b in got}
    if not s["resume_fallback"]:                    # 이력서 질문 3개마다 질문 은행 참고 문항(RAG)
        picks["resume_bank"] = _resume_bank_refs(s, role, used) if s["use_llm"] else []
        src["resume_bank"] = "redis" if picks["resume_bank"] and any(b and b.get("doc_id") for b in picks["resume_bank"]) else "-"
    return {"picks": picks, "log": s["log"] + ["retrieve: " + " · ".join(f"{k} {len(v)}({src.get(k)})" for k, v in picks.items())
                                               + (f" · redis 오류 {src['error']}" if "error" in src else "")]}


def _enrich_character(b):
    """Redis에서 온 인성 문항(common:CA-xxx)에 md 원본의 맞춤 범위·바꾸면 안 되는 것·의도를 붙인다."""
    if b.get("char_id"):
        return b
    cid = (b.get("doc_id") or "").split(":", 1)[-1]
    it = {x["char_id"]: x for x in Q.load_character_bank()}.get(cid)
    return {**b, **{k: it[k] for k in ("char_id", "competency", "intent", "scope", "keep", "signals", "probes")}} if it else b


def _resume_bank_refs(s, role, used):
    """이력서 질문 짝(항목·관점)마다 그 항목 내용으로 Redis 직무 문항을 검색해 가장 가까운 1개(이미 쓴 묶음 제외).
    실패·없음이면 None(→ 이력서만으로 생성)."""
    from .. import vectorstore
    pairs, refs, seen = Q.resume_pairs(s["candidates"]), [], set(used)
    for cand, _angle in pairs:
        it = cand["item"]
        text = " ".join([cand["label"], it.get("role", ""), it.get("detail", ""), " ".join(it.get("tech", []) or [])])[:500]
        ref = None
        try:
            from . import question_chain as QC
            vec = llm.embed([QC.QUERY_INSTRUCT + text])[0]
            for h in vectorstore.knn(vec, k=8, role_codes=[role], categories=["job"], translated_only=True):
                q = (h.get("question_ko") or h.get("question") or "").strip()
                g = h.get("dup_group") or h.get("competency") or h["doc_id"]
                if float(h.get("sim") or 0) < RESUME_BANK_MIN_SIM:
                    break                        # 가장 가까운 것도 관련이 약하면 참고 문항 없이(이력서만으로) 생성
                if q and g not in seen and not Q.BLOCKLIST.search(q):
                    seen.add(g)
                    ref = {"id": None, "doc_id": h["doc_id"], "text": q, "subcategory": h.get("intent_ko") or h.get("competency") or ""}
                    break
        except Exception:
            ref = None
        refs.append(ref)
    return refs


def _bank_fields(b):
    """은행 행 → 문항 출처 필드. DB 은행은 bank_id, Redis 은행은 gen_method에 문서 ID를 남긴다(bank_id는 DB 행 전용 FK)."""
    if b.get("doc_id"):
        return {"bank_id": None, "gen_method": "redis:" + b["doc_id"]}
    if b.get("char_id"):
        return {"bank_id": None, "gen_method": "character:" + b["char_id"]}
    return {"bank_id": b["id"], "gen_method": "db"}


def generate(s: GenState) -> GenState:
    """A2 출제 (2026-11-09 구성 8개):
    인성 2 = 인성 은행(RAG) → 인성 다듬기 에이전트 / 이력서 3 = 이력서 항목 + 은행 참고 문항(RAG) → 합치는 생성 에이전트
    직무 2 = 은행(RAG) + 공고 + 이력서 → 섞어 다시 쓰기 / 기업 1 = 은행(RAG) → 회사 맞춤."""
    drafts, use_llm = [], s["use_llm"]
    role_name = s["job"].get("role_name") or ""
    for b in s["picks"].get("personality", []):
        text = Q.contextualize_character(b, s["job"], s["snapshot"]) if use_llm else b["text"]
        drafts.append({"category": "personality", "eval_items": Q.EVAL_ITEMS["personality"], "text": text, "orig": b["text"],
                       **_bank_fields(b), "intent": b.get("intent") or b.get("subcategory") or ""})
    if s["resume_fallback"]:
        for b in s["picks"].get("resume_fallback", []):
            ref = {"fallback_reason": s["resume_fallback"], **({"bank_doc": b["doc_id"]} if b.get("doc_id") else {})}
            drafts.append({"category": "resume", "eval_items": Q.EVAL_ITEMS["job"], "text": b["text"], "orig": b["text"], "bank_id": b["id"],
                           "gen_method": "fallback_job", "resume_ref": ref, "intent": b.get("subcategory") or ""})
    else:
        banks = s["picks"].get("resume_bank") or []
        for i, (cand, angle) in enumerate(Q.resume_pairs(s["candidates"])):
            tpl = Q.TEMPLATES[angle].format(label=cand["label"])
            if use_llm:
                r = Q.make_resume_question(cand, angle, s["snapshot"], banks[i] if i < len(banks) else None, role_name)
            else:
                ref = {"section": cand["section"], "index": cand["index"], "label": cand["label"],
                       "quote": (cand["item"].get("detail") or cand["item"].get("source") or "")[:160]}
                r = {"text": tpl, "resume_ref": ref, "gen_method": "template"}
            drafts.append({"category": "resume", "eval_items": Q.EVAL_ITEMS["resume"], "bank_id": None, "orig": tpl,
                           "intent": "이력서 항목의 " + Q.ANGLES[angle], **r})
    for cat in ("job", "company"):
        for b in s["picks"].get(cat, []):
            if not use_llm:
                text = b["text"]
            elif cat == "job":
                text = Q.contextualize_job(b["text"], s["job"], s["snapshot"], b.get("subcategory") or "")
            else:
                text = Q.contextualize(b["text"], s["job"], b.get("subcategory") or "")
            drafts.append({"category": cat, "eval_items": Q.EVAL_ITEMS[cat], "text": text, "orig": b["text"],
                           **_bank_fields(b), "intent": b.get("subcategory") or ""})
    return {"drafts": drafts, "log": s["log"] + [f"generate: {len(drafts)}문항"]}


PHIL_SCHEMA = {"type": "object", "properties": {
    "scores": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 5}},
    "pass": {"type": "boolean"}, "problems": {"type": "array", "items": {"type": "string"}},
    "revised_question": {"type": "string"}}, "required": ["scores", "pass", "problems", "revised_question"]}


def _rule_ok(d, text, snapshot):
    """A2·A6 공통 규칙: 길이 10~160자, 차단 목록, 이력서 질문은 고유 명칭·수치가 이력서에 있어야 함."""
    if not (10 <= len(text) <= 160) or Q.BLOCKLIST.search(text):
        return False
    if d["category"] == "resume" and d.get("gen_method") == "llm":
        return Q.grounded(text, [], snapshot)
    return True


def evaluate_philosophy(s: GenState) -> GenState:
    """A6 철학 적합성(prompts/evaluate/philosophy_fit.md): 원칙 6개 모두 4점 이상이면 통과.
    고친 질문은 규칙을 다시 검사하고 최대 2번 고친다. 끝내 실패하면 은행 원문·템플릿으로 되돌린다. 인성 문항(은행 원문)은 검사하지 않는다."""
    if not (s["use_llm"] and REVIEW):
        return {}
    job, drafts, stats = s["job"], [], {"pass": 0, "revised": 0, "reverted": 0, "error": 0}
    for d in s["drafts"]:
        d = dict(d)
        if d["category"] == "personality":
            drafts.append(d)
            continue
        system = prompts.load("evaluate/philosophy_fit", role_name=job.get("role_name") or "", company_name=job.get("company_name") or "",
                              eval_items=", ".join(d["eval_items"]))
        info = f"회사 정보: {(job.get('company_intro') or '')[:300]} / 주요 업무: {(job.get('duties') or '')[:300]}"
        text, ok = d["text"], False
        for _ in range(3):                     # 처음 검사 + 고친 질문 재검사 최대 2번
            try:
                r = llm.chat_json(system, f"{info}\n평가 의도: {d.get('intent') or '-'}\n질문: {text}", PHIL_SCHEMA, timeout=60, temperature=0)
            except llm.LLMError:
                stats["error"] += 1
                ok = True                      # 검수 모델 실패 → 앞 단계 규칙을 통과한 질문 그대로
                break
            if r["pass"] and all(x >= 4 for x in r["scores"][:6] or [0]):
                ok = True
                break
            nq = (r.get("revised_question") or "").strip()
            if not nq or nq == text or not _rule_ok(d, nq, s["snapshot"]):
                break
            text = nq
        if ok:
            stats["revised" if text != d["text"] else "pass"] += 1
            d["text"] = text
        else:
            stats["reverted"] += 1
            d["text"] = d["orig"]
            if d["category"] == "resume" and d.get("gen_method") == "llm":
                d["gen_method"] = "template"
        d["philosophy"] = "pass" if ok else "reverted"
        drafts.append(d)
    return {"drafts": drafts, "log": s["log"] + ["evaluate_philosophy: " + " · ".join(f"{k} {v}" for k, v in stats.items())]}


ANS_SCHEMA = {"type": "object", "properties": {"answerable": {"type": "boolean"}, "asks": {"type": "array", "items": {"type": "string"}},
              "revised_question": {"type": "string"}}, "required": ["answerable", "asks", "revised_question"]}


def evaluate_answerability(s: GenState) -> GenState:
    """A7 답변 가능성(prompts/evaluate/answerability.md): 규칙 _too_broad를 먼저 보고, 넓을 때만 모델로 좁힌다.
    좁힌 질문도 넓거나 규칙 위반이면 첫 요구 과제만 남긴다(기존 fit_scope와 같은 대체 경로)."""
    secs, drafts, n = config.QUESTION_SECONDS, [], 0
    for d in s["drafts"]:
        d = dict(d)
        if s["use_llm"] and Q._too_broad(d["text"], secs):
            n += 1
            nq = ""
            try:
                r = llm.chat_json(prompts.load("evaluate/answerability", seconds=secs, intent=d.get("intent") or ", ".join(d["eval_items"])),
                                  f"질문: {d['text']}", ANS_SCHEMA, timeout=60, temperature=0)
                nq = (r.get("revised_question") or "").strip()
            except llm.LLMError:
                pass
            if not nq or Q._too_broad(nq, secs) or not _rule_ok(d, nq, s["snapshot"]):
                nq = re.split(r"(?<=[?요])\s", d["text"])[0]
            d["text"] = nq
        drafts.append(d)
    return {"drafts": drafts, "log": s["log"] + [f"evaluate_answerability: 좁힘 {n}"]}


def finalize(s: GenState) -> GenState:
    """코드: 순서 고정(Q.PLAN = 인성 2 → 이력서 3 → 직무 2 → 기업 1). 저장 형식은 기존 build_set과 같다."""
    order = {c: i for i, (c, _) in enumerate(Q.PLAN)}
    keep = ("category", "eval_items", "text", "bank_id", "gen_method", "resume_ref")
    final = [{k: d.get(k) for k in keep if k in d} for d in sorted(s["drafts"], key=lambda d: order[d["category"]])]
    return {"final_questions": final, "log": s["log"] + [f"finalize: {len(final)}문항"]}


def _build_generation():
    g = StateGraph(GenState)
    for name, fn in (("start", start), ("extract_keywords", extract_keywords), ("evaluate_trust", evaluate_trust),
                     ("retrieve", retrieve), ("generate", generate), ("evaluate_philosophy", evaluate_philosophy),
                     ("evaluate_answerability", evaluate_answerability), ("finalize", finalize)):
        g.add_node(name, fn)
    g.add_edge(START, "start")
    for a, b in (("start", "extract_keywords"), ("extract_keywords", "evaluate_trust"), ("evaluate_trust", "retrieve"),
                 ("retrieve", "generate"), ("generate", "evaluate_philosophy"), ("evaluate_philosophy", "evaluate_answerability"),
                 ("evaluate_answerability", "finalize"), ("finalize", END)):
        g.add_edge(a, b)
    return g.compile()


GENERATION = _build_generation()


def generate_set(application, use_llm=True):
    """그래프 1-A 실행 → 10문항(기존 build_set과 같은 형식) + 처리 기록."""
    out = GENERATION.invoke({"application": application, "use_llm": use_llm},
                            {"run_name": "question_graph", "tags": ["graph1", "generate"], "metadata": {"application_id": application["id"]}})
    return out["final_questions"], {"log": out.get("log", []), "keyword_report": out.get("keyword_report", {})}


# ======================= B. 면접 한 걸음 (답변 1개) =======================
PRE_SCHEMA = {"type": "object", "properties": {
    "question_topic": {"type": "string"}, "answer_topic": {"type": "string"}, "addresses_question": {"type": "boolean"},
    "logic": {"type": "integer", "minimum": 1, "maximum": 5}, "relevance": {"type": "integer", "minimum": 1, "maximum": 5},
    "inconsistent_with_resume": {"type": "boolean"}, "generic_only": {"type": "boolean"}, "followup_question": {"type": "string"}},
    "required": ["question_topic", "answer_topic", "addresses_question", "logic", "relevance", "inconsistent_with_resume", "generic_only", "followup_question"]}
PRE_SYSTEM = """너는 1차 면접 진행 중 답변을 빠르게 선평가하는 면접관이다. 아래 순서로 판단한다.
1. question_topic: 질문이 묻는 핵심을 10자 안팎으로 쓴다.
2. answer_topic: 답변이 실제로 말한 핵심을 10자 안팎으로 쓴다.
3. addresses_question: 답변이 질문의 핵심을 직접 다루면 true, 다른 주제를 말하면 false.
4. relevance(1~5): addresses_question이 false면 반드시 1~2점. logic(1~5): 주장-근거-결론의 일관성.
5. inconsistent_with_resume: 근거 이력서 항목이 주어졌고 답변 내용이 그것과 명백히 다를 때만 true.
6. generic_only: 구체적인 사례·수치·경험 없이 일반론만 말했을 때 true.
7. followup_question: 질문의 원래 의도를 다시 겨냥하되 답변에 나온 내용을 짚어 더 구체적으로 묻는 한국어 질문 한 문장(60초 안에 답할 범위).
거짓말 여부를 판단하거나 추궁하지 않는다. 나이·가족·학교 서열 등 인적 사정은 묻지 않는다."""


class StepState(TypedDict, total=False):
    att: dict
    q: dict
    text: str
    source: str | None
    proposal: dict | None          # judge_followup 결과 {question, reason}
    followup_id: int | None
    outcome: str                   # next | coding | finished


def current_question(attempt_id):
    return db.one("""SELECT * FROM attempt_questions WHERE attempt_id=%s AND answer_status='pending'
                     ORDER BY seq, parent_id NULLS FIRST, id LIMIT 1""", attempt_id)


def record_answer(s: StepState) -> StepState:
    db.run("UPDATE attempt_questions SET answer_text=%s, answer_status=%s, answer_source=%s, answered_at=now() WHERE id=%s",
           s["text"] or None, "answered" if s["text"] else "no_answer", s["source"], s["q"]["id"])
    return {}


def judge(att, q, answer):
    """A3 꼬리질문 판단 (INT-06·07). 반환 (꼬리질문, 사유) | None.
    ① 생략 규칙(이미 꼬리질문·4개 소진·내용 없음·5자 미만) → ② 모델 선평가(FOLLOWUP_TIMEOUT 안) → 조건 충족 + 문장 검사."""
    from .. import evaluation
    if q["parent_id"] is not None or att["followups_used"] >= config.MAX_FOLLOWUPS:
        return None
    if evaluation.NO_CONTENT.match(answer) or len(answer.strip()) < 5:
        return None
    ref = f"\n근거 이력서 항목: {q['resume_ref']['quote']}" if q["resume_ref"] and q["resume_ref"].get("quote") else ""
    try:
        r = llm.chat_json(PRE_SYSTEM, f"질문: {q['text']}{ref}\n답변: {answer[:1500]}", PRE_SCHEMA, timeout=config.FOLLOWUP_TIMEOUT)
    except llm.LLMError:
        return None
    off = (not r["addresses_question"]) or r["logic"] <= 2 or r["relevance"] <= 2
    if not (off or r["inconsistent_with_resume"] or r["generic_only"]):
        return None
    fq = (r.get("followup_question") or "").strip()
    if not fq or Q.BLOCKLIST.search(fq) or len(fq) > 150:
        return None
    return fq, ("논리성·관련성 2점 이하" if off else ("이력서와 다름" if r["inconsistent_with_resume"] else "일반론"))


def judge_followup(s: StepState) -> StepState:
    if not s["text"]:
        return {"proposal": None}
    fu = judge(s["att"], s["q"], s["text"])
    return {"proposal": {"question": fu[0], "reason": fu[1]} if fu else None}


def make_followup(s: StepState) -> StepState:
    """꼬리질문을 같은 seq 자리에 끼워 넣고 사용 수를 늘린다(질문당 1개·전체 4개는 judge가 이미 확인)."""
    from .. import speech
    att, q, p = s["att"], s["q"], s["proposal"]
    row = db.one("""INSERT INTO attempt_questions (attempt_id, seq, parent_id, category, eval_items, text, gen_method, time_limit)
                    VALUES (%s,%s,%s,'followup',%s,%s,'llm',%s) RETURNING id""",
                 att["id"], q["seq"], q["id"], q["eval_items"], p["question"], config.FOLLOWUP_SECONDS)
    db.run("UPDATE attempt_questions SET prescore=%s WHERE id=%s", db.J({"followup_reason": p["reason"]}), q["id"])
    db.run("UPDATE attempts SET followups_used=followups_used+1 WHERE id=%s", att["id"])
    speech.prewarm([p["question"]])
    return {"followup_id": row["id"] if row else None}


def next_question(s: StepState) -> StepState:
    """남은 질문이 없으면 코딩테스트로 넘기거나 응시를 끝낸다(상태 전이는 코드)."""
    from .. import coding, scheduler
    aid = s["att"]["id"]
    if current_question(aid):
        return {"outcome": "next"}
    if s["att"]["criteria_snapshot"]["coding_enabled"]:
        coding.assign_problems(aid)
        db.run(f"""UPDATE attempts SET status='coding', phase='coding', coding_deadline=now() + interval '{config.CODING_MINUTES} minutes'
                   WHERE id=%s""", aid)
        return {"outcome": "coding"}
    scheduler.finish_attempt(aid, "completed", "interview_done")
    return {"outcome": "finished"}


def _build_step():
    g = StateGraph(StepState)
    for name, fn in (("record_answer", record_answer), ("judge_followup", judge_followup),
                     ("make_followup", make_followup), ("next_question", next_question)):
        g.add_node(name, fn)
    g.add_edge(START, "record_answer")
    g.add_edge("record_answer", "judge_followup")
    g.add_conditional_edges("judge_followup", lambda s: "make" if s.get("proposal") else "next",
                            {"make": "make_followup", "next": "next_question"})
    g.add_edge("make_followup", "next_question")
    g.add_edge("next_question", END)
    return g.compile()


INTERVIEW_STEP = _build_step()


def answer_step(att, q, text, source):
    return INTERVIEW_STEP.invoke({"att": att, "q": q, "text": text or "", "source": source},
                                 {"run_name": "interview_step", "tags": ["graph1", "interview"], "metadata": {"attempt_id": att["id"]}})
