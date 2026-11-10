"""그래프 2 — 평가·합불 (ADLC v0.5 6.3, LangGraph).

응시 종료 → analysis_jobs 큐 → evaluation.work_once → evaluation.analyze → run(attempt_id).

  load ─(취소된 응시)→ save_cancelled → END
       └→ evaluate_blocks → compute → summarize → decide → save → END

- 모델 호출은 evaluate_blocks(A4 채점, 인용 검사 quote_ok 포함)·summarize(A4 요약)·코드 피드백뿐이고 모두 llm.chat_json.
- 점수 합산·AI 판정(compute)·확정(decide)·저장은 코드 노드만 한다(ADLC B-01). 그래프는 순서만 정한다.
- 저장 기준은 DB. 체크포인터를 쓰지 않는다(실패하면 큐가 처음부터 다시 돌림, EVL-08).
- 예외는 그대로 위로 올려 work_once의 재시도·needs_check 처리를 받는다.
"""
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .. import coding, config, db, evaluation as ev, llm


class EvalState(TypedDict, total=False):
    attempt_id: int
    att: dict
    criteria: dict
    versions: dict
    events: dict
    skip: bool                      # 이미 평가 있음(중복 방지)
    cancelled: bool
    blocks: list
    scores_by_q: dict               # question_id → {item: {score, quote, verified, rationale}}
    item_points: dict               # item → [점수…]
    coding_score: float | None
    item_scores: dict
    interview: float
    total: float
    result: str                     # AI(코드) 판정 pass|fail
    summary: dict
    code_feedback: str | None
    final_result: str | None        # decide 결과 (hybrid는 None)
    decided_at: Any


# ---------- 노드 ----------
def load(s: EvalState) -> EvalState:
    aid = s["attempt_id"]
    att = db.one("""SELECT a.*, ap.resume_snapshot, ap.job_id FROM attempts a JOIN applications ap ON ap.id=a.application_id
                    WHERE a.id=%s""", aid)
    if att is None or db.one("SELECT 1 FROM evaluations WHERE attempt_id=%s", aid):
        return {"skip": True}
    events = db.all_("SELECT type, count(*) n FROM attempt_events WHERE attempt_id=%s GROUP BY type", aid)
    return {"att": att, "criteria": att["criteria_snapshot"], "skip": False, "cancelled": att["status"] == "cancelled",
            "versions": {"model": llm.model_version(), **config.VERSIONS, "graph": "evaluation_graph-1"},
            "events": {e["type"]: e["n"] for e in events}, "blocks": ev.question_blocks(aid)}


def save_cancelled(s: EvalState) -> EvalState:
    """EVL-05: 부정행위 취소 → 평가 없이 불합격."""
    pub = {"result": "fail", "status": "응시 취소", "reason": "화면 이탈 2회로 응시가 취소되었습니다.", "strengths": [], "improvements": []}
    db.run("""INSERT INTO evaluations (attempt_id, item_scores, interview_score, coding_score, total_score, result, reason,
              criteria_snapshot, versions, public_feedback, summary, final_result, decided_at)
              VALUES (%s,%s,0,NULL,0,'fail','응시 취소(부정행위)',%s,%s,%s,%s,%s,%s)""",
           s["attempt_id"], db.J({it: 0 for it in ev.ITEMS}), db.J(s["criteria"]), db.J(s["versions"]), db.J(pub),
           "화면 이탈 2회로 응시 취소", *ev._auto_decision(s["criteria"], "fail"))
    return {}


def evaluate_blocks(s: EvalState) -> EvalState:
    """A4 채점: 질문 블록(기본 + 꼬리질문)마다 항목별 1~5점·인용. 인용이 답변 원문에 없으면 1회 재생성 → 그래도 없으면 1점."""
    scores_by_q, item_points = {}, {}
    for b in s["blocks"]:
        sc = ev.evaluate_block(b, s["att"]["resume_snapshot"])
        scores_by_q[b["q"]["id"]] = sc
        for it, v in sc.items():
            item_points.setdefault(it, []).append(v["score"])
    return {"scores_by_q": scores_by_q, "item_points": item_points}


