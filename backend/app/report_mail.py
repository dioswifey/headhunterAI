"""지원자 정량 리포트 메일 (v0.6).

- 지원자가 완료·결과 화면에서 '결과 리포트 이메일로 받기'를 누르면 발송한다(자동 발송 없음, 60초 간격·응시당 5회). Gmail SMTP만.
- 메일에는 합불을 넣지 않는다. 항목별 점수(100점 환산)·AI 면접 점수·코딩 통과 수·강점·개선점·코치 코멘트만.
- 코치 코멘트(coaching, 정성적 코멘트 하나만, 2026-11-17 연습 과제 삭제)는 agents/report_coach.py(llm.chat_json 1회
  직접 호출, 2026-11-17 CrewAI 제거)가 쓰고, 실패하면 템플릿 문구로 대체한다. 숫자는 모두 코드가 만든 값이다.
- 기업 담당자에게는 메일을 보내지 않는다(대시보드에서 확인).
"""
import html
import re
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
        from . import coding as coding_mod
        c = coding_mod.points_summary(r["attempt_id"]) or {"points": 0, "max": 0, "passed": 0, "total": 0}
        coding = {"passed": int(c["passed"]), "total": int(c["total"]), "points": c["points"], "max": c["max"],
                  "score": float(r["coding_score"] or 0)}
    return {"answered": qs["answered"], "base": qs["base"] or 10, "followups": qs["followups"], "coding": coding}


HANGUL = re.compile(r"[가-힣]")
CAT_KO = {"personality": "인성", "resume": "이력서 기반", "job": "직무 이해도", "company": "기업 이해"}


def _model_answer(q):
    """그 문항의 모범답안 — **모델 호출 0회**(질문 ID로 은행에서 바로 꺼낸다, 2026-11-18 A안).

    갈래마다 있는 곳이 다르다:
      · 사내문서 질문 `internal_q:` → Chroma 질문 메타의 `model_answer`(528개 전부 한국어·요지 형태)
      · Redis 은행 `redis:` → Redis 해시. 다만 **236개 중 207개가 영문**이라(2026-11-18 실측)
        한글이 없으면 쓰지 않는다 — 한글 리포트에 영문 모범답안을 섞지 않는다.
      · DB `question_bank` → 컬럼 자체가 없다 · 이력서 문항 → **모범답안을 넣지 않는다**(D안):
        본인 경험을 묻는 질문에 '정답'은 없다. 둘 다 빈 문자열이고 렌더에서 **칸을 뺀다**.

    **검사 범위가 다른 문장들과 같지 않다(2026-11-18 승인)** — 분명히 적어 둔다:
    `rationale`·`quote`는 허용 수치를 '이 응시의 질문·답변·점수'로 두지만, 모범답안은 **그 문장 자신**으로 둔다.
    모범답안은 이 응시와 무관하게 은행에 미리 들어 있는 값이라, 응시 숫자로 대조하면 정상 모범답안이
    전부 `invented_number`로 떨어진다(검사가 아니라 오작동이 된다). 생성 때 A18이 이미 출처 조각과
    토큰을 대조했다(`internal_question_agent.validate`의 `bad_tokens`).
    → 모범답안에 실제로 적용되는 것은 **금지어(FORBIDDEN)와 지시문 누출 검사뿐**이다.
    "모범답안도 똑같이 수치 검사를 했다"고 읽지 말 것."""
    gm = q.get("gen_method") or ""
    if not (gm.startswith("internal_q:") or gm.startswith("redis:")):
        return ""
    try:
        from .agents import question_graph as QG
        ideal = ((QG.bank_reference(dict(q)) or {}).get("ideal") or "").strip()
    except Exception:  # noqa: BLE001 — 은행 조회 실패는 그 칸만 비운다(메일은 나간다)
        return ""
    if not ideal or not HANGUL.search(ideal):
        return ""
    return "" if report_guard.check_text([ideal], report_guard.number_tokens(ideal)) else ideal


