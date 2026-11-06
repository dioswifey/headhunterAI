"""Applicant routes (PRD 4.3~4.7, 4.9, 5장)."""
import os
import re
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import coding, config, db, evaluation, llm, ocr, questions, report_mail, scheduler, security, speech

router = APIRouter(prefix="/api")
KST = ZoneInfo("Asia/Seoul")
CONSENT_ITEMS = ["data", "ai", "share", "retention", "rules"]


def _own_app(app_id, user):
    a = db.one("SELECT * FROM applications WHERE id=%s AND user_id=%s", app_id, user["id"])
    if not a:
        raise HTTPException(404, "지원 내역을 찾을 수 없습니다.")
    return a


def _own_attempt(attempt_id, user):
    a = db.one("""SELECT a.*, ap.user_id, ap.job_id FROM attempts a JOIN applications ap ON ap.id=a.application_id
                  WHERE a.id=%s AND ap.user_id=%s""", attempt_id, user["id"])
    if not a:
        raise HTTPException(404, "응시 정보를 찾을 수 없습니다.")
    return a


# ---------- 지원 ----------
class Apply(BaseModel):
    job_id: int


@router.post("/applications")
def apply(body: Apply, user=Depends(security.applicant)):
    if not user["email_verified"]:
        raise HTTPException(403, "이메일 인증 후 지원할 수 있습니다.")
    job = db.one("""SELECT j.*, r.status role_status FROM jobs j JOIN job_roles r ON r.code=j.role_code WHERE j.id=%s""", body.job_id)
    if not job or job["status"] != "published" or job["role_status"] != "active":
        raise HTTPException(400, "지원할 수 없는 공고입니다.")
    if job["close_date"] < datetime.now(KST).date():
        raise HTTPException(400, "접수가 마감된 공고입니다.")
    existing = db.one("SELECT id FROM applications WHERE job_id=%s AND user_id=%s", body.job_id, user["id"])
    if existing:  # APP-02 중복 지원 차단 → 기존 지원 이어서 진행
        return {"id": existing["id"], "existing": True}
    a = db.run("INSERT INTO applications (job_id, user_id) VALUES (%s,%s) RETURNING id", body.job_id, user["id"])
    for r in db.all_("SELECT id FROM users WHERE company_id=%s AND role='recruiter'", job["company_id"]):
        db.run("INSERT INTO notifications (user_id, type, title, body) VALUES (%s,'apply','신규 지원',%s)", r["id"], f"[{job['title']}]에 새 지원자가 있습니다.")
    return {"id": a["id"], "existing": False}


@router.get("/me/applications")
def my_applications(user=Depends(security.applicant)):
    rows = db.all_("""SELECT ap.id, ap.step, ap.created_at, j.id job_id, j.title, j.close_date, c.name company, ec.coding_enabled,
                      a.id attempt_id, a.status attempt_status, a.analysis_status, a.publish_at,
                      (e.final_result IS NOT NULL) result_open, coalesce(a.criteria_snapshot->>'decision_mode', ec.decision_mode) decision_mode
                      FROM applications ap JOIN jobs j ON j.id=ap.job_id JOIN companies c ON c.id=j.company_id
                      JOIN eval_criteria ec ON ec.job_id=j.id
                      LEFT JOIN attempts a ON a.application_id=ap.id LEFT JOIN evaluations e ON e.attempt_id=a.id
                      WHERE ap.user_id=%s ORDER BY ap.created_at DESC""", user["id"])
    for r in rows:
        r["result_date"] = (r["close_date"] + timedelta(days=1)).isoformat()
    return rows


@router.get("/applications/{app_id}")
def application(app_id: int, user=Depends(security.applicant)):
    a = _own_app(app_id, user)
    job = db.one("""SELECT j.id, j.title, j.close_date, c.name company, ec.coding_enabled FROM jobs j JOIN companies c ON c.id=j.company_id
                    JOIN eval_criteria ec ON ec.job_id=j.id WHERE j.id=%s""", a["job_id"])
    att = db.one("SELECT id, status FROM attempts WHERE application_id=%s", app_id)
    consent = db.one("SELECT version, agreed_at FROM consents WHERE application_id=%s AND withdrawn_at IS NULL ORDER BY id DESC LIMIT 1", app_id)
    return {"id": a["id"], "step": a["step"], "job": job, "attempt": att, "consent": consent, "resume_snapshot": a["resume_snapshot"],
            "pregen_status": a["pregen_status"], "result_date": (job["close_date"] + timedelta(days=1)).isoformat()}


# ---------- 동의 (APP-03) ----------
class Consent(BaseModel):
    items: dict


