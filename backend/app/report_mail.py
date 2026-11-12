"""지원자 정량 리포트 메일 (v0.6).

- 지원자가 완료·결과 화면에서 '결과 리포트 이메일로 받기'를 누르면 발송한다(자동 발송 없음, 60초 간격·응시당 5회). Gmail SMTP만.
- 메일에는 합불을 넣지 않는다. 항목별 점수(100점 환산)·AI 면접 점수·코딩 통과 수·강점·개선점·코치 코멘트만.
- 코치 코멘트는 CrewAI(crew/report_coach.py)가 쓰고, 실패하면 템플릿 문구로 대체한다. 숫자는 모두 코드가 만든 값이다.
- 기업 담당자에게는 메일을 보내지 않는다(대시보드에서 확인).
"""
import html
import threading
import traceback
from datetime import timezone
from zoneinfo import ZoneInfo

from . import config, db, mailer, report_guard
from .evaluation import ITEM_KO, ITEMS

KST = ZoneInfo("Asia/Seoul")
RESEND_GAP_SEC = 60
RESEND_MAX = 5


def _load(attempt_id):
    return db.one("""SELECT a.id attempt_id, a.status, a.completed_at, ap.id application_id, u.id user_id, u.email, u.name,
                     j.title job_title, c.name company, e.item_scores, e.interview_score, e.coding_score, e.strengths, e.improvements,
                     e.public_feedback, e.criteria_snapshot
                     FROM attempts a JOIN applications ap ON ap.id=a.application_id JOIN users u ON u.id=ap.user_id
                     JOIN jobs j ON j.id=ap.job_id JOIN companies c ON c.id=j.company_id
                     JOIN evaluations e ON e.attempt_id=a.id WHERE a.id=%s""", attempt_id)


def _facts(r):
    qs = db.one("""SELECT count(*) FILTER (WHERE parent_id IS NULL) base, count(*) FILTER (WHERE parent_id IS NULL AND answer_status='answered') answered,
                   count(*) FILTER (WHERE parent_id IS NOT NULL) followups FROM attempt_questions WHERE attempt_id=%s""", r["attempt_id"])
    coding = None
    if (r["criteria_snapshot"] or {}).get("coding_enabled"):
        c = db.one("SELECT coalesce(sum(passed),0) passed, coalesce(sum(total),0) total FROM coding_submissions WHERE attempt_id=%s", r["attempt_id"])
        coding = {"passed": int(c["passed"]), "total": int(c["total"]), "score": float(r["coding_score"] or 0)}
    return {"answered": qs["answered"], "base": qs["base"] or 10, "followups": qs["followups"], "coding": coding}


def _template_coach(items, strengths, improvements):
    if report_guard.is_tie(dict(items)):   # 동점: '가장 높고 가장 낮은 항목' 문장을 쓰지 않는다
        coaching = ("이번 면접에서는 항목 간 점수 차이가 없어 특정 항목이 두드러지지 않았습니다. "
                    "항목별 비교보다 질문 의도에 맞춰 실제 경험과 근거를 들어 답하는 연습부터 해 보세요.")
        actions = ["자주 나오는 질문 3개를 골라 '결론 → 근거 → 사례' 순서로 1분 안에 답하는 연습을 해 보세요."]
        if improvements:
            actions.append(f"개선점 '{improvements[0][:60]}'을(를) 보완할 실제 경험 사례를 하나 정리해 두세요.")
        actions.append("답변을 녹음해 다시 듣고, 핵심 문장이 첫 10초 안에 나오는지 확인해 보세요.")
        return {"coaching": coaching, "actions": actions[:3]}
    best = max(items, key=lambda kv: kv[1])
    worst = min(items, key=lambda kv: kv[1])
    coaching = (f"이번 면접에서는 {best[0]} 항목이 {best[1]:.0f}점으로 가장 높았고, {worst[0]} 항목이 {worst[1]:.0f}점으로 가장 낮았습니다. "
                f"잘한 부분은 다음 면접에서도 같은 방식으로 답변하시고, {worst[0]} 항목은 아래 과제로 집중해서 연습해 보세요.")
    actions = [f"{worst[0]}: 자주 나오는 질문 3개를 골라 '결론 → 근거 → 사례' 순서로 1분 안에 답하는 연습을 해 보세요."]
    if improvements:
        actions.append(f"개선점 '{improvements[0][:60]}'을(를) 보완할 실제 경험 사례를 하나 정리해 두세요.")
    actions.append("답변을 녹음해 다시 듣고, 핵심 문장이 첫 10초 안에 나오는지 확인해 보세요.")
    return {"coaching": coaching, "actions": actions[:3]}


