"""Background loops inside the API process (TRD §7.3).

- heartbeat monitor (5s): interview/coding attempts with no heartbeat for 15s → 장애 중단 or 응시 종료
- analysis worker (2s): analysis_jobs queue
- housekeeping (60s): missing-job sweep, resume deadline, auto close, publish notifications, retention delete
"""
import os
import threading
import time
import traceback

from . import config, db, evaluation


def classify_disconnect(att):
    """5.1 판정표: user exit event recorded just before the connection loss → 임의 종료, otherwise 장애."""
    if att["user_exit_at"] and att["last_heartbeat"] and att["user_exit_at"] >= att["last_heartbeat"] - _sec(6):
        return "terminated"
    return "interrupted"


def _sec(n):
    from datetime import timedelta
    return timedelta(seconds=n)


def finish_attempt(attempt_id, status, reason):
    db.run("""UPDATE attempts SET status=%s, completed_at=now() WHERE id=%s""", status, attempt_id)
    db.run("UPDATE attempt_questions SET answer_status='no_answer' WHERE attempt_id=%s AND answer_status='pending'", attempt_id)
    db.run("INSERT INTO attempt_events (attempt_id, type, detail) VALUES (%s,%s,%s)", attempt_id, status, db.J({"reason": reason}))
    evaluation.enqueue(attempt_id)


def heartbeat_monitor():
    rows = db.all_(f"""SELECT * FROM attempts WHERE status IN ('interview','coding')
                       AND last_heartbeat < now() - interval '{config.HEARTBEAT_LOST_SEC} seconds'""")
    for att in rows:
        heartbeat_monitor_one(att)
    # COD-04: 제한시간 종료 → 마지막 저장 코드 자동 제출 (클라이언트 자동 제출이 안 온 경우 30초 유예 후)
    for att in db.all_("SELECT id FROM attempts WHERE status='coding' AND coding_deadline < now() - interval '30 seconds'"):
        db.run("UPDATE coding_submissions SET submitted_at=now() WHERE attempt_id=%s AND submitted_at IS NULL", att["id"])
        finish_attempt(att["id"], "completed", "coding_time_up")


def heartbeat_monitor_one(att):
    if classify_disconnect(att) == "terminated":
        finish_attempt(att["id"], "terminated", "user_exit_then_disconnect")
        return
    remaining = None
    if att["phase"] == "coding" and att["coding_deadline"]:
        remaining = max(0, int((att["coding_deadline"] - att["last_heartbeat"]).total_seconds()))
    db.run("""UPDATE attempts SET status='interrupted', interrupt_count=interrupt_count+1, coding_remaining=%s
              WHERE id=%s AND status IN ('interview','coding')""", remaining, att["id"])
    db.run("INSERT INTO attempt_events (attempt_id, type, detail) VALUES (%s,'interrupted',%s)",
           att["id"], db.J({"signal": "heartbeat_missed", "judgement": "시스템 장애(이어가기 승인)"}))


def housekeeping():
    # v0.9 PAY-08: 정기결제 갱신·재시도·만료 (결제가 꺼져 있으면 아무것도 안 함)
    try:
        from . import payments
        payments.renew_due()
    except Exception as e:   # 결제 오류가 다른 정리 작업을 막지 않게
        print(f"[payments] renew_due 실패: {type(e).__name__}: {e}", flush=True)
    # 누락 작업 재등록: 끝난 응시인데 분석 작업이 없는 건 (6.2 안정성)
    for r in db.all_("""SELECT a.id FROM attempts a LEFT JOIN analysis_jobs j ON j.attempt_id=a.id
                        WHERE a.status IN ('completed','terminated','cancelled') AND j.attempt_id IS NULL"""):
        evaluation.enqueue(r["id"])
    # 정체 작업: running 상태로 10분 넘게 멈춘 건 다시 큐로
    db.run("UPDATE analysis_jobs SET status='queued' WHERE status='running' AND next_run_at < now()")
    # RES-03: 재개 기한(마감 후 24시간) 경과한 장애 중단 응시 → 저장된 답변까지 완료 처리
    for r in db.all_("""SELECT a.id FROM attempts a JOIN applications ap ON ap.id=a.application_id JOIN jobs j ON j.id=ap.job_id
                        WHERE a.status='interrupted' AND (j.close_date + 1 + time '00:00') AT TIME ZONE 'Asia/Seoul' + interval '24 hours' < now()"""):
        finish_attempt(r["id"], "completed", "resume_deadline_passed")
    # JOB-01: 마감일 경과 공고 자동 마감
    db.run("UPDATE jobs SET status='closed', updated_at=now() WHERE status='published' AND close_date < (now() AT TIME ZONE 'Asia/Seoul')::date")
    # 결과 공개 알림 (v0.6): 합불이 확정되면(auto = 평가 직후, semi = 담당자 확인 후) 알린다
    for r in db.all_("""SELECT a.id, ap.user_id, j.title FROM attempts a JOIN applications ap ON ap.id=a.application_id
                        JOIN jobs j ON j.id=ap.job_id JOIN evaluations e ON e.attempt_id=a.id
                        WHERE e.final_result IS NOT NULL AND NOT a.publish_notified"""):
        db.run("INSERT INTO notifications (user_id, type, title, body) VALUES (%s,'result',%s,%s)",
               r["user_id"], "1차 결과가 공개되었습니다", f"[{r['title']}] 결과를 마이페이지에서 확인할 수 있습니다.")
        db.run("UPDATE attempts SET publish_notified=true WHERE id=%s", r["id"])
    retention()


def retention():
    """6.1: 응시 데이터 = 공개일 + 7일, 미지원 업로드 = 24시간. 삭제 기록은 audit_logs."""
    for r in db.all_("""SELECT ap.id FROM applications ap JOIN attempts a ON a.application_id=ap.id
                        WHERE a.publish_at + interval '7 days' < now()"""):
        for f in db.all_("""SELECT f.id, f.storage_path FROM files f JOIN resume_extractions x ON x.file_id=f.id
                            JOIN applications ap ON ap.extraction_id=x.id WHERE ap.id=%s""", r["id"]):
            _unlink(f["storage_path"])
            db.run("DELETE FROM files WHERE id=%s", f["id"])
        db.run("DELETE FROM applications WHERE id=%s", r["id"])
        db.audit(None, "retention_delete", "application", r["id"])
    for x in db.all_("""SELECT x.id, x.file_id, f.storage_path FROM resume_extractions x JOIN files f ON f.id=x.file_id
                        WHERE x.expires_at < now() AND NOT EXISTS (SELECT 1 FROM applications ap WHERE ap.extraction_id=x.id)"""):
        _unlink(x["storage_path"])
        db.run("DELETE FROM files WHERE id=%s", x["file_id"])
        db.audit(None, "retention_delete", "upload", x["id"])
    db.run("DELETE FROM sessions WHERE expires_at < now()")


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _loop(fn, every):
    def run():
        while True:
            try:
                fn()
            except Exception:
                traceback.print_exc()
            time.sleep(every)
    threading.Thread(target=run, daemon=True).start()


def _worker():
    while True:
        try:
            if not evaluation.work_once():
                time.sleep(2)
        except Exception:
            traceback.print_exc()
            time.sleep(5)


def start():
    _loop(heartbeat_monitor, 5)
    _loop(housekeeping, 60)
    threading.Thread(target=_worker, daemon=True).start()