@router.post("/applications/{app_id}/consent")
def consent(app_id: int, body: Consent, user=Depends(security.applicant)):
    a = _own_app(app_id, user)
    if not all(body.items.get(k) is True for k in CONSENT_ITEMS):
        raise HTTPException(400, "필수 항목에 모두 동의해야 다음 단계로 진행할 수 있습니다.")
    db.run("INSERT INTO consents (user_id, application_id, scope, version, items) VALUES (%s,%s,'application',%s,%s)",
           user["id"], app_id, config.CONSENT_VERSION, db.J(body.items))
    if a["step"] == "consent":
        db.run("UPDATE applications SET step='resume' WHERE id=%s", app_id)
    db.audit(user["id"], "consent", "application", app_id)
    return {"ok": True, "version": config.CONSENT_VERSION}


@router.delete("/applications/{app_id}/consent")
def withdraw(app_id: int, user=Depends(security.applicant)):
    """동의 철회 → 진행 중 응시 취소, 데이터 즉시 삭제."""
    _own_app(app_id, user)
    db.run("DELETE FROM applications WHERE id=%s", app_id)
    db.audit(user["id"], "consent_withdraw_delete", "application", app_id)
    return {"ok": True}


# ---------- 이력서 업로드·OCR (4.4) ----------
@router.post("/resume/uploads")
async def upload(file: UploadFile = File(...), user=Depends(security.applicant)):
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "파일은 10MB 이하만 올릴 수 있습니다.")
    mime = ocr.sniff_mime(data[:8])
    if not mime:
        raise HTTPException(415, "PDF·JPG·PNG 파일만 인식할 수 있습니다. PDF로 변환하거나 직접 작성해 주세요.")
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    path = os.path.join(config.UPLOAD_DIR, uuid.uuid4().hex)
    with open(path, "wb") as f:
        f.write(data)
    if mime == "application/pdf":
        try:
            safe = ocr.pdf_is_safe(path)
        except Exception:
            safe = False
        if not safe:
            os.remove(path)
            raise HTTPException(400, "열 수 없거나 5쪽을 넘는 PDF, 또는 스크립트·첨부 파일이 포함된 PDF는 올릴 수 없습니다.")
    f = db.run("""INSERT INTO files (owner_id, kind, filename, mime, size_bytes, storage_path, expires_at)
                  VALUES (%s,'resume_original',%s,%s,%s,%s, now() + interval '24 hours') RETURNING id""",
               user["id"], (file.filename or "resume")[:200], mime, len(data), path)
    x = db.run("INSERT INTO resume_extractions (user_id, file_id) VALUES (%s,%s) RETURNING id", user["id"], f["id"])
    ocr.start(x["id"], path, mime)
    return {"extraction_id": x["id"]}


@router.get("/resume/extractions/{xid}")
def extraction(xid: int, user=Depends(security.applicant)):
    x = db.one("""SELECT x.id, x.status, x.stage, x.progress, x.pages, x.structured, x.failure_reason, f.filename, f.mime
                  FROM resume_extractions x JOIN files f ON f.id=x.file_id WHERE x.id=%s AND x.user_id=%s""", xid, user["id"])
    if not x:
        raise HTTPException(404, "인식 작업을 찾을 수 없습니다.")
    return x


@router.get("/resume/extractions/{xid}/file")
def extraction_file(xid: int, user=Depends(security.applicant)):
    f = db.one("""SELECT f.* FROM resume_extractions x JOIN files f ON f.id=x.file_id WHERE x.id=%s AND x.user_id=%s""", xid, user["id"])
    if not f:
        raise HTTPException(404)
    return FileResponse(f["storage_path"], media_type=f["mime"])


class ResumeConfirm(BaseModel):
    data: dict
    extraction_id: int | None = None
    save_title: str | None = None


def _diff_edits(auto, final):
    """OCR-05: 자동 추출값 대비 지원자 수정 필드."""
    edits = []
    if not auto:
        return edits
    for k in ("name", "email", "phone"):
        if (auto.get("basic", {}).get(k) or "") != (final.get("basic", {}).get(k) or ""):
            edits.append(f"basic.{k}")
    for sec in ocr.SECTIONS:
        a, b = auto.get(sec, []), final.get(sec, [])
        for i in range(max(len(a), len(b))):
            ai = {k: v for k, v in (a[i] if i < len(a) else {}).items() if k not in ("needs_check", "origin")}
            bi = {k: v for k, v in (b[i] if i < len(b) else {}).items() if k not in ("needs_check", "origin")}
            if ai != bi:
                edits.append(f"{sec}[{i}]")
    if sorted(auto.get("skills", [])) != sorted(final.get("skills", [])):
        edits.append("skills")
    return edits