def build_questions(rows, scores_by_q):
    """문항별 분석 재료 — **순수 함수 · DB·모델 호출 0회.** 실서비스(`_questions`)와 측정 도구가 **같은 함수**를 쓴다
    (측정이 보는 메일과 실제로 나가는 메일이 달라지면 그 측정은 의미가 없다).

    rows        : [{id, seq, category, text, gen_method, answer_text, answer_status}] — **기본 문항만**
                  (꼬리질문은 `question_scores` 행이 없다 = 채점 대상이 아니다. 개수만 따로 보여 준다)
    scores_by_q : {question_id: {eval_item: {score, quote, verified, rationale}}}

    `rationale`·`quote`·모범답안은 `report_guard` 검사를 통과한 것만 남기고, 못 넘으면 **그 칸을 뺀다**.
    사유가 하나도 안 남으면 **인용까지 뺀다**(2026-11-18 사용자 결정) — '내 답변 조각 + 설명 없음'은
    지원자에게 미완성으로 읽힌다. 걸러진 건수는 `filtered`에 남겨 측정에서 셀 수 있게 한다.
    `improvements`는 비워 둔다 — '개선점'이 문항별인지 전체인지 확인되면 이 리스트만 채우면 된다."""
    out = []
    for r in rows:
        sc = scores_by_q.get(r["id"]) or {}
        ss = [(k, v) for k, v in sc.items() if isinstance(v, dict)]
        ans = r.get("answer_text") or ""
        allowed = report_guard.number_tokens(r.get("text") or "", ans, *[v.get("score") for _, v in ss])
        ans_nums = report_guard.number_tokens(ans)
        notes, seen, n_drop = [], set(), 0
        for item, v in ss:
            t = (v.get("rationale") or "").strip()
            if not t or t in seen:
                continue
            if report_guard.check_text([t], allowed):
                n_drop += 1
                continue
            seen.add(t)
            notes.append((ITEM_KO.get(item, item), t))
        quote, q_drop = "", False
        for _, v in ss:
            t = (v.get("quote") or "").strip()
            if not (v.get("verified") and t):
                continue
            if report_guard.check_text([t], ans_nums):
                q_drop = True
                continue
            quote = t
            break
        if not notes:                       # 사유가 전부 걸러졌으면 인용도 보여 주지 않는다
            quote = ""
        ma = _model_answer(r)
        out.append({"seq": r.get("seq"), "category": CAT_KO.get(r.get("category"), r.get("category")),
                    "text": r.get("text") or "",
                    "items": [(ITEM_KO.get(k, k), int(v.get("score") or 0)) for k, v in ss],
                    "quote": quote, "notes": notes, "model_answer": ma, "improvements": [],
                    "answered": r.get("answer_status") == "answered",
                    # 측정용: 관문에 걸려 빠진 칸 수. all_notes_dropped = 사유가 0개로 남은 문항
                    "filtered": {"notes": n_drop, "quote": bool(q_drop or (not notes and ss)),
                                 "all_notes_dropped": bool(ss and not notes)}})
    return out


def _questions(attempt_id):
    """DB에서 기본 문항·점수를 읽어 `build_questions`에 넘긴다(쿼리 2개, 모델 0회)."""
    rows = db.all_("""SELECT q.id, q.seq, q.category, q.text, q.gen_method, q.answer_text, q.answer_status
                      FROM attempt_questions q WHERE q.attempt_id=%s AND q.parent_id IS NULL ORDER BY q.seq""", attempt_id)
    scores = db.all_("""SELECT s.question_id, s.eval_item, s.score, s.quote, s.rationale, s.verified
                        FROM question_scores s JOIN attempt_questions q ON q.id=s.question_id
                        WHERE q.attempt_id=%s AND q.parent_id IS NULL ORDER BY s.question_id, s.eval_item""", attempt_id)
    by_q = {}
    for s in scores or []:
        by_q.setdefault(s["question_id"], {})[s["eval_item"]] = {
            "score": s["score"], "quote": s["quote"], "verified": s["verified"], "rationale": s["rationale"]}
    return build_questions(rows or [], by_q)


def _template_coach(items):
    if report_guard.is_tie(dict(items)):   # 동점: '가장 높고 가장 낮은 항목' 문장을 쓰지 않는다
        coaching = ("이번 면접에서는 항목 간 점수 차이가 없어 특정 항목이 두드러지지 않았습니다. "
                    "항목별 비교보다 질문 의도에 맞춰 실제 경험과 근거를 들어 답하는 연습부터 해 보세요.")
        return {"coaching": coaching}
    best = max(items, key=lambda kv: kv[1])
    worst = min(items, key=lambda kv: kv[1])
    coaching = (f"이번 면접에서는 {best[0]} 항목이 {best[1]:.0f}점으로 가장 높았고, {worst[0]} 항목이 {worst[1]:.0f}점으로 가장 낮았습니다. "
                f"잘한 부분은 다음 면접에서도 같은 방식으로 답변하시고, {worst[0]} 항목은 집중해서 연습해 보세요.")
    return {"coaching": coaching}