def _coach(r, items, facts):
    cached = (r["public_feedback"] or {}).get("coaching")
    if cached:
        return cached, cached.get("writer", "template")
    out, writer, reject = None, "template", None
    if config.REPORT_WRITER == "crewai":
        try:
            from .crew import report_coach
            out = report_coach.write({"job": r["job_title"], "item_scores": {k: v for k, v in items},
                                      "interview_score": float(r["interview_score"]), "coding": facts["coding"],
                                      "strengths": r["strengths"], "improvements": r["improvements"]})
            writer = "crewai" if out else "template"
            reject = None if out else report_coach.LAST_REJECT
        except Exception as e:
            traceback.print_exc()
            reject = f"exception:{type(e).__name__}"
    out = out or _template_coach(items, r["strengths"] or [], r["improvements"] or [])
    out["writer"] = writer
    if reject:
        out["reject"] = reject      # 템플릿 대체 사유 코드만(원문 없음) — 리포트 품질 측정 M-42 원인 추적
    db.run("UPDATE evaluations SET public_feedback = public_feedback || %s WHERE attempt_id=%s", db.J({"coaching": out}), r["attempt_id"])
    return out, writer


# ───────── 본문 (메일 클라이언트 호환: 표 레이아웃 + 인라인 스타일, 3색 + 흑백 명암) ─────────
FONT = "'Pretendard','Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif"


def _e(s):
    return html.escape(str(s or ""))


def _bar(v):
    v = max(0, min(100, round(float(v or 0))))
    fill = f'<td width="{v}%" style="background:#000000;height:8px;font-size:0;line-height:0;border-radius:4px">&nbsp;</td>' if v else ""
    rest = f'<td style="height:8px;font-size:0;line-height:0">&nbsp;</td>' if v < 100 else ""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#e6e6e6;border-radius:4px">'
            f'<tr>{fill}{rest}</tr></table>')


def _metric(label, value, unit, sub=""):
    return (f'<td width="33%" valign="top" style="padding:18px 16px;border:1px solid #e6e6e6;border-radius:12px">'
            f'<div style="font-size:12px;color:#737373;font-weight:600">{label}</div>'
            f'<div style="font-size:30px;font-weight:700;color:#000000;margin-top:6px;letter-spacing:-0.5px">{value}'
            f'<span style="font-size:14px;font-weight:500;color:#737373"> {unit}</span></div>'
            f'<div style="font-size:12px;color:#8c8c8c;margin-top:2px">{sub}</div></td>')


def _list(items, mark):
    rows = "".join(
        f'<tr><td width="28" valign="top" style="padding:6px 0"><div style="width:20px;height:20px;border-radius:6px;background:#000000;'
        f'color:#39FF14;font-size:12px;font-weight:700;text-align:center;line-height:20px">{mark if mark else i + 1}</div></td>'
        f'<td style="padding:6px 0;font-size:14px;line-height:1.6;color:#1a1a1a">{_e(s)}</td></tr>' for i, s in enumerate(items))
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>' if rows else \
        '<p style="font-size:14px;color:#737373;margin:0">-</p>'


def _section(title, body):
    return (f'<tr><td style="padding:26px 32px 0"><div style="font-size:15px;font-weight:700;color:#000000;margin-bottom:12px">{title}</div>'
            f'{body}</td></tr>')