def compute(s: EvalState) -> EvalState:
    """코드만: 항목 점수 → AI 면접 점수 → 코딩 반영 → 총점 → AI 판정."""
    crit = s["criteria"]
    coding_score = coding.grade(s["attempt_id"]) if crit["coding_enabled"] else None
    if crit["coding_enabled"] and coding_score is None:
        coding_score = 0.0
    item_scores, interview, total, result = ev.compute(s["item_points"], crit, coding_score)
    return {"coding_score": coding_score, "item_scores": item_scores, "interview": interview, "total": total, "result": result}


def summarize(s: EvalState) -> EvalState:
    summ = ev.summarize(s["blocks"], s["scores_by_q"], s["item_scores"])
    cfb = ev.code_feedback(s["attempt_id"]) if s["criteria"]["coding_enabled"] else None
    return {"summary": summ, "code_feedback": cfb}


def decide(s: EvalState) -> EvalState:
    """코드만: auto = AI 판정을 바로 확정, hybrid = 담당자 확인 대기(final_result NULL)."""
    final, at = ev._auto_decision(s["criteria"], s["result"])
    return {"final_result": final, "decided_at": at}


def save(s: EvalState) -> EvalState:
    att, crit, summ = s["att"], s["criteria"], s["summary"]
    status_ko = {"completed": "응시 완료", "terminated": "응시 종료(지원자 종료)"}.get(att["status"], att["status"])
    pub = {"result": s["result"], "status": status_ko, "strengths": summ["strengths"][:3], "improvements": summ["improvements"][:3],
           "notice": "이 피드백은 AI가 생성했으며, 근거가 된 답변 내용에 대해 설명을 요청할 수 있습니다."}
    with db.tx() as conn:
        for qid, sc in s["scores_by_q"].items():
            for it, v in sc.items():
                conn.execute("""INSERT INTO question_scores (question_id, eval_item, score, quote, verified, rationale)
                                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (question_id, eval_item) DO UPDATE
                                SET score=EXCLUDED.score, quote=EXCLUDED.quote, verified=EXCLUDED.verified, rationale=EXCLUDED.rationale""",
                             (qid, it, v["score"], v["quote"], v["verified"], v["rationale"]))
        conn.execute("""INSERT INTO evaluations (attempt_id, item_scores, interview_score, coding_score, total_score, result, reason,
                        criteria_snapshot, versions, strengths, improvements, summary, code_feedback, public_feedback,
                        final_result, decided_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                     (s["attempt_id"], db.J(s["item_scores"]), s["interview"], s["coding_score"], s["total"], s["result"],
                      f"종합 총점 {s['total']} {'≥' if s['result'] == 'pass' else '<'} 합격 기준 {crit['pass_score']}",
                      db.J(crit), db.J({**s["versions"], "events": s["events"]}),
                      db.J(summ["strengths"][:3]), db.J(summ["improvements"][:3]), summ["summary"], s["code_feedback"], db.J(pub),
                      s["final_result"], s["decided_at"]))
    return {}


# ---------- 엣지 ----------
def _after_load(s: EvalState) -> str:
    if s.get("skip"):
        return "end"
    return "cancelled" if s.get("cancelled") else "evaluate"


def build():
    g = StateGraph(EvalState)
    for name, fn in (("load", load), ("save_cancelled", save_cancelled), ("evaluate_blocks", evaluate_blocks),
                     ("compute", compute), ("summarize", summarize), ("decide", decide), ("save", save)):
        g.add_node(name, fn)
    g.add_edge(START, "load")
    g.add_conditional_edges("load", _after_load, {"end": END, "cancelled": "save_cancelled", "evaluate": "evaluate_blocks"})
    g.add_edge("save_cancelled", END)
    g.add_edge("evaluate_blocks", "compute")
    g.add_edge("compute", "summarize")
    g.add_edge("summarize", "decide")
    g.add_edge("decide", "save")
    g.add_edge("save", END)
    return g.compile()


GRAPH = build()


def run(attempt_id: int) -> dict:
    return GRAPH.invoke({"attempt_id": attempt_id}, {"run_name": "evaluation_graph", "tags": ["graph2"],
                                                     "metadata": {"attempt_id": attempt_id}})
