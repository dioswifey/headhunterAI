"""Rubric evaluation, scoring, pass/fail, analysis job queue (PRD 4.8 EVL-01~08, TRD §7).

LLM only scores items 0~5 with quotes; every number after that is computed here.
"""
import re
from datetime import datetime, timedelta, timezone

from . import coding, config, db, llm

ITEMS = ["expertise", "logic", "relevance", "delivery", "fit"]
ITEM_KO = {"expertise": "직무 전문성", "logic": "논리성", "relevance": "내용 관련성·구체성", "delivery": "전달력", "fit": "조직 적합성"}
WEIGHT_COL = {"expertise": "w_expertise", "logic": "w_logic", "relevance": "w_relevance", "delivery": "w_delivery", "fit": "w_fit"}
NO_CONTENT = re.compile(r"^\s*(잘\s*)?(모르겠|모릅니다|모르겠습니다|패스|없습니다)[^가-힣]*\s*$")

RUBRIC = """0: 미응답(이 경우는 입력되지 않음)
1: 답변했으나 최저 수준 — 질문과 관련성이 거의 없음, "모르겠습니다", 근거 인용 불가
2: 부분적으로 관련되나 핵심이 빠지거나 오류가 있음
3: 기본 요구를 충족함
4: 근거와 사례가 분명하고 우수함
5: 깊이·정확성·구체성이 모두 탁월함"""
ITEM_DEF = {
    "expertise": "AI/AX 개념과 기술을 정확히 이해하고 적용하는가",
    "logic": "주장–근거–결론이 일관되고 모순이 없는가",
    "relevance": "질문 의도에 맞고 실제 경험·사례로 구체화했는가",
    "delivery": "핵심을 먼저 말하고 구조적으로 명확하게 설명하는가(답변 텍스트 기준)",
    "fit": "협업·책임감·성장 태도와 지원 동기가 드러나는가",
}
EVAL_SYSTEM = f"""너는 채용 1차 면접 답변을 루브릭으로 채점하는 평가자다.
루브릭(항목별 0~5점):
{RUBRIC}
규칙:
1. 지정된 평가 항목만 채점한다.
2. quote에는 점수의 근거가 된 지원자 답변 원문 일부를 글자 그대로 복사한다(10~80자). 답변에 없는 문장을 만들지 않는다.
3. 지원자의 이름·성별·나이·말투·발음 등 인적 특성은 고려하지 않는다.
4. rationale은 한 문장 한국어로 쓴다."""
EVAL_SCHEMA = {"type": "object", "properties": {"scores": {"type": "array", "items": {"type": "object", "properties": {
    "item": {"type": "string", "enum": ITEMS}, "score": {"type": "integer", "minimum": 1, "maximum": 5},
    "quote": {"type": "string"}, "rationale": {"type": "string"}}, "required": ["item", "score", "quote", "rationale"]}}},
    "required": ["scores"]}

SUMMARY_SCHEMA = {"type": "object", "properties": {
    "strengths": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    "improvements": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    "summary": {"type": "string"}}, "required": ["strengths", "improvements", "summary"]}


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def quote_ok(quote, answer):
    q = _norm(quote)
    return len(q) >= 4 and q in _norm(answer)


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
    ref = ""
    if q["resume_ref"] and q["resume_ref"].get("quote"):
        ref = f"\n근거 이력서 항목({q['resume_ref'].get('label')}): {q['resume_ref']['quote']}"
    item_txt = "\n".join(f"- {it}: {ITEM_DEF[it]}" for it in items)
    out = {}
    pending = list(items)
    for attempt in range(2):  # 인용 불일치 시 1회 재생성 (EVL-02)
        try:
            res = llm.chat_json(EVAL_SYSTEM, f"평가 항목:\n{item_txt}\n{ref}\n\n{convo}", EVAL_SCHEMA, temperature=0)
        except llm.LLMError:
            if attempt == 0:
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
    """EVL-03~05. item_points: {item: [0..5 scores]}"""
    item_scores = {}
    for it in ITEMS:
        pts = item_points.get(it, [])
        item_scores[it] = round(sum(pts) / len(pts) / 5 * 100, 2) if pts else 0.0
    interview = round(sum(item_scores[it] * criteria[WEIGHT_COL[it]] / 100 for it in ITEMS), 2)
    if criteria["coding_enabled"]:
        r = criteria["coding_ratio"] / 100
        total = round(interview * (1 - r) + (coding_score or 0) * r, 2)
    else:
        total = interview
    result = "pass" if total >= criteria["pass_score"] else "fail"
    return item_scores, interview, total, result