def render(r, items, facts, coach):
    done = r["completed_at"].astimezone(KST) if r["completed_at"] else None
    when = done.strftime("%Y.%m.%d %H:%M") if done else ""
    cod = facts["coding"]
    metrics = _metric("AI 면접 점수", f"{float(r['interview_score']):.1f}", "/ 100", "5개 항목 가중 평균") + \
        '<td width="10" style="font-size:0">&nbsp;</td>' + \
        _metric("답변한 기본 질문", facts["answered"], f"/ {facts['base']}", f"꼬리질문 {facts['followups']}개") + \
        '<td width="10" style="font-size:0">&nbsp;</td>' + \
        (_metric("코딩테스트", f"{cod['passed']}", f"/ {cod['total']} 통과", f"통과율 {cod['score']:.0f}%") if cod
         else _metric("코딩테스트", "-", "", "이 공고는 미사용"))
    item_rows = "".join(
        f'<tr><td width="34%" style="padding:7px 0;font-size:14px;color:#1a1a1a">{_e(k)}</td>'
        f'<td style="padding:7px 12px">{_bar(v)}</td>'
        f'<td width="52" align="right" style="padding:7px 0;font-size:14px;font-weight:700;color:#000000">{v:.0f}</td></tr>' for k, v in items)
    actions = "".join(f'<tr><td width="22" valign="top" style="padding:5px 0;font-size:13px;font-weight:700;color:#39FF14">{i + 1}.</td>'
                      f'<td style="padding:5px 0;font-size:14px;line-height:1.6;color:#ffffff">{_e(a)}</td></tr>' for i, a in enumerate(coach["actions"]))
    my_url = f"{config.PUBLIC_BASE_URL}/applicant/#/my"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>AI 면접 리포트</title></head>
