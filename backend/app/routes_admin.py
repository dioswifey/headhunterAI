"""Recruiter / operator routes (PRD 4.2, 4.9 RPT-01, 4.10 ADM-01~08)."""
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, db, evaluation, job_import, llm, security

router = APIRouter(prefix="/api/admin")
ops_router = APIRouter(prefix="/api/ops")

# JOB-02: 차별 소지 표현 (경고만, 게시 차단 없음)
BIAS = re.compile(r"(남성|여성|남자|여자|\d{2}\s*세\s*(이하|미만|이상)|나이|연령|미혼|기혼|용모|외모|신장|키\s*\d|병역필|군필|출신\s*학교|명문대|지방대|SKY|인서울|종교|흡연)")


def _company_job(job_id, user):
    j = db.one("SELECT * FROM jobs WHERE id=%s AND company_id=%s", job_id, user["company_id"])
    if not j:
        raise HTTPException(404, "공고를 찾을 수 없습니다.")  # ACC-06: 다른 회사 공고는 존재 여부도 숨김
    return j


class JobIn(BaseModel):
    title: str
    role_code: str = "ai_ax"
    company_intro: str = ""
    duties: str = ""
    requirements: str = ""
    preferred: str = ""
    employment_type: str = "정규직"
    location: str = ""
    headcount: int = 1
    open_date: date
    close_date: date


def _bias_warnings(body):
    hits = []
    for field in ("title", "company_intro", "duties", "requirements", "preferred"):
        for m in BIAS.finditer(getattr(body, field) or ""):
            hits.append({"field": field, "text": m.group(0)})
    return hits


@router.get("/jobs")
def jobs(user=Depends(security.recruiter)):
    return db.all_("""SELECT j.*, r.name role_name, ec.locked_at, ec.coding_enabled,
                      (SELECT count(*) FROM applications ap WHERE ap.job_id=j.id) applicants,
                      (SELECT count(*) FROM applications ap JOIN attempts a ON a.application_id=ap.id WHERE ap.job_id=j.id) attempts
                      FROM jobs j JOIN job_roles r ON r.code=j.role_code JOIN eval_criteria ec ON ec.job_id=j.id
                      WHERE j.company_id=%s ORDER BY j.created_at DESC""", user["company_id"])


@router.get("/jobs/{job_id}")
def job(job_id: int, user=Depends(security.recruiter)):
    j = _company_job(job_id, user)
    j["criteria"] = db.one("SELECT * FROM eval_criteria WHERE job_id=%s", job_id)
    j["applicants"] = db.one("SELECT count(*) n FROM applications WHERE job_id=%s", job_id)["n"]
    return j


def _check_role(code):
    r = db.one("SELECT * FROM job_roles WHERE code=%s", code)
    if not r or r["status"] != "active":
        raise HTTPException(400, "준비 중인 직무로는 공고를 만들 수 없습니다.")