@router.post("/applications/{app_id}/resume")
def confirm_resume(app_id: int, body: ResumeConfirm, user=Depends(security.applicant)):
    a = _own_app(app_id, user)
    if a["step"] in ("consent",):
        raise HTTPException(400, "먼저 동의를 완료해 주세요.")
    if a["step"] == "started":
        raise HTTPException(400, "응시가 시작된 뒤에는 이력서를 바꿀 수 없습니다.")
    d = body.data
    d.setdefault("basic", {})
    for sec in ocr.SECTIONS:
        d[sec] = [{k: v for k, v in it.items() if k != "needs_check"} for it in d.get(sec, []) if any(str(v).strip() for k, v in it.items() if k not in ("page", "origin", "needs_check", "source"))]
    d["skills"] = [s for s in d.get("skills", []) if str(s).strip()]
    if not d["basic"].get("name", "").strip() or not (d.get("education") or d.get("career")):
        raise HTTPException(400, "성명과 학력 또는 경력 1건 이상은 필수입니다.")
    auto = None
    if body.extraction_id:
        x = db.one("SELECT * FROM resume_extractions WHERE id=%s AND user_id=%s", body.extraction_id, user["id"])
        if not x:
            raise HTTPException(404, "인식 작업을 찾을 수 없습니다.")
        auto = x["structured"]
        db.run("UPDATE resume_extractions SET status='confirmed', expires_at=now() + interval '3650 days' WHERE id=%s", x["id"])
        db.run("UPDATE files SET expires_at=NULL WHERE id=%s", x["file_id"])
    edits = _diff_edits(auto, d)
    db.run("""UPDATE applications SET resume_snapshot=%s, resume_edits=%s, extraction_id=%s,
              step=CASE WHEN step IN ('resume','env','ready') THEN 'env' ELSE step END, pregen_status='none', pregen_questions=NULL WHERE id=%s""",
           db.J(d), db.J(edits), body.extraction_id, app_id)
    if body.save_title:
        db.run("INSERT INTO resumes (user_id, title, data) VALUES (%s,%s,%s)", user["id"], body.save_title[:100], db.J(d))
    return {"ok": True, "edits": edits}


@router.get("/me/resumes")
def my_resumes(user=Depends(security.applicant)):
    return db.all_("SELECT id, title, data, updated_at FROM resumes WHERE user_id=%s ORDER BY updated_at DESC", user["id"])


class ResumeSave(BaseModel):
    title: str
    data: dict


@router.post("/me/resumes")
def save_resume(body: ResumeSave, user=Depends(security.applicant)):
    return db.run("INSERT INTO resumes (user_id, title, data) VALUES (%s,%s,%s) RETURNING id", user["id"], body.title[:100], db.J(body.data))


@router.delete("/me/resumes/{rid}")
def delete_resume(rid: int, user=Depends(security.applicant)):
    db.run("DELETE FROM resumes WHERE id=%s AND user_id=%s", rid, user["id"])
    return {"ok": True}


# ---------- 환경 점검 (APP-06) ----------
class EnvCheck(BaseModel):
    mic: bool
    cam: bool
    speaker: bool
    network: bool
    ai: bool


@router.post("/applications/{app_id}/env")
def env_check(app_id: int, body: EnvCheck, user=Depends(security.applicant)):
    a = _own_app(app_id, user)
    # 임시(2026-10-29, 사용자 결정): 카메라는 없어도 통과. 원래 APP-06은 카메라 필수. 카메라 없이 통과하면 감사 로그 action=env_check_no_camera
    if not all(v for k, v in body.model_dump().items() if k != "cam"):
        raise HTTPException(400, "모든 점검 항목을 통과해야 시작할 수 있습니다.")
    db.audit(user["id"], "env_check" if body.cam else "env_check_no_camera", "application", app_id)
    if a["step"] not in ("env", "ready"):
        raise HTTPException(400, "이력서 확인을 먼저 완료해 주세요.")
    db.run("UPDATE applications SET step='ready' WHERE id=%s", app_id)
    if a["pregen_status"] in ("none", "failed"):
        questions.start_pregen(app_id)  # RSQ-05: 점검 단계에서 미리 생성
    return {"ok": True}


# ---------- 면접 (4.5, 5장) ----------
def _publish_at(close_date):
    return datetime.combine(close_date + timedelta(days=1), time(10, 0), KST)