<body style="margin:0;padding:0;background:#f2f2f2;font-family:{FONT};-webkit-font-smoothing:antialiased">
<div style="display:none;max-height:0;overflow:hidden">{_e(r['name'])}님의 AI 면접 정량 리포트가 도착했습니다.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f2f2"><tr><td align="center" style="padding:32px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:18px;overflow:hidden">
  <tr><td style="background:#000000;padding:22px 32px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-family:'Outfit',{FONT};font-size:22px;font-weight:800;color:#ffffff;letter-spacing:-0.3px">headhunter<span style="color:#39FF14">AI</span></td>
      <td align="right" style="font-size:12px;color:#a6a6a6;font-weight:600">AI 면접 리포트</td></tr></table></td></tr>
  <tr><td style="padding:34px 32px 6px">
    <div style="display:inline-block;font-size:11px;font-weight:700;letter-spacing:1px;color:#000000;background:#39FF14;border-radius:99px;padding:4px 10px">QUANTITATIVE REPORT</div>
    <h1 style="margin:16px 0 6px;font-size:24px;line-height:1.35;color:#000000;font-weight:700">{_e(r['name'])}님, AI 면접을 마치셨습니다</h1>
    <div style="font-size:14px;color:#737373">{_e(r['company'])} · {_e(r['job_title'])}{' · ' + when + ' 응시' if when else ''}</div></td></tr>
  <tr><td style="padding:20px 32px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{metrics}</tr></table></td></tr>
  {_section("항목별 점수 <span style='font-size:12px;font-weight:500;color:#8c8c8c'>(100점 환산)</span>",
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{item_rows}</table>')}
  {_section("잘한 부분", _list(r["strengths"] or [], "✓"))}
  {_section("더 발전할 수 있는 부분", _list(r["improvements"] or [], None))}
  <tr><td style="padding:26px 32px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#000000;border-radius:14px">
    <tr><td style="padding:22px 22px 18px">
      <div style="font-size:12px;font-weight:700;letter-spacing:0.5px;color:#39FF14">AI 코치 코멘트</div>
      <p style="margin:10px 0 0;font-size:14px;line-height:1.7;color:#ffffff">{_e(coach['coaching'])}</p>
      <div style="font-size:12px;font-weight:700;color:#a6a6a6;margin-top:18px">다음 면접 전까지 해 볼 것</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:4px">{actions}</table></td></tr></table></td></tr>
  <tr><td align="center" style="padding:30px 32px 6px">
    <a href="{_e(my_url)}" style="display:inline-block;background:#39FF14;color:#000000;font-size:15px;font-weight:700;text-decoration:none;padding:14px 28px;border-radius:12px">마이페이지에서 보기</a></td></tr>
  <tr><td style="padding:22px 32px 30px">
    <p style="margin:0;font-size:12px;line-height:1.7;color:#8c8c8c">이 리포트는 1차 합격 여부와 관계없이 연습과 성장을 위해 보내 드립니다. 1차 결과는 마이페이지에서 확인할 수 있습니다.
    점수는 시스템이 루브릭 채점 결과로 계산했고, 문장은 AI가 작성했습니다. 근거가 된 답변 내용에 대해 설명을 요청할 수 있습니다.</p></td></tr>
  <tr><td style="background:#f7f7f7;padding:18px 32px;font-size:11px;line-height:1.6;color:#8c8c8c">
    이 메일은 headhunterAI AI 면접을 완료한 지원자에게 발송되는 알림 메일입니다. 발신 전용 주소라 회신되지 않습니다.</td></tr>
</table></td></tr></table></body></html>"""


def render_text(r, items, facts, coach):
    lines = [f"{r['name']}님, AI 면접을 마치셨습니다", f"{r['company']} · {r['job_title']}", "",
             f"AI 면접 점수: {float(r['interview_score']):.1f} / 100", f"답변한 기본 질문: {facts['answered']} / {facts['base']}"]
    if facts["coding"]:
        lines.append(f"코딩테스트: {facts['coding']['passed']} / {facts['coding']['total']} 통과")
    lines += ["", "[항목별 점수]"] + [f"- {k}: {v:.0f}" for k, v in items]
    lines += ["", "[잘한 부분]"] + [f"- {s}" for s in r["strengths"] or []]
    lines += ["", "[더 발전할 수 있는 부분]"] + [f"- {s}" for s in r["improvements"] or []]
    lines += ["", "[AI 코치 코멘트]", coach["coaching"], ""] + [f"{i + 1}. {a}" for i, a in enumerate(coach["actions"])]
    lines += ["", f"마이페이지: {config.PUBLIC_BASE_URL}/applicant/#/my", "이 리포트는 1차 합격 여부와 관계없이 보내 드립니다."]
    return "\n".join(lines)


def send(attempt_id):
    r = _load(attempt_id)
    if not r or r["status"] == "cancelled" or not mailer.enabled():
        return {"ok": False, "reason": "skip"}
    items = [(ITEM_KO[it], float((r["item_scores"] or {}).get(it, 0))) for it in ITEMS]
    facts = _facts(r)
    coach, writer = _coach(r, items, facts)
    try:
        mailer.send(r["email"], f"[headhunterAI] {r['job_title']} AI 면접 리포트", render_text(r, items, facts, coach), render(r, items, facts, coach))
        db.run("INSERT INTO email_logs (attempt_id, user_id, kind, to_addr, status, writer) VALUES (%s,%s,'applicant_report',%s,'sent',%s)",
               attempt_id, r["user_id"], r["email"], writer)
        return {"ok": True}
    except mailer.MailError as e:
        db.run("INSERT INTO email_logs (attempt_id, user_id, kind, to_addr, status, writer, error) VALUES (%s,%s,'applicant_report',%s,'failed',%s,%s)",
               attempt_id, r["user_id"], r["email"], writer, str(e))
        return {"ok": False, "reason": "smtp"}


def send_async(attempt_id):
    def run():
        try:
            send(attempt_id)
        except Exception:
            traceback.print_exc()
    threading.Thread(target=run, daemon=True).start()


def status(attempt_id):
    row = db.one("""SELECT status, to_addr, created_at FROM email_logs WHERE attempt_id=%s AND kind='applicant_report'
                    ORDER BY created_at DESC LIMIT 1""", attempt_id)
    n = db.one("SELECT count(*) n FROM email_logs WHERE attempt_id=%s AND kind='applicant_report'", attempt_id)["n"]
    if not row:
        return {"status": "none", "count": 0}
    return {"status": row["status"], "to": row["to_addr"], "at": row["created_at"].astimezone(timezone.utc).isoformat(), "count": n}
