"""Rubric evaluation, scoring, pass/fail, analysis job queue (PRD 4.8 EVL-01~08, TRD §7).

LLM only scores items 0~5 with quotes; every number after that is computed here.
"""
import re
from datetime import datetime, timedelta, timezone

from . import coding, config, db, llm, prompts, report_guard

ITEMS = ["expertise", "logic", "relevance", "delivery", "fit"]
ITEM_KO = {"expertise": "직무 전문성", "logic": "논리성", "relevance": "내용 관련성·구체성", "delivery": "전달력", "fit": "조직 적합성"}
WEIGHT_COL = {"expertise": "w_expertise", "logic": "w_logic", "relevance": "w_relevance", "delivery": "w_delivery", "fit": "w_fit"}
NO_CONTENT = re.compile(r"^\s*(잘\s*)?(모르겠|모릅니다|모르겠습니다|패스|없습니다)[^가-힣]*\s*$")

ITEM_DEF = {
    "expertise": "지원 직무(백엔드·데이터·AI/AX 등 질문에 맞는 분야)의 핵심 개념·기술을 정확히 이해하는가 — 정의뿐 아니라 동작 원리·한계를 알고, 기술을 선택한 트레이드오프 근거를 설명하는가",
    "logic": "주장–근거–결론이 일관되고 모순이 없는가 — 예상 못한 문제·갈등이 생겼을 때 원인을 파악해 현실적인 대안을 실행한 과정이 드러나는가",
    "relevance": "질문 의도에 맞게 답했는가(구체적 사례·수치가 있으면 가점이지 없다고 감점하지 않는다) — 팀 전체 성과가 아니라 본인이 직접 설계·구현·개선한 역할과 기여를 구분해서 말하는가",
    "delivery": "결론을 먼저 말하고(두괄식) 전문 용어를 상대가 알아듣게 풀어 설명하는가",
    "fit": "협업 태도·의견 충돌이나 모르는 질문을 대하는 태도가 합리적인가, 피드백 수용과 성장 의지·지원 동기가 드러나는가",
}
EVAL_SYSTEM = prompts.load("evaluate/scoring")  # 2026-11-19: evaluation.py 안에 박혀 있던 문자열 → 마운트 파일로 이전(재빌드 없이 수정 가능)
EVAL_SCHEMA = {"type": "object", "properties": {"scores": {"type": "array", "items": {"type": "object", "properties": {
    "item": {"type": "string", "enum": ITEMS}, "quote": {"type": "string"},
    "score": {"type": "integer", "minimum": 1, "maximum": 5}, "rationale": {"type": "string"}}, "required": ["item", "quote", "score", "rationale"]}}},
    "required": ["scores"]}

SUMMARY_SCHEMA = {"type": "object", "properties": {
    "strengths": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    "improvements": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    "summary": {"type": "string"}}, "required": ["strengths", "improvements", "summary"]}


def _norm(s):
    """인용 대조용 정규화. 공백에 더해 구두점·따옴표도 지운다(2026-11-19) —
    쉼표·마침표 하나 차이로 멀쩡한 인용이 탈락해 좋은 답변이 1점으로 떨어지던 문제(QA-32 부수 원인).
    '원문 부분 문자열이어야 한다'는 규칙 자체는 그대로다 — 표기 차이만 흡수한다."""
    return re.sub(r"[\s.,!?·…‥\"'“”‘’()\[\]{}~\-—–:;]+", "", s or "")


def quote_ok(quote, answer):
    q = _norm(quote)
    return len(q) >= 4 and q in _norm(answer)


