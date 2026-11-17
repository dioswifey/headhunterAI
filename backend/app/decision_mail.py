"""합불 통보 메일 (v0.10, EML-07~09).

- hybrid 공고에서 담당자가 `POST /admin/applicants/{id}/decision`으로 최종 합불을 확정하면 1회 자동 발송한다(재발송 버튼 없음).
- 메일에는 합불만 담고, 점수·강점·개선점·코딩테스트 결과는 넣지 않는다(그건 auto 전용 report_mail.py가 맡는다).
- 메시지 문장은 agents/decision_notice.py(llm.chat_json 1회 직접 호출, 2026-11-17 CrewAI 제거)가 쓰고, 실패하면 템플릿 문구로 대체한다.
- 발송은 베스트에포트: 실패해도 담당자의 확정 자체는 그대로 유지된다(routes_admin.decide가 호출).
"""
import threading
import traceback

from . import config, db, mailer

TEMPLATE = {"pass": "1차 전형 결과를 안내드립니다. 축하드립니다, 합격하셨습니다. 다음 전형 안내를 기다려 주세요.",
            "fail": "1차 전형 결과를 안내드립니다. 아쉽게도 이번에는 합격하지 못하셨습니다. 지원해 주셔서 감사합니다."}
FONT = "'Pretendard','Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif"


def _load(attempt_id):
    return db.one("""SELECT a.id attempt_id, ap.id application_id, u.id user_id, u.email, u.name,
                     j.title job_title, c.name company, e.final_result
                     FROM attempts a JOIN applications ap ON ap.id=a.application_id JOIN users u ON u.id=ap.user_id
                     JOIN jobs j ON j.id=ap.job_id JOIN companies c ON c.id=j.company_id
                     JOIN evaluations e ON e.attempt_id=a.id WHERE a.id=%s""", attempt_id)


def _message(r):
    writer = "template"
    if config.REPORT_WRITER == "local":
        try:
            from .agents import decision_notice
            out = decision_notice.write({"job": r["job_title"], "company": r["company"], "name": r["name"], "result": r["final_result"]})
            if out:
                return out["message"], "local"
        except Exception:
            traceback.print_exc()
    return TEMPLATE[r["final_result"]], writer


def _render(r, message):
    label = "합격" if r["final_result"] == "pass" else "불합격"
    color = "#39FF14" if r["final_result"] == "pass" else "#8c8c8c"
    my_url = f"{config.PUBLIC_BASE_URL}/applicant/#/my"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>1차 전형 결과 안내</title></head>
<body style="margin:0;padding:0;background:#f2f2f2;font-family:{FONT};-webkit-font-smoothing:antialiased">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f2f2"><tr><td align="center" style="padding:32px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:18px;overflow:hidden">
  <tr><td style="background:#000000;padding:22px 32px">
    <span style="font-family:'Outfit',{FONT};font-size:22px;font-weight:800;color:#ffffff">headhunter<span style="color:#39FF14">AI</span></span></td></tr>
  <tr><td style="padding:34px 32px 6px">
    <div style="display:inline-block;font-size:11px;font-weight:700;letter-spacing:1px;color:#000000;background:{color};border-radius:99px;padding:4px 10px">1차 전형 {label}</div>
    <h1 style="margin:16px 0 6px;font-size:22px;line-height:1.4;color:#000000;font-weight:700">{r['name']}님, 안내드립니다</h1>
    <div style="font-size:14px;color:#737373">{r['company']} · {r['job_title']}</div></td></tr>
  <tr><td style="padding:22px 32px 0"><p style="margin:0;font-size:15px;line-height:1.8;color:#1a1a1a">{message}</p></td></tr>
  <tr><td align="center" style="padding:30px 32px 6px">
    <a href="{my_url}" style="display:inline-block;background:#000000;color:#39FF14;font-size:15px;font-weight:700;text-decoration:none;padding:14px 28px;border-radius:12px">마이페이지에서 확인</a></td></tr>
  <tr><td style="background:#f7f7f7;padding:18px 32px;font-size:11px;line-height:1.6;color:#8c8c8c">
    이 메일은 담당자가 확정한 1차 전형 결과를 안내하는 메일입니다. 발신 전용 주소라 회신되지 않습니다.</td></tr>
</table></td></tr></table></body></html>"""


def send(attempt_id):
    r = _load(attempt_id)
    if not r or not r["final_result"] or not mailer.enabled():
        return {"ok": False, "reason": "skip"}
    message, writer = _message(r)
    subject = f"[headhunterAI] {r['job_title']} 1차 전형 결과 안내"
    text = f"{r['name']}님, {message}\n\n마이페이지: {config.PUBLIC_BASE_URL}/applicant/#/my"
    try:
        mailer.send(r["email"], subject, text, _render(r, message))
        db.run("INSERT INTO email_logs (attempt_id, user_id, kind, to_addr, status, writer) VALUES (%s,%s,'decision_notice',%s,'sent',%s)",
               attempt_id, r["user_id"], r["email"], writer)
        return {"ok": True}
    except mailer.MailError as e:
        db.run("INSERT INTO email_logs (attempt_id, user_id, kind, to_addr, status, writer, error) VALUES (%s,%s,'decision_notice',%s,'failed',%s,%s)",
               attempt_id, r["user_id"], r["email"], writer, str(e))
        return {"ok": False, "reason": "smtp"}


def send_async(attempt_id):
    def run():
        try:
            send(attempt_id)
        except Exception:
            traceback.print_exc()
    threading.Thread(target=run, daemon=True).start()