def _coach(r, items, facts):
    cached = (r["public_feedback"] or {}).get("coaching")
    if cached:
        return cached, cached.get("writer", "template")
    out, writer, reject = None, "template", None
    if config.REPORT_WRITER == "local":
        try:
            from .agents import report_coach
            out = report_coach.write({"job": r["job_title"], "item_scores": {k: v for k, v in items},
                                      "interview_score": float(r["interview_score"]), "coding": facts["coding"],
                                      "strengths": r["strengths"], "improvements": r["improvements"]})
            writer = "local" if out else "template"
            reject = None if out else report_coach.LAST_REJECT
        except Exception as e:
            traceback.print_exc()
            reject = f"exception:{type(e).__name__}"
    out = out or _template_coach(items)
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


def _chip(label, score):
    return (f'<span style="display:inline-block;font-size:12px;color:#1a1a1a;background:#f2f2f2;border-radius:99px;'
            f'padding:3px 9px;margin:0 4px 4px 0">{_e(label)} <b style="color:#000000">{score}</b>/5</span>')


def _qa(qa):
    """문항 1개 블록. **값이 없는 칸은 그리지 않는다**(빈 칸이 보이면 미완성으로 읽힌다)."""
    parts = [f'<div style="font-size:12px;font-weight:700;color:#737373">Q{qa["seq"]} · {_e(qa["category"])}</div>'
             f'<p style="margin:6px 0 0;font-size:14px;line-height:1.6;color:#000000;font-weight:600">{_e(qa["text"])}</p>']
    if not qa["answered"]:
        parts.append('<p style="margin:8px 0 0;font-size:13px;color:#8c8c8c">답변이 기록되지 않아 분석을 넣지 않았습니다.</p>')
    if qa["items"]:
        parts.append('<div style="margin-top:10px">' + "".join(_chip(k, v) for k, v in qa["items"]) + "</div>")
    if qa["quote"]:
        parts.append('<div style="margin-top:10px;padding:10px 12px;background:#f7f7f7;border-left:3px solid #d9d9d9;'
                     f'font-size:13px;line-height:1.6;color:#404040">내 답변에서 — {_e(qa["quote"])}</div>')
    if qa["notes"]:
        rows = "".join(f'<li style="margin:4px 0">{_e(k)}: {_e(t)}</li>' for k, t in qa["notes"])
        parts.append(f'<ul style="margin:10px 0 0;padding-left:18px;font-size:13px;line-height:1.7;color:#404040">{rows}</ul>')
    if qa["improvements"]:
        rows = "".join(f'<li style="margin:4px 0">{_e(t)}</li>' for t in qa["improvements"])
        parts.append(f'<ul style="margin:8px 0 0;padding-left:18px;font-size:13px;line-height:1.7;color:#404040">{rows}</ul>')
    if qa["model_answer"]:
        parts.append('<div style="margin-top:10px;padding:12px;background:#ffffff;border:1px solid #39FF14;border-radius:10px">'
                     '<div style="font-size:11px;font-weight:700;letter-spacing:0.4px;color:#000000">이렇게 답하면 좋습니다</div>'
                     f'<p style="margin:6px 0 0;font-size:13px;line-height:1.7;color:#1a1a1a">{_e(qa["model_answer"])}</p></div>')
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:14px">'
            f'<tr><td style="padding:16px;border:1px solid #e6e6e6;border-radius:12px">{"".join(parts)}</td></tr></table>')


def _section(title, body):
    return (f'<tr><td style="padding:26px 32px 0"><div style="font-size:15px;font-weight:700;color:#000000;margin-bottom:12px">{title}</div>'
            f'{body}</td></tr>')


