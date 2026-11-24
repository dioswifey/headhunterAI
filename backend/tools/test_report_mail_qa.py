"""리포트 메일 '질문별 분석 + 모범답안' 단위 검사 (2026-11-18) — 모델·DB·Chroma·SMTP 없이 호스트에서 돈다.

    cd backend && python -m tools.test_report_mail_qa
    docker exec -w /srv headhunterAI-api python -m tools.test_report_mail_qa

가짜 DB 행으로 다음을 확인한다.
  M1 `_questions`: 기본 문항만 읽고 **꼬리질문은 들어오지 않는다**(질의에 parent_id IS NULL)
  M2 **금지어(합불·인적 특성)가 든 rationale·quote·모범답안은 걸러진다** — 사용자 요구의 핵심
  M3 지어낸 수치가 든 rationale도 걸러진다(기존 report_guard 규칙이 그대로 적용)
  M4 값이 없는 칸은 **렌더에 아예 나오지 않는다**(빈 칸 금지)
  M5 모범답안: 사내문서 질문(A17)은 넣고 · DB 은행·이력서는 **넣지 않는다**(D안) · Redis 영문은 쓰지 않는다
  M6 모범답안이 있는 문항과 없는 문항이 **한 메일에 섞여도** 레이아웃이 깨지지 않는다
  M7 꼬리질문은 분석에서 빠지고 **개수만** 남는다
  M8 합불 단어가 완성된 메일 본문(HTML·텍스트) 어디에도 없다
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if "app.db" not in sys.modules:          # 호스트에 psycopg가 없어도 import 되게
    _db = types.ModuleType("app.db")
    _db.one = _db.run = _db.all_ = lambda *a, **k: None
    _db.J = lambda x: x
    sys.modules["app.db"] = _db

from tools import test_internal_rag_graph as _T  # noqa: E402,F401  (langgraph·langsmith 가짜 설치)

from app import db, report_guard  # noqa: E402
from app import report_mail as RM  # noqa: E402

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))


# ---------------- 가짜 DB ----------------
BASE = [
    # A17 사내문서 질문 — 모범답안 있음
    {"id": 1, "seq": 1, "category": "job", "gen_method": "internal_q:IQ-job-abc-0|internal:DOC#s1",
     "text": "LLM 상담 자동화에서 예외 규칙을 어떻게 구조화하시겠습니까?",
     "answer_text": "용어집을 먼저 만들고 예외를 분리했습니다. 처리량은 120건이었습니다.", "answer_status": "answered"},
    # DB 은행 문항 — 모범답안 없음(칸이 빠져야 한다)
    {"id": 2, "seq": 2, "category": "job", "gen_method": "db",
     "text": "학습이 잘 되지 않을 때 확인하는 순서를 설명해 주세요.",
     "answer_text": "데이터부터 확인합니다.", "answer_status": "answered"},
    # 이력서 문항 — D안: 모범답안을 넣지 않는다
    {"id": 3, "seq": 3, "category": "resume", "gen_method": "llm|internal:DOC#s2",
     "text": "이력서에 적은 프로젝트에서 맡은 역할을 설명해 주세요.",
     "answer_text": "제가 설계와 구현을 맡았습니다.", "answer_status": "answered"},
    # Redis 문항 — 모범답안이 영문이면 쓰지 않는다
    {"id": 4, "seq": 4, "category": "company", "gen_method": "redis:ai_ax:ai-009",
     "text": "우리 회사 서비스 중 관심 있는 부분을 설명해 주세요.",
     "answer_text": "상담 자동화에 관심이 있습니다.", "answer_status": "answered"},
    # 답변이 없는 문항
    {"id": 5, "seq": 5, "category": "personality", "gen_method": "character:CA-001",
     "text": "협업 중 충돌을 어떻게 조율했는지 설명해 주세요.", "answer_text": None, "answer_status": "skipped"},
]
SCORES = [
    {"question_id": 1, "eval_item": "logic", "score": 4, "verified": True,
     "quote": "용어집을 먼저 만들고 예외를 분리했습니다", "rationale": "주장과 근거가 이어집니다."},
    {"question_id": 1, "eval_item": "relevance", "score": 3, "verified": True,
     "quote": "예외를 분리했습니다", "rationale": "질문 의도에 맞게 답했습니다."},
    # M2 금지어가 든 사유 → 걸러져야 한다
    {"question_id": 2, "eval_item": "logic", "score": 2, "verified": True,
     "quote": "데이터부터 확인합니다", "rationale": "이 답변으로는 불합격 가능성이 높습니다."},
    # M3 지어낸 수치(답변·질문에 없는 97) → 걸러져야 한다
    {"question_id": 2, "eval_item": "relevance", "score": 2, "verified": True,
     "quote": "데이터부터", "rationale": "정확도를 97%까지 올린 근거가 없습니다."},
    # M2 금지어가 든 인용 → 인용 칸이 빠져야 한다
    {"question_id": 3, "eval_item": "logic", "score": 3, "verified": True,
     "quote": "출신 학교를 말씀드리면", "rationale": "역할 설명이 구체적입니다."},
    {"question_id": 4, "eval_item": "fit", "score": 3, "verified": True,
     "quote": "상담 자동화에 관심이 있습니다", "rationale": "지원 동기가 드러납니다."},
]
IDEALS = {"internal_q:IQ-job-abc-0": "예외 규칙을 유형별로 나누고 판단 기준을 문서로 남기는 답변이 좋습니다.",
          "redis:ai_ax:ai-009": "I would separate exceptions by type and document the criteria clearly.",
          "llm|internal:DOC#s2": "이력서 문항에는 모범답안을 쓰지 않는다(이 값이 쓰이면 D안 위반)."}


class Env:
    """db.all_ 과 question_graph.bank_reference 를 가짜로 바꾼다."""

    def __init__(self, base=None, scores=None, forbidden_ideal=False):
        self.base = BASE if base is None else base
        self.scores = SCORES if scores is None else scores
        self.forbidden_ideal = forbidden_ideal
        self.ref_calls = []

    def all_(self, sql, *a):
        return list(self.base) if "ORDER BY q.seq" in sql else list(self.scores)

    def bank_reference(self, q):
        gm = q.get("gen_method") or ""
        self.ref_calls.append(gm)
        if self.forbidden_ideal:
            return {"keywords": [], "ideal": "합격에 가까운 답변입니다."}
        for k, v in IDEALS.items():
            if gm.startswith(k.split("|")[0]):
                return {"keywords": [], "ideal": v}
        return {"keywords": [], "ideal": ""}

    def __enter__(self):
        from app.agents import question_graph as QG
        self._saved = [(db, "all_", db.all_), (QG, "bank_reference", QG.bank_reference)]
        db.all_ = self.all_
        QG.bank_reference = self.bank_reference
        return self

    def __exit__(self, *e):
        for m, n, v in self._saved:
            setattr(m, n, v)


R = {"name": "이지원", "company": "헤드헌터랩", "job_title": "AI/AX 엔지니어", "completed_at": None,
     "interview_score": 52.0, "strengths": ["질문 의도에 맞게 답했습니다."], "improvements": ["근거를 수치로 들어 보세요."]}
ITEMS = [("논리성", 60.0), ("관련성", 50.0), ("전문성", 45.0), ("전달력", 40.0), ("적합성", 55.0)]
FACTS = {"answered": 4, "base": 5, "followups": 3, "coding": None}
COACH = {"coaching": "항목별로 근거를 들어 답하는 연습을 해 보세요."}


def main():
    with Env() as env:
        qas = RM._questions(7)

    check("M1 기본 문항 5개만(꼬리질문 없음)", len(qas) == 5, str(len(qas)))
    q1, q2, q3, q4, q5 = qas

    check("M2 금지어 든 사유는 걸러진다(Q2 '불합격' 문장 제외)",
          all("불합격" not in t for _, t in q2["notes"]), str(q2["notes"]))
    check("M3 지어낸 수치 든 사유도 걸러진다(Q2 '97%' 문장 제외)",
          all("97" not in t for _, t in q2["notes"]), str(q2["notes"]))
    check("M2+M3 → Q2는 남는 사유가 0개", q2["notes"] == [], str(q2["notes"]))
    check("M9 사유가 0개면 인용도 빼고 점수만 남긴다(2026-11-18 결정)",
          q2["quote"] == "" and q2["items"], f"quote={q2['quote']!r} items={q2['items']}")
    check("M9 걸러진 건수가 기록된다(Q2 사유 2건·사유 전부 탈락)",
          q2["filtered"] == {"notes": 2, "quote": True, "all_notes_dropped": True}, str(q2["filtered"]))
    check("M9 정상 문항은 걸러진 것 없음(Q1)",
          q1["filtered"] == {"notes": 0, "quote": False, "all_notes_dropped": False}, str(q1["filtered"]))
    check("M9 점수 행이 없는 문항은 all_notes_dropped=False(Q5)",
          q5["filtered"]["all_notes_dropped"] is False, str(q5["filtered"]))
    check("M2 금지어 든 인용은 빠진다(Q3 '출신 학교')", q3["quote"] == "", repr(q3["quote"]))
    check("M1 정상 사유·인용은 남는다(Q1)", len(q1["notes"]) == 2 and q1["quote"], str(q1))

    check("M5 A17 문항은 모범답안이 들어간다", q1["model_answer"].startswith("예외 규칙을 유형별로"), repr(q1["model_answer"]))
    check("M5 DB 은행 문항은 모범답안 없음", q2["model_answer"] == "", repr(q2["model_answer"]))
    check("M5 이력서 문항은 모범답안 없음(D안)", q3["model_answer"] == "", repr(q3["model_answer"]))
    check("M5 Redis 영문 모범답안은 쓰지 않는다", q4["model_answer"] == "", repr(q4["model_answer"]))
    check("M5 이력서·DB·인성 문항은 은행 조회조차 하지 않는다",
          env.ref_calls == ["internal_q:IQ-job-abc-0|internal:DOC#s1", "redis:ai_ax:ai-009"], str(env.ref_calls))
    check("M1 답변 없는 문항은 answered=False", q5["answered"] is False and q5["notes"] == [], str(q5))

    # 모범답안 자체에 금지어가 있으면 버린다
    with Env(forbidden_ideal=True):
        bad = RM._questions(7)
    check("M2 모범답안에 금지어가 있으면 버린다", all(q["model_answer"] == "" for q in bad),
          str([q["model_answer"] for q in bad]))

    # ---------------- 렌더 ----------------
    html_out = RM.render(R, ITEMS, FACTS, COACH, qas)
    text_out = RM.render_text(R, ITEMS, FACTS, COACH, qas)

    check("M4 빈 인용 칸은 렌더에 없다(‘내 답변에서 —’는 인용 있는 문항 수만큼)",
          html_out.count("내 답변에서 —") == sum(1 for q in qas if q["quote"]), str(html_out.count("내 답변에서 —")))
    check("M4 모범답안 칸은 있는 문항만", html_out.count("이렇게 답하면 좋습니다") == 1,
          str(html_out.count("이렇게 답하면 좋습니다")))
    check("M4 사유가 0개인 문항에는 목록을 그리지 않는다",
          "<ul" in html_out and html_out.count("<ul") == sum(1 for q in qas if q["notes"]),
          f"ul {html_out.count('<ul')} vs notes {sum(1 for q in qas if q['notes'])}")
    check("M6 섞여도 문항 블록은 5개", html_out.count("Q1 ·") + html_out.count("Q2 ·") + html_out.count("Q3 ·")
          + html_out.count("Q4 ·") + html_out.count("Q5 ·") == 5)
    check("M6 HTML 표가 닫힌다(<table> 수 = </table> 수)",
          html_out.count("<table") == html_out.count("</table>"),
          f"{html_out.count('<table')} vs {html_out.count('</table>')}")
    check("M7 꼬리질문은 개수만", "꼬리질문 3개는 채점 대상이 아니라 분석에서 제외" in html_out)
    check("M7 꼬리질문 본문은 분석에 없다", "Q6 ·" not in html_out)

    # M8 — 완성된 메일에 합불 단어가 없다
    bad_html = report_guard.FORBIDDEN.findall(html_out)
    bad_text = report_guard.FORBIDDEN.findall(text_out)
    # 기존 고지 문구의 '1차 합격 여부와 관계없이'는 제품 사양이므로 그 구절만 빼고 본다
    strip = lambda s: s.replace("1차 합격 여부와 관계없이", "")  # noqa: E731
    check("M8 HTML 본문에 합불·인적 특성 단어 없음(고지 문구 제외)",
          not report_guard.FORBIDDEN.search(strip(html_out)), str(bad_html))
    check("M8 텍스트 본문에 합불·인적 특성 단어 없음(고지 문구 제외)",
          not report_guard.FORBIDDEN.search(strip(text_out)), str(bad_text))
    check("M8 텍스트 본문에도 문항별 분석이 들어간다",
          "[Q1 · 직무 이해도]" in text_out and "이렇게 답하면 좋습니다:" in text_out)

    # 분석이 아예 없을 때(평가 전) 섹션을 그리지 않는다
    none_html = RM.render(R, ITEMS, FACTS, COACH, [])
    check("M4 문항 분석이 없으면 섹션 자체가 없다", "질문별 분석" not in none_html)

    print(f"\n{len(OK)}/{len(OK) + len(FAIL)}" + (f"  실패: {FAIL}" if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