def summarize(blocks, scores_by_q, item_scores):
    lines = []
    for b in blocks:
        q = b["q"]
        sc = scores_by_q.get(q["id"], {})
        lines.append(f"[{q['seq']}] {q['text']}\n답변: {(q['answer_text'] or '(미응답)')[:400]}\n점수: " +
                     ", ".join(f"{ITEM_KO[k]} {v['score']}" for k, v in sc.items()))
    items = ", ".join(f"{ITEM_KO[k]} {v}" for k, v in item_scores.items())
    try:
        return llm.chat_json(
            "너는 채용 1차 면접 결과를 정리하는 평가자다. 아래 채점 결과와 답변에 실제로 있는 내용만 근거로, 강점과 개선점을 각각 1~3개(중요도 순), 총평 2문장을 한국어로 쓴다. 답변에 없는 경험을 지어내지 않는다. 인적 특성은 언급하지 않는다.",
            f"항목 점수(100점 환산): {items}\n\n" + "\n\n".join(lines)[:9000], SUMMARY_SCHEMA, temperature=0.2)
    except llm.LLMError:
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
    att = db.one("""SELECT a.*, ap.resume_snapshot, ap.job_id FROM attempts a JOIN applications ap ON ap.id=a.application_id
                    WHERE a.id=%s""", attempt_id)
    if db.one("SELECT 1 FROM evaluations WHERE attempt_id=%s", attempt_id):
        return  # 중복 방지
    criteria = att["criteria_snapshot"]
    versions = {"model": llm.model_version(), **config.VERSIONS}
    events = db.all_("SELECT type, count(*) n FROM attempt_events WHERE attempt_id=%s GROUP BY type", attempt_id)
    if att["status"] == "cancelled":  # EVL-05: 부정행위 취소 → 평가 없이 불합격
        pub = {"result": "fail", "status": "응시 취소", "reason": "화면 이탈 2회로 응시가 취소되었습니다.", "strengths": [], "improvements": []}
        db.run("""INSERT INTO evaluations (attempt_id, item_scores, interview_score, coding_score, total_score, result, reason,
                  criteria_snapshot, versions, public_feedback, summary) VALUES (%s,%s,0,NULL,0,'fail','응시 취소(부정행위)',%s,%s,%s,%s)""",
               attempt_id, db.J({it: 0 for it in ITEMS}), db.J(criteria), db.J(versions), db.J(pub), "화면 이탈 2회로 응시 취소")
        return
    blocks = question_blocks(attempt_id)
    scores_by_q, item_points = {}, {}
    for b in blocks:
        sc = evaluate_block(b, att["resume_snapshot"])
        scores_by_q[b["q"]["id"]] = sc
        for it, v in sc.items():
            item_points.setdefault(it, []).append(v["score"])
    coding_score = coding.grade(attempt_id) if criteria["coding_enabled"] else None
    if criteria["coding_enabled"] and coding_score is None:
        coding_score = 0.0
    item_scores, interview, total, result = compute(item_points, criteria, coding_score)
    summ = summarize(blocks, scores_by_q, item_scores)
    cfb = code_feedback(attempt_id) if criteria["coding_enabled"] else None
    status_ko = {"completed": "응시 완료", "terminated": "응시 종료(지원자 종료)"}.get(att["status"], att["status"])
    pub = {"result": result, "status": status_ko, "strengths": summ["strengths"][:3], "improvements": summ["improvements"][:3],
           "notice": "이 피드백은 AI가 생성했으며, 근거가 된 답변 내용에 대해 설명을 요청할 수 있습니다."}
    with db.tx() as conn:
        for qid, sc in scores_by_q.items():
            for it, v in sc.items():
                conn.execute("""INSERT INTO question_scores (question_id, eval_item, score, quote, verified, rationale)
                                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (question_id, eval_item) DO UPDATE
                                SET score=EXCLUDED.score, quote=EXCLUDED.quote, verified=EXCLUDED.verified, rationale=EXCLUDED.rationale""",
                             (qid, it, v["score"], v["quote"], v["verified"], v["rationale"]))
        conn.execute("""INSERT INTO evaluations (attempt_id, item_scores, interview_score, coding_score, total_score, result, reason,
                        criteria_snapshot, versions, strengths, improvements, summary, code_feedback, public_feedback)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                     (attempt_id, db.J(item_scores), interview, coding_score, total, result,
                      f"종합 총점 {total} {'≥' if result == 'pass' else '<'} 합격 기준 {criteria['pass_score']}",
                      db.J(criteria), db.J({**versions, "events": {e['type']: e['n'] for e in events}}),
                      db.J(summ["strengths"][:3]), db.J(summ["improvements"][:3]), summ["summary"], cfb, db.J(pub)))


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