@router.post("/jobs")
def create_job(body: JobIn, user=Depends(security.recruiter)):
    _check_role(body.role_code)
    if body.close_date < body.open_date:
        raise HTTPException(400, "마감일은 시작일 이후여야 합니다.")
    j = db.run("""INSERT INTO jobs (company_id, role_code, title, company_intro, duties, requirements, preferred, employment_type,
                  location, headcount, open_date, close_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
               user["company_id"], body.role_code, body.title, body.company_intro, body.duties, body.requirements, body.preferred,
               body.employment_type, body.location, body.headcount, body.open_date, body.close_date)
    db.run("INSERT INTO eval_criteria (job_id) VALUES (%s)", j["id"])
    return {"id": j["id"], "warnings": _bias_warnings(body)}


@router.put("/jobs/{job_id}")
def update_job(job_id: int, body: JobIn, user=Depends(security.recruiter)):
    j = _company_job(job_id, user)
    _check_role(body.role_code)
    if j["status"] == "closed":
        raise HTTPException(400, "마감된 공고는 수정할 수 없습니다.")
    db.run("""UPDATE jobs SET role_code=%s, title=%s, company_intro=%s, duties=%s, requirements=%s, preferred=%s, employment_type=%s,
              location=%s, headcount=%s, open_date=%s, close_date=%s, updated_at=now() WHERE id=%s""",
           body.role_code, body.title, body.company_intro, body.duties, body.requirements, body.preferred, body.employment_type,
           body.location, body.headcount, body.open_date, body.close_date, job_id)
    return {"ok": True, "warnings": _bias_warnings(body)}


@router.post("/jobs/{job_id}/publish")
def publish(job_id: int, user=Depends(security.recruiter)):
    j = _company_job(job_id, user)
    if not (j["title"] and j["duties"] and j["requirements"]):
        raise HTTPException(400, "제목·주요 업무·자격 요건을 채워야 게시할 수 있습니다.")
    db.run("UPDATE jobs SET status='published', updated_at=now() WHERE id=%s", job_id)
    db.audit(user["id"], "job_publish", "job", job_id)
    return {"ok": True}


@router.post("/jobs/{job_id}/close")
def close(job_id: int, user=Depends(security.recruiter)):
    _company_job(job_id, user)
    db.run("UPDATE jobs SET status='closed', updated_at=now() WHERE id=%s", job_id)
    db.audit(user["id"], "job_close", "job", job_id)
    return {"ok": True}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, user=Depends(security.recruiter)):
    _company_job(job_id, user)
    if db.one("SELECT 1 FROM applications WHERE job_id=%s", job_id):
        raise HTTPException(400, "지원자가 있는 공고는 삭제할 수 없고 마감만 할 수 있습니다.")
    db.run("DELETE FROM jobs WHERE id=%s", job_id)
    return {"ok": True}


class DraftIn(BaseModel):
    seniority: str
    focus: str
    stack: str
    location: str
    extra: str = ""


DRAFT_SCHEMA = {"type": "object", "properties": {k: {"type": "string"} for k in ("title", "company_intro", "duties", "requirements", "preferred")},
                "required": ["title", "company_intro", "duties", "requirements", "preferred"]}


@router.post("/jobs/draft-ai")
def draft(body: DraftIn, user=Depends(security.recruiter)):
    """JOB-02: AI 공고 초안. 담당자가 검토·편집 후 게시."""
    company = db.one("SELECT * FROM companies WHERE id=%s", user["company_id"])
    try:
        out = llm.chat_json(
            "너는 채용 공고 작성 도우미다. AI/AX 엔지니어 공고 초안을 한국어로 쓴다. 성별·나이·외모·출신 학교·혼인 등 차별 소지 조건은 넣지 않는다. "
            "duties·requirements·preferred는 줄마다 '- '로 시작하는 3~5줄 목록으로 쓴다.",
            f"회사: {company['name']}\n회사 소개: {company['intro']}\n경력 수준: {body.seniority}\n주요 업무 방향: {body.focus}\n"
            f"기술 스택: {body.stack}\n근무지: {body.location}\n추가: {body.extra}", DRAFT_SCHEMA, temperature=0.5)
    except llm.LLMError:
        raise HTTPException(503, "AI 초안 생성에 실패했습니다. 잠시 후 다시 시도하거나 직접 작성해 주세요.")
    return out


class ImportIn(BaseModel):
    text: str = ""
    url: str = ""


@router.post("/jobs/import")
def import_posting(body: ImportIn, user=Depends(security.recruiter)):
    """JOB-09 (PRD v0.8): 기존 공고 가져오기 — 붙여넣기 또는 링크. 원문을 칸에 옮겨 담기만 하고 내용을 지어내지 않는다."""
    try:
        return job_import.import_job(body.text, body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))


class CriteriaIn(BaseModel):
    w_expertise: int
    w_logic: int
    w_relevance: int
    w_delivery: int
    w_fit: int
    coding_enabled: bool
    coding_gate_mode: str = "none"
    pass_score: int
    decision_mode: str = "auto"


@router.put("/jobs/{job_id}/criteria")
def set_criteria(job_id: int, body: CriteriaIn, user=Depends(security.recruiter)):
    _company_job(job_id, user)
    c = db.one("SELECT * FROM eval_criteria WHERE job_id=%s", job_id)
    if c["locked_at"]:  # JOB-07
        raise HTTPException(409, "첫 응시가 시작되어 평가 기준이 잠겼습니다.")
    ws = [body.w_expertise, body.w_logic, body.w_relevance, body.w_delivery, body.w_fit]
    if sum(ws) != 100 or min(ws) < 5 or any(w % 5 for w in ws):
        raise HTTPException(400, "가중치는 5% 단위, 각 5% 이상, 합계 100%여야 합니다.")
    if body.coding_gate_mode not in ("none", "hard", "soft"):
        raise HTTPException(400, "코딩테스트 게이트는 없음·하드·소프트 중에서 선택할 수 있습니다.")
    if not 0 <= body.pass_score <= 100:
        raise HTTPException(400, "합격 기준은 0~100점입니다.")
    if body.decision_mode not in ("auto", "hybrid"):
        raise HTTPException(400, "합불 결정 방식은 자동 또는 하이브리드만 선택할 수 있습니다.")
    db.run("""UPDATE eval_criteria SET w_expertise=%s, w_logic=%s, w_relevance=%s, w_delivery=%s, w_fit=%s,
              coding_enabled=%s, coding_gate_mode=%s, pass_score=%s, decision_mode=%s, version=version+1 WHERE job_id=%s""",
           *ws, body.coding_enabled, body.coding_gate_mode, body.pass_score, body.decision_mode, job_id)
    db.audit(user["id"], "criteria_change", "job", job_id)
    return db.one("SELECT * FROM eval_criteria WHERE job_id=%s", job_id)


# ---------- 지원자 (ADM-01~06) ----------
APPLICANT_SQL = """SELECT ap.id application_id, ap.step, ap.created_at applied_at, u.name, u.email, u.phone, j.id job_id, j.title job_title,
    a.id attempt_id, a.status attempt_status, a.analysis_status, a.exit_count, a.interrupt_count, a.completed_at,
    e.interview_score, e.coding_score, e.total_score, e.result ai_result, e.final_result result, e.decided_at, ec.coding_enabled,
    coalesce(a.criteria_snapshot->>'decision_mode', ec.decision_mode) decision_mode,
    (e.attempt_id IS NOT NULL AND e.final_result IS NULL) needs_decision
    FROM applications ap JOIN users u ON u.id=ap.user_id JOIN jobs j ON j.id=ap.job_id JOIN eval_criteria ec ON ec.job_id=j.id
    LEFT JOIN attempts a ON a.application_id=ap.id LEFT JOIN evaluations e ON e.attempt_id=a.id
    WHERE j.company_id=%s"""


@router.get("/applicants")
def applicants(job_id: int | None = None, result: str | None = None, status: str | None = None, user=Depends(security.recruiter)):
    rows = db.all_(APPLICANT_SQL + """ AND (%s::int IS NULL OR j.id=%s) AND (%s::text IS NULL OR e.final_result=%s OR (%s='pending' AND e.attempt_id IS NOT NULL AND e.final_result IS NULL))
                   AND (%s::text IS NULL OR a.status=%s) ORDER BY e.total_score DESC NULLS LAST, ap.created_at DESC""",
                   user["company_id"], job_id, job_id, result, result, result, status, status)
    return rows


class DeleteIn(BaseModel):
    ids: list[int]


@router.post("/applicants/delete")
def applicants_delete(body: DeleteIn, user=Depends(security.recruiter)):
    """지원자 목록에서 선택·전체 삭제(2026-11-06 사용자 지시). 우리 회사 공고의 지원 건만 지운다.
    응시·답변·평가·코딩 제출·메모 등은 ON DELETE CASCADE로 같이 지워진다. 되돌릴 수 없음(화면에서 확인 창)."""
    ids = sorted({int(i) for i in body.ids})[:1000]
    if not ids:
        raise HTTPException(400, "삭제할 지원자를 고르세요.")
    rows = db.all_("""DELETE FROM applications ap USING jobs j WHERE ap.job_id=j.id AND j.company_id=%s AND ap.id = ANY(%s)
                      RETURNING ap.id""", user["company_id"], ids)
    for r in rows:
        db.audit(user["id"], "application.delete", "application", r["id"])
    return {"deleted": len(rows)}


def _company_application(app_id, user):
    row = db.one(APPLICANT_SQL + " AND ap.id=%s", user["company_id"], app_id)
    if not row:
        raise HTTPException(404, "지원자를 찾을 수 없습니다.")
    return row


@router.get("/applicants/{app_id}")
def applicant_detail(app_id: int, user=Depends(security.recruiter)):
    base = _company_application(app_id, user)
    ap = db.one("SELECT resume_snapshot, resume_edits, extraction_id FROM applications WHERE id=%s", app_id)
    out = {**base, "resume": ap["resume_snapshot"], "resume_edits": ap["resume_edits"] or []}
    if ap["extraction_id"]:
        x = db.one("""SELECT x.pages, x.ocr_engine, f.filename, f.id file_id FROM resume_extractions x JOIN files f ON f.id=x.file_id
                      WHERE x.id=%s""", ap["extraction_id"])
        if x:
            out["resume_file"] = {"file_id": x["file_id"], "filename": x["filename"], "engine": x["ocr_engine"],
                                  "pages": [{"page": p["page"], "method": p["method"], "confidence": p["confidence"]} for p in x["pages"]]}
    if base["attempt_id"]:
        aid = base["attempt_id"]
        qs = db.all_("SELECT * FROM attempt_questions WHERE attempt_id=%s ORDER BY seq, parent_id NULLS FIRST, id", aid)
        scores = db.all_("""SELECT qs.* FROM question_scores qs JOIN attempt_questions q ON q.id=qs.question_id WHERE q.attempt_id=%s""", aid)
        by_q = {}
        for s in scores:
            by_q.setdefault(s["question_id"], []).append(s)
        for q in qs:
            q["scores"] = by_q.get(q["id"], [])
        out["questions"] = qs
        out["evaluation"] = db.one("SELECT * FROM evaluations WHERE attempt_id=%s", aid)
        out["events"] = db.all_("SELECT type, detail, created_at FROM attempt_events WHERE attempt_id=%s ORDER BY created_at", aid)
        out["coding"] = db.all_("""SELECT p.title, p.level, s.code, s.passed, s.total, s.paste_blocked, s.submitted_at FROM coding_submissions s
                                   JOIN coding_problems p ON p.id=s.problem_id WHERE s.attempt_id=%s ORDER BY p.level DESC""", aid)
    out["memos"] = db.all_("""SELECT m.id, m.body, m.created_at, u.name author FROM memos m JOIN users u ON u.id=m.author_id
                              WHERE m.application_id=%s ORDER BY m.created_at DESC""", app_id)
    db.audit(user["id"], "result_view", "application", app_id)
    return out


class DecisionIn(BaseModel):
    action: str            # confirm = 허용(AI 판정 그대로 확정), override = 판정 변경 — 둘 다 원클릭
    note: str = ""         # 선택 입력(내부 기록)


@router.post("/applicants/{app_id}/decision")
def decide(app_id: int, body: DecisionIn, user=Depends(security.recruiter)):
    """hybrid 공고: 담당자는 '허용'(그대로 확정) 또는 '판정 변경'만 한다(원클릭). 점수는 바꾸지 않고 AI 판정(result)은 그대로 남는다.
    확정 성공 시 EML-07: decision_mail로 합불 통보 메일을 비동기 발송한다(베스트에포트, 실패해도 확정 자체는 유지)."""
    base = _company_application(app_id, user)
    if not base["attempt_id"]:
        raise HTTPException(404, "응시 기록이 없습니다.")
    e = db.one("SELECT result, final_result FROM evaluations WHERE attempt_id=%s", base["attempt_id"])
    if not e:
        raise HTTPException(409, "AI 분석이 끝난 뒤 확정할 수 있습니다.")
    if base["decision_mode"] != "hybrid":
        raise HTTPException(409, "자동 확정 공고는 담당자 확인 단계가 없습니다.")
    if e["final_result"]:
        raise HTTPException(409, "이미 확정된 결과입니다.")
    if body.action == "confirm":
        final, note = e["result"], None
    elif body.action == "override":
        final, note = ("fail" if e["result"] == "pass" else "pass"), (body.note.strip()[:1000] or "담당자 판정 변경")
    else:
        raise HTTPException(400, "잘못된 요청입니다.")
    db.run("""UPDATE evaluations SET final_result=%s, decided_by=%s, decided_at=now(), decision_note=%s
              WHERE attempt_id=%s AND final_result IS NULL""", final, user["id"], note, base["attempt_id"])
    db.audit(user["id"], "decision_" + body.action, "application", app_id)
    from . import decision_mail
    decision_mail.send_async(base["attempt_id"])
    return {"ok": True, "result": final}


@router.get("/files/{file_id}")
def file(file_id: int, user=Depends(security.recruiter)):
    f = db.one("""SELECT f.* FROM files f JOIN resume_extractions x ON x.file_id=f.id JOIN applications ap ON ap.extraction_id=x.id
                  JOIN jobs j ON j.id=ap.job_id WHERE f.id=%s AND j.company_id=%s""", file_id, user["company_id"])
    if not f:
        raise HTTPException(404)
    db.audit(user["id"], "file_view", "file", file_id)
    return FileResponse(f["storage_path"], media_type=f["mime"], filename=f["filename"])


class MemoIn(BaseModel):
    body: str


@router.post("/applicants/{app_id}/memos")
def add_memo(app_id: int, body: MemoIn, user=Depends(security.recruiter)):
    _company_application(app_id, user)
    if not body.body.strip():
        raise HTTPException(400, "메모 내용을 입력해 주세요.")
    db.run("INSERT INTO memos (application_id, author_id, body) VALUES (%s,%s,%s)", app_id, user["id"], body.body.strip()[:2000])
    db.audit(user["id"], "memo_add", "application", app_id)
    return {"ok": True}


@router.get("/compare")
def compare(ids: str, user=Depends(security.recruiter)):
    """ADM-03: 2~3명 대조, 결과 변경 없음."""
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()][:3]
    out = []
    for i in id_list:
        row = _company_application(i, user)
        e = db.one("SELECT item_scores, strengths, improvements FROM evaluations WHERE attempt_id=%s", row["attempt_id"]) if row["attempt_id"] else None
        out.append({**row, "item_scores": e["item_scores"] if e else None, "strengths": e["strengths"] if e else [], "improvements": e["improvements"] if e else []})
    return out


@router.get("/shortlist")
def shortlist(job_id: int | None = None, user=Depends(security.recruiter)):
    """ADM-06: 1차 합격자 + 삭제 예정일."""
    return db.all_("""SELECT ap.id application_id, u.name, u.email, u.phone, j.title job_title, e.total_score,
                      a.publish_at + interval '7 days' delete_at
                      FROM applications ap JOIN users u ON u.id=ap.user_id JOIN jobs j ON j.id=ap.job_id
                      JOIN attempts a ON a.application_id=ap.id JOIN evaluations e ON e.attempt_id=a.id
                      WHERE j.company_id=%s AND e.final_result='pass' AND (%s::int IS NULL OR j.id=%s)
                      ORDER BY e.total_score DESC""", user["company_id"], job_id, job_id)


@router.get("/stats")
def stats(user=Depends(security.recruiter)):
    cid = user["company_id"]
    monthly = db.all_("""SELECT to_char(a.started_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM') AS month, count(*) n
                         FROM attempts a JOIN applications ap ON ap.id=a.application_id JOIN jobs j ON j.id=ap.job_id
                         WHERE j.company_id=%s GROUP BY 1 ORDER BY 1""", cid)
    per_job = db.all_("""SELECT j.id, j.title, count(e.attempt_id) evaluated,
                         count(*) FILTER (WHERE e.final_result='pass') passed, round(avg(e.total_score), 2) avg_total
                         FROM jobs j LEFT JOIN applications ap ON ap.job_id=j.id LEFT JOIN attempts a ON a.application_id=ap.id
                         LEFT JOIN evaluations e ON e.attempt_id=a.id WHERE j.company_id=%s GROUP BY j.id ORDER BY j.id DESC""", cid)
    counts = db.one("""SELECT count(*) FILTER (WHERE a.interrupt_count>0) interrupted, count(*) FILTER (WHERE a.status='cancelled') cancelled,
                       count(*) FILTER (WHERE ap.extraction_id IS NOT NULL) ocr_used,
                       count(*) FILTER (WHERE x.status='failed') ocr_failed
                       FROM applications ap JOIN jobs j ON j.id=ap.job_id LEFT JOIN attempts a ON a.application_id=ap.id
                       LEFT JOIN resume_extractions x ON x.id=ap.extraction_id WHERE j.company_id=%s""", cid)
    return {"monthly": monthly, "per_job": per_job, "counts": counts}


@router.get("/notifications")
def notifications(user=Depends(security.recruiter)):
    return db.all_("SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 30", user["id"])


# ---------- 운영 (EVL-08 확인 필요 → 재처리) ----------
@ops_router.get("/analysis")
def needs_check(user=Depends(security.operator)):
    return db.all_("SELECT * FROM analysis_jobs WHERE status <> 'done' ORDER BY created_at")


@ops_router.post("/analysis/{attempt_id}/retry")
def retry(attempt_id: int, user=Depends(security.operator)):
    db.run("UPDATE analysis_jobs SET status='queued', tries=0, created_at=now(), next_run_at=now() WHERE attempt_id=%s", attempt_id)
    db.run("UPDATE attempts SET analysis_status='queued' WHERE id=%s", attempt_id)
    db.audit(user["id"], "analysis_retry", "attempt", attempt_id)
    return {"ok": True}