def render(r, items, facts, coach, qas=()):
    done = r["completed_at"].astimezone(KST) if r["completed_at"] else None
    when = done.strftime("%Y.%m.%d %H:%M") if done else ""
    cod = facts["coding"]
    metrics = _metric("AI 면접 점수", f"{float(r['interview_score']):.1f}", "/ 100", "5개 항목 가중 평균") + \
        '<td width="10" style="font-size:0">&nbsp;</td>' + \
        _metric("답변한 기본 질문", facts["answered"], f"/ {facts['base']}", f"꼬리질문 {facts['followups']}개") + \
        '<td width="10" style="font-size:0">&nbsp;</td>' + \
        (_metric("코딩테스트", f"{cod['points']:g}", f"/ {cod['max']}점", f"테스트 {cod['passed']}/{cod['total']} 통과") if cod
         else _metric("코딩테스트", "-", "", "이 공고는 미사용"))
    item_rows = "".join(
        f'<tr><td width="34%" style="padding:7px 0;font-size:14px;color:#1a1a1a">{_e(k)}</td>'
        f'<td style="padding:7px 12px">{_bar(v)}</td>'
        f'<td width="52" align="right" style="padding:7px 0;font-size:14px;font-weight:700;color:#000000">{v:.0f}</td></tr>' for k, v in items)
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
  {_section(f"질문별 분석 <span style='font-size:12px;font-weight:500;color:#8c8c8c'>(기본 질문 {len(qas)}개"
            + (f" · 꼬리질문 {facts['followups']}개는 채점 대상이 아니라 분석에서 제외" if facts["followups"] else "") + ")</span>",
            "".join(_qa(q) for q in qas)) if qas else ""}
  <tr><td style="padding:26px 32px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#000000;border-radius:14px">
    <tr><td style="padding:22px 22px 18px">
      <div style="font-size:12px;font-weight:700;letter-spacing:0.5px;color:#39FF14">AI 코치 코멘트</div>
      <p style="margin:10px 0 0;font-size:14px;line-height:1.7;color:#ffffff">{_e(coach['coaching'])}</p></td></tr></table></td></tr>
  <tr><td align="center" style="padding:30px 32px 6px">
    <a href="{_e(my_url)}" style="display:inline-block;background:#39FF14;color:#000000;font-size:15px;font-weight:700;text-decoration:none;padding:14px 28px;border-radius:12px">마이페이지에서 보기</a></td></tr>
  <tr><td style="padding:22px 32px 30px">
    <p style="margin:0;font-size:12px;line-height:1.7;color:#8c8c8c">이 리포트는 1차 합격 여부와 관계없이 연습과 성장을 위해 보내 드립니다. 1차 결과는 마이페이지에서 확인할 수 있습니다.
    점수는 시스템이 루브릭 채점 결과로 계산했고, 문장은 AI가 작성했습니다. 근거가 된 답변 내용에 대해 설명을 요청할 수 있습니다.</p></td></tr>
  <tr><td style="background:#f7f7f7;padding:18px 32px;font-size:11px;line-height:1.6;color:#8c8c8c">
    이 메일은 headhunterAI AI 면접을 완료한 지원자에게 발송되는 알림 메일입니다. 발신 전용 주소라 회신되지 않습니다.</td></tr>
</table></td></tr></table></body></html>"""


def render_text(r, items, facts, coach, qas=()):
    lines = [f"{r['name']}님, AI 면접을 마치셨습니다", f"{r['company']} · {r['job_title']}", "",
             f"AI 면접 점수: {float(r['interview_score']):.1f} / 100", f"답변한 기본 질문: {facts['answered']} / {facts['base']}"]
    if facts["coding"]:
        lines.append(f"코딩테스트: {facts['coding']['points']:g} / {facts['coding']['max']}점 (테스트 {facts['coding']['passed']} / {facts['coding']['total']} 통과)")
    lines += ["", "[항목별 점수]"] + [f"- {k}: {v:.0f}" for k, v in items]
    lines += ["", "[잘한 부분]"] + [f"- {s}" for s in r["strengths"] or []]
    lines += ["", "[더 발전할 수 있는 부분]"] + [f"- {s}" for s in r["improvements"] or []]
    for q in qas:
        lines += ["", f"[Q{q['seq']} · {q['category']}] {q['text']}"]
        if not q["answered"]:
            lines.append("답변이 기록되지 않아 분석을 넣지 않았습니다.")
        if q["items"]:
            lines.append("점수: " + " · ".join(f"{k} {v}/5" for k, v in q["items"]))
        if q["quote"]:
            lines.append(f"내 답변에서 — {q['quote']}")
        lines += [f"- {k}: {t}" for k, t in q["notes"]]
        lines += [f"- {t}" for t in q["improvements"]]
        if q["model_answer"]:
            lines += ["이렇게 답하면 좋습니다: " + q["model_answer"]]
    lines += ["", "[AI 코치 코멘트]", coach["coaching"]]
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
        qas = _questions(attempt_id)   # 문항별 분석(모델 0회). 조회가 실패해도 메일은 나간다
    except Exception:
        traceback.print_exc()
        qas = []
    to = mailer.route(r["email"])      # 테스트 계정이면 TEST_MAIL_REDIRECT로(개발 전용)
    try:
        mailer.send(to, f"[headhunterAI] {r['job_title']} AI 면접 리포트",
                    render_text(r, items, facts, coach, qas), render(r, items, facts, coach, qas))
        db.run("INSERT INTO email_logs (attempt_id, user_id, kind, to_addr, status, writer) VALUES (%s,%s,'applicant_report',%s,'sent',%s)",
               attempt_id, r["user_id"], to, writer)
        return {"ok": True}
    except mailer.MailError as e:
        db.run("INSERT INTO email_logs (attempt_id, user_id, kind, to_addr, status, writer, error) VALUES (%s,%s,'applicant_report',%s,'failed',%s,%s)",
               attempt_id, r["user_id"], to, writer, str(e))
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