@router.post("/applications/{app_id}/attempt")
def start_attempt(app_id: int, user=Depends(security.applicant)):
    a = _own_app(app_id, user)
    if a["step"] != "ready":
        raise HTTPException(400, "동의·이력서 확인·환경 점검을 모두 마쳐야 시작할 수 있습니다.")
    if db.one("SELECT 1 FROM attempts WHERE application_id=%s", app_id):
        raise HTTPException(409, "이 공고는 이미 응시했습니다. 지원 건당 1회만 응시할 수 있습니다.")
    job = db.one("SELECT * FROM jobs WHERE id=%s", a["job_id"])
    if job["close_date"] < datetime.now(KST).date():
        raise HTTPException(400, "접수가 마감되어 응시를 시작할 수 없습니다.")
    active = db.one("SELECT count(*) n FROM attempts WHERE status IN ('interview','coding')")["n"]
    if active >= config.MAX_CONCURRENT_INTERVIEWS:  # INT-08, 응시 횟수 미차감
        raise HTTPException(503, "지금은 응시 인원이 많아 시작할 수 없습니다. 잠시 후 마이페이지에서 다시 시작해 주세요.")
    qs = a["pregen_questions"] if a["pregen_status"] == "ready" and a["pregen_questions"] else questions.build_set(a, use_llm=False)
    with db.tx() as conn:
        conn.execute("UPDATE eval_criteria SET locked_at=coalesce(locked_at, now()) WHERE job_id=%s", (a["job_id"],))
        crit = conn.execute("SELECT * FROM eval_criteria WHERE job_id=%s", (a["job_id"],)).fetchone()
        crit_snap = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in crit.items()}
        att = conn.execute("""INSERT INTO attempts (application_id, status, phase, criteria_version, criteria_snapshot, started_at, last_heartbeat, publish_at)
                              VALUES (%s,'interview','interview',%s,%s,now(),now(),%s) RETURNING id""",
                           (app_id, crit["version"], db.J(crit_snap), _publish_at(job["close_date"]))).fetchone()
        for i, q in enumerate(qs, start=1):
            conn.execute("""INSERT INTO attempt_questions (attempt_id, seq, category, eval_items, text, bank_id, resume_ref, gen_method, time_limit)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                         (att["id"], i, q["category"], q["eval_items"], q["text"], q.get("bank_id"), db.J(q.get("resume_ref")),
                          q.get("gen_method", "db"), config.QUESTION_SECONDS))
            if q.get("bank_id"):
                conn.execute("UPDATE question_bank SET use_count=use_count+1 WHERE id=%s", (q["bank_id"],))
        conn.execute("UPDATE applications SET step='started' WHERE id=%s", (app_id,))
        conn.execute("INSERT INTO attempt_events (attempt_id, type, detail) VALUES (%s,'started',%s)",
                     (att["id"], db.J({"pregen": a["pregen_status"]})))
    speech.prewarm([q["text"] for q in qs])   # v0.9 INT-01: 기본 질문 음성(Qwen3-TTS)을 미리 만든다
    return {"attempt_id": att["id"]}


def _current_question(attempt_id):
    return db.one("""SELECT * FROM attempt_questions WHERE attempt_id=%s AND answer_status='pending'
                     ORDER BY seq, parent_id NULLS FIRST, id LIMIT 1""", attempt_id)


def _state(att):
    st = {"attempt_id": att["id"], "application_id": att["application_id"], "status": att["status"], "phase": att["phase"], "exit_count": att["exit_count"],
          "publish_at": att["publish_at"].isoformat() if att["publish_at"] else None, "stt": speech.enabled()}
    if att["status"] == "interview" or (att["status"] == "interrupted" and att["phase"] == "interview"):
        q = _current_question(att["id"])
        if q:
            st["question"] = {"id": q["id"], "seq": q["seq"], "text": q["text"], "time_limit": q["time_limit"],
                              "followup": q["parent_id"] is not None, "category": q["category"]}
        st["total"] = 10
    if att["phase"] == "coding":
        if att["status"] == "interrupted":
            st["remaining"] = att["coding_remaining"]
        elif att["coding_deadline"]:
            st["remaining"] = max(0, int((att["coding_deadline"] - security.now()).total_seconds()))
    return st


@router.get("/attempts/{attempt_id}")
def attempt_state(attempt_id: int, user=Depends(security.applicant)):
    att = _own_attempt(attempt_id, user)
    if att["status"] in ("interview", "coding"):
        if att["user_exit_at"]:  # 새로고침·뒤로가기 등 임의 종료 후 다시 열림 → 응시 종료 (RES-04)
            scheduler.finish_attempt(att["id"], "terminated", "user_exit_reload")
        elif att["last_heartbeat"] and att["last_heartbeat"] < security.now() - timedelta(seconds=7):
            # 종료 조작 없이 끊겼다가 돌아옴 → 장애 판정, 이어가기 (5.1)
            scheduler.heartbeat_monitor_one(att)
        att = _own_attempt(attempt_id, user)
    return _state(att)


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


def _maybe_followup(att, q, answer):
    """INT-06: 질문당 1개, 전체 4개, 5초 안에 판단 못 하면 생략."""
    if q["parent_id"] is not None or att["followups_used"] >= config.MAX_FOLLOWUPS:
        return None
    if evaluation.NO_CONTENT.match(answer) or len(answer.strip()) < 5:
        return None  # INT-07
    ref = ""
    if q["resume_ref"] and q["resume_ref"].get("quote"):
        ref = f"\n근거 이력서 항목: {q['resume_ref']['quote']}"
    try:
        r = llm.chat_json(PRE_SYSTEM, f"질문: {q['text']}{ref}\n답변: {answer[:1500]}", PRE_SCHEMA, timeout=config.FOLLOWUP_TIMEOUT)
    except llm.LLMError:
        return None
    trigger = (not r["addresses_question"]) or r["logic"] <= 2 or r["relevance"] <= 2 or r["inconsistent_with_resume"] or r["generic_only"]
    fq = (r.get("followup_question") or "").strip()
    if not trigger or not fq or questions.BLOCKLIST.search(fq) or len(fq) > 150:
        return None
    reason = "논리성·관련성 2점 이하" if (not r["addresses_question"] or r["logic"] <= 2 or r["relevance"] <= 2) else ("이력서와 다름" if r["inconsistent_with_resume"] else "일반론")
    return fq, reason


class Answer(BaseModel):
    question_id: int
    text: str


def _answer_target(attempt_id, question_id, user):
    att = _own_attempt(attempt_id, user)
    if att["status"] != "interview":
        raise HTTPException(409, "지금은 답변을 제출할 수 없습니다.")
    q = _current_question(attempt_id)
    if not q or q["id"] != question_id:
        raise HTTPException(409, "현재 질문이 아닙니다.")
    return att, q


@router.post("/attempts/{attempt_id}/answer")
def answer(attempt_id: int, body: Answer, user=Depends(security.applicant)):
    att, q = _answer_target(attempt_id, body.question_id, user)
    return _save_answer(att, q, body.text.strip(), "typed" if body.text.strip() else None, user)


@router.post("/attempts/{attempt_id}/answer-audio")
async def answer_audio(attempt_id: int, question_id: int = Form(...), text: str = Form(""), audio: UploadFile = File(None),
                       user=Depends(security.applicant)):
    """v0.9 INT-03: 답변 녹음 → Qwen3-ASR로 글로 바꿔 저장. 음성 파일은 저장하지 않는다.
    음성 인식이 실패하면 비상 입력란 글만 쓴다(흐름은 멈추지 않음)."""
    att, q = _answer_target(attempt_id, question_id, user)
    typed = (text or "").strip()
    heard, stt_error = "", None
    if audio is not None:
        raw = await audio.read(20 * 1024 * 1024 + 1)
        if raw and len(raw) <= 20 * 1024 * 1024:
            try:
                heard = speech.transcribe(raw)
            except speech.SpeechError as e:
                stt_error = str(e)[:200]
        del raw
    full = "\n".join(x for x in (heard, typed) if x)
    source = "voice+typed" if (heard and typed) else ("voice" if heard else ("typed" if typed else None))
    if stt_error:
        db.run("INSERT INTO attempt_events (attempt_id, type, detail) VALUES (%s,'stt_failed',%s)", attempt_id, db.J({"question_id": q["id"], "error": stt_error}))
    return _save_answer(att, q, full, source, user)


@router.get("/attempts/{attempt_id}/questions/{question_id}/audio")
def question_audio(attempt_id: int, question_id: int, user=Depends(security.applicant)):
    """v0.9 INT-01: 질문 음성(Qwen3-TTS, 캐시). 실패하면 503 → 화면이 브라우저 내장 음성으로 읽는다."""
    _own_attempt(attempt_id, user)
    q = db.one("SELECT text FROM attempt_questions WHERE id=%s AND attempt_id=%s", question_id, attempt_id)
    if not q:
        raise HTTPException(404, "질문을 찾을 수 없습니다.")
    try:
        data = speech.synthesize(q["text"])
    except speech.SpeechError:
        raise HTTPException(503, "질문 음성을 만들 수 없습니다.")
    return Response(data, media_type="audio/wav", headers={"cache-control": "private, max-age=3600"})


def _save_answer(att, q, text, source, user):
    attempt_id = att["id"]
    db.run("UPDATE attempt_questions SET answer_text=%s, answer_status=%s, answer_source=%s, answered_at=now() WHERE id=%s",
           text or None, "answered" if text else "no_answer", source, q["id"])
    if text:
        fu = _maybe_followup(att, q, text)
        if fu:
            db.run("""INSERT INTO attempt_questions (attempt_id, seq, parent_id, category, eval_items, text, gen_method, time_limit)
                      VALUES (%s,%s,%s,'followup',%s,%s,'llm',%s)""", attempt_id, q["seq"], q["id"], q["eval_items"], fu[0], config.FOLLOWUP_SECONDS)
            db.run("UPDATE attempt_questions SET prescore=%s WHERE id=%s", db.J({"followup_reason": fu[1]}), q["id"])
            db.run("UPDATE attempts SET followups_used=followups_used+1 WHERE id=%s", attempt_id)
            speech.prewarm([fu[0]])
    if not _current_question(attempt_id):  # 기본 10문항 + 꼬리질문 모두 끝남
        crit = att["criteria_snapshot"]
        if crit["coding_enabled"]:
            coding.assign_problems(attempt_id)
            db.run(f"""UPDATE attempts SET status='coding', phase='coding', coding_deadline=now() + interval '{config.CODING_MINUTES} minutes'
                       WHERE id=%s""", attempt_id)
        else:
            scheduler.finish_attempt(attempt_id, "completed", "interview_done")
    return _state(_own_attempt(attempt_id, user))


@router.post("/attempts/{attempt_id}/heartbeat")
def heartbeat(attempt_id: int, user=Depends(security.applicant)):
    att = _own_attempt(attempt_id, user)
    if att["status"] in ("interview", "coding"):
        db.run("UPDATE attempts SET last_heartbeat=now() WHERE id=%s", attempt_id)
    return {"status": att["status"]}


class Event(BaseModel):
    type: str          # exit | user_exit | paste_block | offline | device_error
    detail: dict = {}


@router.post("/attempts/{attempt_id}/events")
def event(attempt_id: int, body: Event, user=Depends(security.applicant)):
    att = _own_attempt(attempt_id, user)
    active = att["status"] in ("interview", "coding")
    db.run("INSERT INTO attempt_events (attempt_id, type, detail) VALUES (%s,%s,%s)", attempt_id, body.type[:30], db.J(body.detail))
    if body.type == "user_exit" and active:
        db.run("UPDATE attempts SET user_exit_at=now() WHERE id=%s", attempt_id)
    elif body.type == "paste_block" and body.detail.get("problem_id"):
        db.run("UPDATE coding_submissions SET paste_blocked=paste_blocked+1 WHERE attempt_id=%s AND problem_id=%s", attempt_id, body.detail["problem_id"])
    elif body.type == "exit" and active:  # 5.2 화면 이탈
        n = db.run("UPDATE attempts SET exit_count=exit_count+1 WHERE id=%s RETURNING exit_count", attempt_id)["exit_count"]
        if n >= 2:
            scheduler.finish_attempt(attempt_id, "cancelled", "screen_exit_2")
            return {"action": "cancelled"}
        return {"action": "warn", "exit_count": n}
    return {"action": "none"}


@router.post("/attempts/{attempt_id}/resume")
def resume(attempt_id: int, user=Depends(security.applicant)):
    """RES-02: 이어가기 — 끊긴 질문을 새 답변 시간으로, 코딩은 남은 시간 유지."""
    att = _own_attempt(attempt_id, user)
    if att["status"] != "interrupted":
        raise HTTPException(409, "이어갈 수 있는 상태가 아닙니다.")
    if att["phase"] == "coding":
        db.run("""UPDATE attempts SET status='coding', last_heartbeat=now(), user_exit_at=NULL,
                  coding_deadline=now() + make_interval(secs => %s) WHERE id=%s""", att["coding_remaining"] or 0, attempt_id)
    else:
        db.run("UPDATE attempts SET status='interview', last_heartbeat=now(), user_exit_at=NULL WHERE id=%s", attempt_id)
    db.run("INSERT INTO attempt_events (attempt_id, type, detail) VALUES (%s,'resumed','{}')", attempt_id)
    return _state(_own_attempt(attempt_id, user))


# ---------- 코딩테스트 (4.7) ----------
@router.get("/attempts/{attempt_id}/coding")
def coding_view(attempt_id: int, user=Depends(security.applicant)):
    att = _own_attempt(attempt_id, user)
    if att["phase"] != "coding":
        raise HTTPException(409, "코딩테스트 단계가 아닙니다.")
    subs = db.all_("""SELECT s.problem_id, s.code, s.saved_at, p.level, p.title, p.statement, p.tests, p.func_name
                      FROM coding_submissions s JOIN coding_problems p ON p.id=s.problem_id WHERE s.attempt_id=%s ORDER BY p.level DESC""", attempt_id)
    for s in subs:
        s["examples"] = [{"input": t["args"], "expected": t["expected"]} for t in s.pop("tests") if t.get("public")]
    return {"problems": subs, **_state(att)}


class Code(BaseModel):
    code: str


def _coding_open(att):
    if att["status"] != "coding":
        raise HTTPException(409, "코딩테스트가 진행 중이 아닙니다.")


@router.post("/attempts/{attempt_id}/coding/{problem_id}/save")
def coding_save(attempt_id: int, problem_id: int, body: Code, user=Depends(security.applicant)):
    _coding_open(_own_attempt(attempt_id, user))
    db.run("UPDATE coding_submissions SET code=%s, saved_at=now() WHERE attempt_id=%s AND problem_id=%s", body.code[:20000], attempt_id, problem_id)
    return {"ok": True}


@router.post("/attempts/{attempt_id}/coding/{problem_id}/run")
def coding_run(attempt_id: int, problem_id: int, body: Code, user=Depends(security.applicant)):
    _coding_open(_own_attempt(attempt_id, user))
    db.run("UPDATE coding_submissions SET code=%s, saved_at=now() WHERE attempt_id=%s AND problem_id=%s", body.code[:20000], attempt_id, problem_id)
    p = db.one("SELECT * FROM coding_problems WHERE id=%s", problem_id)
    return coding.run_public(p, body.code)


@router.post("/attempts/{attempt_id}/coding/submit")
def coding_submit(attempt_id: int, user=Depends(security.applicant)):
    att = _own_attempt(attempt_id, user)
    _coding_open(att)
    db.run("UPDATE coding_submissions SET submitted_at=now() WHERE attempt_id=%s", attempt_id)
    scheduler.finish_attempt(attempt_id, "completed", "coding_submitted")
    return _state(_own_attempt(attempt_id, user))


# ---------- 무제한 코딩 연습 (Features '무제한 코딩 연습', 2026-11-05) ----------
# 공고·응시와 무관. 게시된 문제(coding_problems)만, 횟수 제한 없음, 기록 저장 없음. 비공개 테스트는 통과 수만 알려 준다
@router.get("/practice/coding")
def practice_list(user=Depends(security.applicant)):
    return db.all_("""SELECT p.id, p.title, p.level, (g.id IS NOT NULL) AS generated
                      FROM coding_problems p LEFT JOIN generated_problems g ON g.published_problem_id=p.id
                      ORDER BY p.level, p.id""")


def _practice_problem(pid):
    p = db.one("SELECT * FROM coding_problems WHERE id=%s", pid)
    if not p:
        raise HTTPException(404, "문제를 찾을 수 없습니다.")
    return p


@router.get("/practice/coding/{pid}")
def practice_view(pid: int, user=Depends(security.applicant)):
    p = _practice_problem(pid)
    return {"id": p["id"], "title": p["title"], "level": p["level"], "statement": p["statement"], "func_name": p["func_name"],
            "starter": p["starter"], "examples": [{"input": t["args"], "expected": t["expected"]} for t in p["tests"] if t.get("public")]}


@router.post("/practice/coding/{pid}/run")
def practice_run(pid: int, body: Code, user=Depends(security.applicant)):
    return coding.run_public(_practice_problem(pid), body.code[:20000])


@router.post("/practice/coding/{pid}/submit")
def practice_submit(pid: int, body: Code, user=Depends(security.applicant)):
    p = _practice_problem(pid)
    tests = p["tests"]
    res = coding.sandbox_run(body.code[:20000], p["func_name"], tests, hide_hidden=True)
    results = res.get("results", [])
    passed = sum(1 for r in results if r.get("ok"))
    pub = [{"input": t["args"], "expected": t["expected"], **r} for t, r in zip(tests, results) if t.get("public")]
    hidden = [r for t, r in zip(tests, results) if not t.get("public")]
    return {"status": res.get("status"), "stderr": res.get("stderr", ""), "passed": passed, "total": len(tests),
            "public": pub, "hidden_passed": sum(1 for r in hidden if r.get("ok")), "hidden_total": len(hidden)}


# ---------- SQL 연습 (SQLD 유형 4지선다, 2026-11-06) ----------
# 게시된(sql_problems.status='published') 문제만. 정답·해설은 답을 고른 뒤에만, 기록 저장 없음. 외부 API 호출 없음
class SqlAnswer(BaseModel):
    choice: int


@router.get("/practice/sql")
def practice_sql_list(subject: int | None = None, user=Depends(security.applicant)):
    return db.all_("""SELECT id, subject, area, item, kind, title FROM sql_problems WHERE status='published'
                      AND (%s::int IS NULL OR subject=%s) ORDER BY subject, area, item, id""", subject, subject)


def _sql_problem(pid):
    p = db.one("SELECT * FROM sql_problems WHERE id=%s AND status='published'", pid)
    if not p:
        raise HTTPException(404, "문제를 찾을 수 없습니다.")
    return p


@router.get("/practice/sql/{pid}")
def practice_sql_view(pid: int, user=Depends(security.applicant)):
    p = _sql_problem(pid)
    return {k: p[k] for k in ("id", "subject", "area", "item", "kind", "title", "stem", "tables", "query_sql", "choices")}


@router.post("/practice/sql/{pid}/answer")
def practice_sql_answer(pid: int, body: SqlAnswer, user=Depends(security.applicant)):
    p = _sql_problem(pid)
    if body.choice not in (1, 2, 3, 4):
        raise HTTPException(400, "보기 번호는 1~4입니다.")
    return {"correct": body.choice == p["answer"], "answer": p["answer"], "explanation": p["explanation"]}


# ---------- 결과 (RPT-03~05) ----------
def _result_row(app_id):
    return db.one("""SELECT a.id attempt_id, a.status, a.analysis_status, a.publish_at, e.public_feedback, e.final_result, e.decided_at,
                     coalesce(a.criteria_snapshot->>'decision_mode', 'auto') decision_mode
                     FROM attempts a LEFT JOIN evaluations e ON e.attempt_id=a.id WHERE a.application_id=%s""", app_id)


@router.get("/applications/{app_id}/result")
def result(app_id: int, user=Depends(security.applicant)):
    """v0.6: 합불은 확정(final_result)되면 바로 공개. auto = 평가 직후, semi = 담당자 확인 후. 점수는 리포트 메일·화면 공통 정량값."""
    _own_app(app_id, user)
    row = _result_row(app_id)
    if not row:
        raise HTTPException(404, "응시 기록이 없습니다.")
    if not row["public_feedback"] or not row["final_result"]:
        return {"published": False, "decision_mode": row["decision_mode"], "analysis_status": row["analysis_status"]}
    pub = {k: v for k, v in row["public_feedback"].items() if k != "result"}
    return {"published": True, "result": row["final_result"], "decided_at": row["decided_at"].isoformat(),
            "expires_at": (row["publish_at"] + timedelta(days=7)).isoformat() if row["publish_at"] else None, **pub}


@router.get("/applications/{app_id}/report")
def report_status(app_id: int, user=Depends(security.applicant)):
    """완료 화면 폴링용: 분석 상태 · 결정 방식 · (확정됐으면) 합불 · 리포트 메일 상태."""
    _own_app(app_id, user)
    row = _result_row(app_id)
    if not row:
        raise HTTPException(404, "응시 기록이 없습니다.")
    return {"attempt_status": row["status"], "analysis_status": row["analysis_status"], "decision_mode": row["decision_mode"],
            "evaluated": bool(row["public_feedback"]), "result": row["final_result"],
            "email": report_mail.status(row["attempt_id"]), "email_enabled": report_mail.mailer.enabled()}


@router.post("/applications/{app_id}/report/email")
def report_email(app_id: int, user=Depends(security.applicant)):
    """'결과 리포트 이메일로 받기' — 평가가 끝난 건만, 60초 간격 · 응시당 최대 5회."""
    _own_app(app_id, user)
    row = _result_row(app_id)
    if not row or not row["public_feedback"]:
        raise HTTPException(409, "AI 분석이 끝나면 리포트를 받을 수 있습니다.")
    if row["status"] == "cancelled":
        raise HTTPException(409, "응시가 취소된 건은 리포트가 없습니다.")
    if not report_mail.mailer.enabled():
        raise HTTPException(503, "이메일 발송이 설정되지 않았습니다.")
    last = db.one("""SELECT count(*) n, max(created_at) at FROM email_logs WHERE attempt_id=%s AND kind='applicant_report'""", row["attempt_id"])
    if last["n"] >= report_mail.RESEND_MAX:
        raise HTTPException(429, f"리포트 메일은 응시당 최대 {report_mail.RESEND_MAX}회까지 받을 수 있습니다.")
    if last["at"] and (security.now() - last["at"]).total_seconds() < report_mail.RESEND_GAP_SEC:
        raise HTTPException(429, "방금 보냈습니다. 1분 뒤에 다시 시도해 주세요.")
    r = report_mail.send(row["attempt_id"])
    if not r["ok"]:
        raise HTTPException(502, "메일을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return {"ok": True, "to": user["email"]}


@router.get("/me/notifications")
def notifications(user=Depends(security.current_user)):
    return db.all_("SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 30", user["id"])


@router.post("/me/notifications/read")
def notifications_read(user=Depends(security.current_user)):
    db.run("UPDATE notifications SET read_at=now() WHERE user_id=%s AND read_at IS NULL", user["id"])
    return {"ok": True}