def _score_reference(q):
    """채점용 사내문서·은행 근거(모범 답변 요지·기대 키워드) 조회 — gen_method로 internal_q:/redis: 출처를 가려낸다.
    `question_graph.bank_reference`와 같은 조회지만 `FOLLOWUP_INTERNAL_REF` 게이트를 안 쓴다(그 게이트는 꼬리질문
    **힌트 노출** 위험 때문인데, 채점은 지원자에게 안 보이는 사후 처리라 같은 위험이 없다, 2026-11-19).
    실패는 조용히 빈 값으로 둔다(이 참고 자료가 없어도 채점 자체는 그대로 돈다)."""
    gm = (q.get("gen_method") or "")
    ideal, keywords = "", ""
    try:
        if gm.startswith("internal_q:"):
            from . import internal_store
            qid = gm[len("internal_q:"):].split("|")[0]
            got = internal_store.question_collection(q.get("category") or "").get(ids=[qid], include=["metadatas"])
            m = (got.get("metadatas") or [None])[0] or {}
            ideal, keywords = (m.get("model_answer") or "").strip(), (m.get("expected_keywords") or "").strip()
        elif gm.startswith("redis:"):
            import redis
            r = redis.Redis.from_url(config.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
            kw, idl = r.hmget("qb:" + gm[len("redis:"):].split("|")[0], "expected_keywords", "model_answer")
            ideal = (idl or b"").decode("utf-8", "ignore").strip()
            keywords = (kw or b"").decode("utf-8", "ignore").strip()
    except Exception:  # noqa: BLE001 — 컬렉션·Redis 오류는 채점을 막지 않는다
        pass
    return ideal, keywords


def _career_level(snapshot):
    """지원자 연차 힌트(평가 관점 차이, 2026-11-19) — 경력 항목이 있으면 경력, 없으면 신입으로 본다."""
    return "경력" if (snapshot or {}).get("career") else "신입"


def question_blocks(attempt_id):
    """Base questions with their follow-up merged (EVL-01)."""
    rows = db.all_("SELECT * FROM attempt_questions WHERE attempt_id=%s ORDER BY seq, parent_id NULLS FIRST, id", attempt_id)
    blocks = {}
    for r in rows:
        if r["parent_id"] is None:
            blocks[r["id"]] = {"q": r, "followups": []}
    for r in rows:
        if r["parent_id"] is not None and r["parent_id"] in blocks:
            blocks[r["parent_id"]]["followups"].append(r)
    return list(blocks.values())


def evaluate_block(block, snapshot):
    q = block["q"]
    answers = [q] + block["followups"]
    answered = [a for a in answers if a["answer_status"] == "answered" and (a["answer_text"] or "").strip()]
    items = q["eval_items"]
    if not answered:  # 미응답 0점
        return {it: {"score": 0, "quote": None, "verified": True, "rationale": "미응답"} for it in items}
    full = "\n".join(a["answer_text"] for a in answered)
    if all(NO_CONTENT.match(a["answer_text"]) for a in answered):  # INT-07
        return {it: {"score": 1, "quote": None, "verified": True, "rationale": "내용 없는 답변"} for it in items}
    convo = f"질문: {q['text']}\n답변: {q['answer_text'] or '(미응답)'}"
    for f in block["followups"]:
        convo += f"\n꼬리질문: {f['text']}\n답변: {f['answer_text'] or '(미응답)'}"
    ref = f"\n지원자 연차: {_career_level(snapshot)}"
    if q["resume_ref"] and q["resume_ref"].get("quote"):
        ref += f"\n근거 이력서 항목({q['resume_ref'].get('label')}): {q['resume_ref']['quote']}"
    ideal, keywords = _score_reference(q)
    if ideal:
        ref += f"\n참고 모범 답변 요지(표현이 달라도 의미가 통하면 맞는 답으로 인정): {ideal}"
    if keywords:
        ref += f"\n참고 기대 키워드: {keywords}"
    item_txt = "\n".join(f"- {it}: {ITEM_DEF[it]}" for it in items)
    user = f"평가 항목:\n{item_txt}\n\n[참고 자료 — 인용 금지, 내용 부합 판단에만 쓴다]{ref}\n\n[대화 — quote는 여기서만]\n{convo}"
    out = {}
    pending = list(items)
    for attempt in range(5):  # 인용 불일치 시 최대 4회 재생성(2026-11-19: 억울한 1점을 더 줄이려고 3→5)(2026-11-19: 참고 자료 추가로 인용 혼동이 늘어 1→2회로 늘림, EVL-02)
        try:
            res = llm.chat_json(EVAL_SYSTEM, user, EVAL_SCHEMA, temperature=0)
        except llm.LLMError:
            if attempt < 2:
                continue
            raise
        for s in res.get("scores", []):
            it = s.get("item")
            if it in pending and 1 <= int(s.get("score", 0)) <= 5 and quote_ok(s.get("quote", ""), full):
                out[it] = {"score": int(s["score"]), "quote": s["quote"].strip(), "verified": True, "rationale": s.get("rationale", "")}
                pending.remove(it)
        if not pending:
            break
    for it in pending:  # 근거 없는 항목 → 1점 확정
        out[it] = {"score": 1, "quote": None, "verified": False, "rationale": "근거 인용 검증 실패로 1점 확정"}
    return out


def compute(item_points, criteria, coding_score):
    """EVL-03~05, DEC-06~09. item_points: {item: [0..5 scores]}.
    AI 면접 점수와 코딩테스트 점수는 섞지 않고 각각 독립 채점한다. 합불은 면접 점수 단독으로 pass_score와 비교한 뒤,
    coding_gate_mode(hard/soft)가 있으면 그 위에 override한다(coding_gate_score·soft_override는 config 고정값)."""
    item_scores = {}
    for it in ITEMS:
        pts = item_points.get(it, [])
        item_scores[it] = round(sum(pts) / len(pts) / 5 * 100, 2) if pts else 0.0
    interview = round(sum(item_scores[it] * criteria[WEIGHT_COL[it]] / 100 for it in ITEMS), 2)
    result = "pass" if interview >= criteria["pass_score"] else "fail"
    gate = criteria.get("coding_gate_mode", "none")
    if criteria["coding_enabled"] and gate != "none" and coding_score is not None and coding_score < config.CODING_GATE_SCORE:
        if gate == "hard":
            result = "fail"
        elif gate == "soft":
            result = "pass" if interview >= config.CODING_GATE_SOFT_OVERRIDE else "fail"
    total = interview
    return item_scores, interview, total, result


SUMMARY_GEN_SCHEMA = {"type": "object", "properties": {
    "strengths": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    "strength_items": {"type": "array", "items": {"type": "string", "enum": ITEMS}, "maxItems": 3},
    "improvements": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    "improvement_items": {"type": "array", "items": {"type": "string", "enum": ITEMS}, "maxItems": 3},
    "summary": {"type": "string"}}, "required": ["strengths", "strength_items", "improvements", "improvement_items", "summary"]}
SUMMARY_SYSTEM = ("너는 채용 1차 면접 결과를 정리하는 평가자다. 아래 채점 결과와 답변에 실제로 있는 내용만 근거로, 강점과 개선점을 각각 1~3개(중요도 순), "
                  "총평 2문장을 한국어로 쓴다. 강점은 반드시 '강점 후보 항목'에서만, 개선점은 반드시 '개선점 후보 항목'에서만 고르고, "
                  "각 문장이 어느 항목인지 strength_items·improvement_items에 같은 순서로 적는다. 답변에 없는 경험·수치를 지어내지 않는다. "
                  "따옴표는 답변 원문을 글자 그대로 옮길 때만 쓴다. 합격·불합격 여부와 인적 특성은 언급하지 않는다.")


def _summary_problem(res, top, bottom, allowed):
    """요약 결과 검사 → 사유 코드(문제 없으면 None). report_guard 규칙 + 점수-서술 일치(상위·하위 2개 항목)."""
    s, si = res.get("strengths") or [], res.get("strength_items") or []
    i, ii = res.get("improvements") or [], res.get("improvement_items") or []
    if not s or not i or not (res.get("summary") or "").strip():
        return "empty"
    if len(si) != len(s) or len(ii) != len(i):
        return "item_label_mismatch"
    if any(x not in top for x in si) or any(x not in bottom for x in ii):
        return "score_inconsistent"
    return report_guard.check_text(s + i + [res["summary"]], allowed)


def summarize(blocks, scores_by_q, item_scores):
    """A4 요약. 검사(금지어·지어낸 수치·점수-서술 일치)에 걸리면 1회 다시 만들고, 그래도 걸리면 항목 점수 기준 대체 문구."""
    lines = []
    for b in blocks:
        q = b["q"]
        sc = scores_by_q.get(q["id"], {})
        lines.append(f"[{q['seq']}] {q['text']}\n답변: {(q['answer_text'] or '(미응답)')[:400]}\n점수: " +
                     ", ".join(f"{ITEM_KO[k]} {v['score']}" for k, v in sc.items()))
    items = ", ".join(f"{ITEM_KO[k]} {v}" for k, v in item_scores.items())
    top, bottom = report_guard.top_bottom(item_scores)
    tie = report_guard.is_tie(item_scores)
    if tie:   # 동점: 항목 비교로 강점·개선점을 가를 수 없다 → 답변 내용 기반으로 쓰고 '항목 간 차이 없음'을 적게 한다
        top = bottom = set(item_scores)
        cand = ("항목 간 점수 차이가 없다(동점). 강점·개선점을 항목 점수 비교로 쓰지 말고, 답변 내용에서 실제로 보인 점만 근거로 쓴다. "
                "답변에서 뚜렷한 강점을 찾기 어려우면 강점에 그 사실을 한 문장으로 쓴다. 개선점은 답변 내용에 근거한 구체적인 보완 방향으로 쓴다. "
                "총평에 '항목 간 점수 차이가 없다'는 점을 넣는다. 항목 라벨은 문장 내용에 가장 가까운 항목을 고른다.")
    else:
        cand = (f"강점 후보 항목: {', '.join(f'{it}({ITEM_KO[it]})' for it in ITEMS if it in top)}\n"
                f"개선점 후보 항목: {', '.join(f'{it}({ITEM_KO[it]})' for it in ITEMS if it in bottom)}")
    user = f"항목 점수(100점 환산): {items}\n{cand}\n\n" + "\n\n".join(lines)[:9000]
    answers = "\n".join(a["answer_text"] or "" for b in blocks for a in [b["q"]] + b["followups"])
    allowed = report_guard.number_tokens(user, *item_scores.values())
    for _ in range(2):
        try:
            # 2026-11-24(컨텍스트 엔지니어링 ③): 이 user 프롬프트의 상한이 9,000자인데 창 기본값은 6144토큰이다.
            # 실측 응시 10건의 최대값은 5.5k자(= 창 안)였지만 꼬리질문이 많은 세트에서는 넘길 수 있고, 넘기면
            # Ollama가 조용히 잘라 요약이 앞 질문만 보고 쓰인다 → 상한보다 큰 창을 명시한다.
            res = llm.chat_json(SUMMARY_SYSTEM, user, SUMMARY_GEN_SCHEMA, temperature=0.2, num_ctx=8192)
        except llm.LLMError:
            continue
        if _summary_problem(res, top, bottom, allowed) is None:
            fix = lambda t: report_guard.strip_bad_quotes(t, answers)  # noqa: E731 — 원문에 없는 '인용'은 따옴표만 없앤다
            return {"strengths": [fix(t) for t in res["strengths"]], "improvements": [fix(t) for t in res["improvements"]],
                    "summary": fix(res["summary"])}
    if tie:
        return {"strengths": ["항목 간 점수 차이가 없어 특정 강점을 꼽기 어려웠습니다."],
                "improvements": ["질문 의도에 맞춰 실제 경험과 근거를 들어 답변을 구체적으로 보완하면 좋겠습니다."],
                "summary": "자동 요약 생성에 실패해 항목 점수 기준으로 정리했습니다. 항목 간 점수 차이가 없어 항목 비교 대신 답변 구체화를 권합니다."}
    best = sorted(item_scores.items(), key=lambda kv: -kv[1])
    return {"strengths": [f"{ITEM_KO[best[0][0]]} 항목에서 상대적으로 높은 평가를 받았습니다."],
            "improvements": [f"{ITEM_KO[best[-1][0]]} 항목을 보완하면 좋겠습니다."], "summary": "자동 요약 생성에 실패해 항목 점수 기준으로 정리했습니다."}


CODE_FB_SCHEMA = {"type": "object", "properties": {"feedback": {"type": "string"}}, "required": ["feedback"]}


def code_feedback(attempt_id):
    subs = db.all_("SELECT s.code, s.passed, s.total, p.title, p.statement FROM coding_submissions s JOIN coding_problems p ON p.id=s.problem_id WHERE s.attempt_id=%s", attempt_id)
    if not subs:
        return None
    body = "\n\n".join(f"문제: {s['title']}\n통과: {s['passed']}/{s['total']}\n코드:\n{s['code'][:2500]}" for s in subs)
    try:  # COD-06: 담당자 리포트 전용, 점수 미반영
        return llm.chat_json("너는 코드 리뷰어다. 제출 코드의 접근 방식·정확성·가독성을 담당자용으로 3문장 이내 한국어로 평가한다. 점수는 매기지 않는다.",
                             body, CODE_FB_SCHEMA, timeout=90)["feedback"]
    except llm.LLMError:
        return None


def analyze(attempt_id):
    """그래프 2 평가·합불(LangGraph, ADLC v0.5 6.3): load → evaluate_blocks → compute → summarize → decide → save.
    노드 코드는 backend/app/agents/evaluation_graph.py, 점수·판정 계산은 이 파일의 compute·_auto_decision."""
    from .agents import evaluation_graph
    evaluation_graph.run(attempt_id)


def decision_mode(criteria):
    return (criteria or {}).get("decision_mode") or "auto"


def _auto_decision(criteria, result):
    """auto: AI(코드) 판정을 바로 확정. hybrid: 담당자 확인 전까지 final_result=NULL."""
    if decision_mode(criteria) == "auto":
        return result, datetime.now(timezone.utc)
    return None, None


def enqueue(attempt_id):
    db.run("INSERT INTO analysis_jobs (attempt_id) VALUES (%s) ON CONFLICT DO NOTHING", attempt_id)
    db.run("UPDATE attempts SET analysis_status='queued' WHERE id=%s AND analysis_status IN ('none')", attempt_id)


def work_once():
    """Claim one due job (SKIP LOCKED), run, retry with exponential backoff (EVL-08)."""
    with db.tx() as conn:
        job = conn.execute("""SELECT * FROM analysis_jobs WHERE status IN ('queued','running') AND next_run_at <= now()
                              ORDER BY next_run_at LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
        if not job:
            return False
        conn.execute("UPDATE analysis_jobs SET status='running', tries=tries+1, updated_at=now(), next_run_at=now() + interval '10 minutes' WHERE attempt_id=%s",
                     (job["attempt_id"],))
        conn.execute("UPDATE attempts SET analysis_status='running' WHERE id=%s", (job["attempt_id"],))
    aid = job["attempt_id"]
    try:
        analyze(aid)
        db.run("UPDATE analysis_jobs SET status='done', last_error=NULL, updated_at=now() WHERE attempt_id=%s", aid)
        db.run("UPDATE attempts SET analysis_status='done' WHERE id=%s", aid)
    except Exception as e:
        tries = job["tries"] + 1
        age = datetime.now(timezone.utc) - job["created_at"]
        if tries >= 3 or age > timedelta(minutes=30):
            db.run("UPDATE analysis_jobs SET status='needs_check', last_error=%s, updated_at=now() WHERE attempt_id=%s", type(e).__name__, aid)
            db.run("UPDATE attempts SET analysis_status='needs_check' WHERE id=%s", aid)
            for op in db.all_("SELECT id FROM users WHERE role='operator'"):
                db.run("INSERT INTO notifications (user_id, type, title, body) VALUES (%s,'ops','분석 확인 필요',%s)",
                       op["id"], f"응시 #{aid} 분석이 자동 재시도 후에도 실패했습니다. 재처리가 필요합니다.")
        else:
            db.run("UPDATE analysis_jobs SET status='queued', last_error=%s, next_run_at=now() + make_interval(secs => %s), updated_at=now() WHERE attempt_id=%s",
                   type(e).__name__, 30 * (2 ** (tries - 1)), aid)
            db.run("UPDATE attempts SET analysis_status='queued' WHERE id=%s", aid)
    return True
