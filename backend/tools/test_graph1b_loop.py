"""그래프 1-B 면접 루프 + 꼬리질문 판단 ②③ 단위 검사 (2026-11-13) — 모델·DB·Redis 없이 돈다(가짜 db·llm·redis).

langgraph가 필요하다. 호스트에 없으면 api 컨테이너의 임시 사본에서 돌린다(서버 코드·재시작과 무관):
    docker exec headhunterAI-api rm -rf /tmp/g1b && docker cp backend/. headhunterAI-api:/tmp/g1b
    docker exec -w /tmp/g1b headhunterAI-api python -m tools.test_graph1b_loop
(Git Bash면 MSYS_NO_PATHCONV=1)

  J1 keyword_groups·keyword_hits (동의어 |, 대소문자·띄어쓰기 무시)
  J2 ② 기대 키워드 0개 → 꼬리질문, 사유 '기대 키워드 0개'
  J3 힌트 금지: 꼬리질문에 지원자가 말하지 않은 기대 키워드 → 생략 (원 질문에 있던 말은 허용)
  J4 ③ 모범 답변 있으면 PRE_SYSTEM_IDEAL·specificity 스키마, 2점 이하 → 사유 '모범 답변 대비 구체성 2점 이하'
  J5 모범 답변·키워드 없으면 기존 PRE_SYSTEM·PRE_SCHEMA 그대로(eval_followup 기준선 유지)
  J6 ②가 걸려도 모델 오류면 생략 / ① 생략 규칙(꼬리질문·4개 소진·내용 없음)은 모델 호출 0회
  J7 이력서 질문: 근거 인용의 영문 기술 토큰이 키워드, 숫자는 제외 / Redis 조회 실패 → ②③ 없이 판단
  L1 루프: 첫 답변에서 DB로 루프 생성 → 꼬리질문 삽입 → ask_question이 꼬리질문에서 대기 → 다음 질문 → 끝(END·체크포인트 삭제)
  L2 체크포인트 유실(재시작)·어긋남 → DB로 다시 만들어 이어감
  L3 질문당 1개·전체 4개 상한(make_followup DB 재확인 포함)
  L4 루프 실패 → 한 걸음 그래프 대체 경로, 답변 저장·전이 유지
  L5 마지막 답변 + 코딩 사용 → coding 전환
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for name in ("coding", "scheduler", "speech"):            # 부작용 있는 모듈은 가짜로
    sys.modules.setdefault(f"app.{name}", types.ModuleType(f"app.{name}"))
if "app.db" not in sys.modules:
    sys.modules["app.db"] = types.ModuleType("app.db")
try:
    import langsmith  # noqa: F401
except ImportError:
    _ls = types.ModuleType("langsmith")
    _ls.traceable = lambda *a, **k: (a[0] if a and callable(a[0]) and not k else (lambda f: f))
    sys.modules["langsmith"] = _ls

import datetime as dt  # noqa: E402
from decimal import Decimal  # noqa: E402

from app import config, llm  # noqa: E402
from app.agents import question_graph as G  # noqa: E402

coding_mod, sched_mod, speech_mod = sys.modules["app.coding"], sys.modules["app.scheduler"], sys.modules["app.speech"]
EVENTS = []
coding_mod.assign_problems = lambda aid: EVENTS.append(("assign", aid))
sched_mod.finish_attempt = lambda aid, st, why: EVENTS.append(("finish", aid, st, why))
speech_mod.prewarm = lambda xs: None


# ---------------- 가짜 DB ----------------
class FakeDB:
    def __init__(self, coding=False, used=0, n=3, gen=None):
        self.att = {"id": 7, "status": "interview", "followups_used": used, "application_id": 1, "user_id": 1, "job_id": 1,
                    "criteria_snapshot": {"coding_enabled": coding}, "started_at": dt.datetime(2026, 11, 13, 10, 0),
                    "score": Decimal("1.5")}
        self.qs, self.next_id = [], 100
        for i in range(n):
            self._add(seq=i + 1, parent=None, cat="job", text=f"질문 {i + 1}: 검색 품질을 어떻게 평가했나요?",
                      gen=(gen[i] if gen else "db"))

    def _add(self, seq, parent, cat, text, gen="db"):
        self.next_id += 1
        self.qs.append({"id": self.next_id, "attempt_id": 7, "seq": seq, "parent_id": parent, "category": cat, "eval_items": ["job"],
                        "text": text, "resume_ref": None, "gen_method": gen, "time_limit": 90, "answer_text": None,
                        "answer_status": "pending", "answer_source": None, "prescore": None})
        return self.next_id

    def q(self, qid):
        return next(x for x in self.qs if x["id"] == qid)

    def pending(self):
        p = [x for x in self.qs if x["answer_status"] == "pending"]
        p.sort(key=lambda x: (x["seq"], x["parent_id"] is not None, x["parent_id"] or 0, x["id"]))
        return dict(p[0]) if p else None

    def one(self, sql, *a):
        if "FROM attempts a JOIN applications" in sql:
            return dict(self.att)
        if "answer_status='pending'" in sql:
            return self.pending()
        if "SELECT 1 AS x FROM attempt_questions WHERE parent_id" in sql:
            return {"x": 1} if any(x["parent_id"] == a[0] for x in self.qs) else None
        if "SELECT followups_used FROM attempts" in sql:
            return {"followups_used": self.att["followups_used"]}
        if sql.strip().startswith("INSERT INTO attempt_questions"):
            aid, seq, parent, items, text, tl = a
            return {"id": self._add(seq, parent, "followup", text, "llm")}
        if "SELECT answer_status FROM attempt_questions" in sql:
            return {"answer_status": self.q(a[0])["answer_status"]}
        raise AssertionError("unexpected one: " + sql)

    def run(self, sql, *a):
        if "SET answer_text" in sql:
            q = self.q(a[3])
            q.update(answer_text=a[0], answer_status=a[1], answer_source=a[2])
        elif "SET prescore" in sql:
            self.q(a[1])["prescore"] = a[0]
        elif "followups_used=followups_used+1" in sql:
            self.att["followups_used"] += 1
        elif "status='coding'" in sql:
            self.att["status"] = "coding"
        else:
            raise AssertionError("unexpected run: " + sql)


def use_db(fdb):
    G.db.one, G.db.run, G.db.J = fdb.one, fdb.run, (lambda x: x)


# ---------------- 가짜 llm·redis ----------------
CALLS = []


def fake_llm(results):
    def f(system, user, schema, **kw):
        CALLS.append((system, user, schema))
        r = results.pop(0) if results else ok_result()
        if isinstance(r, Exception):
            raise r
        return r
    llm.chat_json = f


def ok_result(**kw):
    r = {"question_topic": "", "answer_topic": "", "addresses_question": True, "logic": 4, "relevance": 4,
         "inconsistent_with_resume": False, "generic_only": False, "followup_question": "그때 직접 무엇을 했고 결과가 어땠나요?"}
    r.update(kw)
    return r


class FakeRedisMod(types.ModuleType):
    def __init__(self, data, fail=False):
        super().__init__("redis")
        self.data, self.fail = data, fail
        mod = self

        class R:
            @staticmethod
            def from_url(*a, **k):
                return R()

            def hmget(self, key, *fields):
                if mod.fail:
                    raise ConnectionError("down")
                h = mod.data.get(key, {})
                return [h.get(f, "").encode() if f in h else None for f in fields]
        self.Redis = R


def set_redis(data, fail=False):
    sys.modules["redis"] = FakeRedisMod(data, fail)


ATT0 = {"id": 0, "followups_used": 0}


def Qd(text="RAG 검색 품질을 어떻게 평가했나요?", gen="db", parent=None, cat="job", quote=None):
    return {"id": 1, "parent_id": parent, "text": text, "resume_ref": ({"quote": quote} if quote else None), "seq": 1,
            "eval_items": [], "gen_method": gen, "category": cat}


FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def test_judge():
    g = G.keyword_groups("Recall@k|재현율, 재순위|rerank;평가 세트")
    check("J1 groups", g == [["Recall@k", "재현율"], ["재순위", "rerank"], ["평가 세트"]], g)
    check("J1 hits", G.keyword_hits(g, "RECALL@K를 쟀고 평가세트를 만들었다") == ["Recall@k", "평가 세트"])

    set_redis({"qb:ai_ax:x:01": {"expected_keywords": "Recall@k|재현율, 재순위", "model_answer": ""}})
    CALLS.clear(); fake_llm([ok_result()])
    d = G.judge_detail(ATT0, Qd(gen="redis:ai_ax:x:01"), "열심히 평가하고 팀과 많이 소통하며 개선했습니다.")
    check("J2 kw zero → followup", d["followup"] and d["followup"][1] == "기대 키워드 0개" and d["keyword_hits"] == [], d)
    check("J5 no ideal → base prompt", CALLS[-1][0] is G.PRE_SYSTEM and CALLS[-1][2] is G.PRE_SCHEMA)

    CALLS.clear(); fake_llm([ok_result()])
    d = G.judge_detail(ATT0, Qd(gen="redis:ai_ax:x:01"), "재현율로 평가했고 0.62에서 0.81로 올렸습니다.")
    check("J2 kw hit → no followup", d["followup"] is None and d["keyword_hits"] == ["Recall@k"], d)

    fake_llm([ok_result(followup_question="재순위를 적용해 본 적이 있나요?")])
    d = G.judge_detail(ATT0, Qd(gen="redis:ai_ax:x:01"), "열심히 평가하고 팀과 많이 소통하며 개선했습니다.")
    check("J3 hint ban", d["followup"] is None, d)
    fake_llm([ok_result(followup_question="RAG 검색 품질을 평가할 때 직접 무엇을 했나요?")])
    d = G.judge_detail(ATT0, Qd(text="재순위를 포함해 RAG 검색 품질을 어떻게 평가했나요?", gen="redis:ai_ax:x:01"),
                       "열심히 평가하고 팀과 많이 소통하며 개선했습니다.")
    check("J3 question words allowed", d["followup"] is not None, d)

    set_redis({"qb:ai_ax:x:02": {"expected_keywords": "", "model_answer": "평가 세트를 만들고 Recall@k를 측정해 개선 전후를 비교"}})
    CALLS.clear(); fake_llm([ok_result(specificity=2)])
    d = G.judge_detail(ATT0, Qd(gen="redis:ai_ax:x:02|internal:d1#s1"), "여러 방법으로 평가해서 품질이 좋아졌습니다.")
    sys_, user_, schema_ = CALLS[-1]
    check("J4 ideal prompt", sys_ is G.PRE_SYSTEM_IDEAL and "specificity" in schema_["required"] and "모범 답변 요지" in user_)
    check("J4 low specificity → followup", d["followup"] and d["followup"][1] == "모범 답변 대비 구체성 2점 이하" and d["specificity"] == 2, d)
    fake_llm([ok_result(specificity=4)])
    d = G.judge_detail(ATT0, Qd(gen="redis:ai_ax:x:02"), "120개 평가 세트로 Recall@5를 0.62→0.81로 올렸습니다.")
    check("J4 specific → none", d["followup"] is None and d["specificity"] == 4, d)

    set_redis({"qb:ai_ax:x:01": {"expected_keywords": "Recall@k|재현율", "model_answer": ""}})
    CALLS.clear(); fake_llm([llm.LLMError("timeout")])
    d = G.judge_detail(ATT0, Qd(gen="redis:ai_ax:x:01"), "열심히 평가하고 개선했습니다.")
    check("J6 llm error → skip even if kw zero", d["followup"] is None and len(CALLS) == 1)
    CALLS.clear()
    a = G.judge_detail(ATT0, Qd(parent=5), "열심히 평가하고 개선했습니다.")["followup"]
    b = G.judge_detail({"id": 0, "followups_used": 4}, Qd(), "열심히 평가하고 개선했습니다.")["followup"]
    c = G.judge_detail(ATT0, Qd(), "모르겠습니다.")["followup"]
    check("J6 skip rules, 0 llm calls", a is None and b is None and c is None and not CALLS)

    q = Qd(cat="resume", quote="2024 사내 RAG 챗봇 (Python, LangChain, Redis) 응답 30% 단축")
    check("J7 resume keywords", G._resume_keywords(q) == [["RAG"], ["Python"], ["LangChain"], ["Redis"]], G._resume_keywords(q))
    fake_llm([ok_result()])
    d = G.judge_detail(ATT0, q, "랭체인으로 챗봇을 만들었고 응답 속도를 개선했습니다.")
    check("J7 resume kw zero → followup", d["followup"] and d["followup"][1] == "기대 키워드 0개", d)
    set_redis({}, fail=True)
    CALLS.clear(); fake_llm([ok_result(generic_only=True)])
    d = G.judge_detail(ATT0, Qd(gen="redis:ai_ax:x:01"), "열심히 평가하고 개선했습니다.")
    check("J7 redis down → base judge", d["keyword_hits"] is None and d["followup"][1] == "일반론" and CALLS[-1][0] is G.PRE_SYSTEM, d)
    check("judge contract", G.judge(ATT0, Qd(parent=1), "x" * 10) is None)


def cfg():
    return {"configurable": {"thread_id": G._thread(7)}}


def test_loop():
    set_redis({}, fail=True)
    # L1 + L2
    fdb = FakeDB(n=3); use_db(fdb); EVENTS.clear(); G.discard(7)
    q1, q2, q3 = [x["id"] for x in fdb.qs]
    fake_llm([ok_result(generic_only=True)])                         # q1 → 꼬리질문
    G.answer_step(fdb.att, fdb.pending(), "그냥 열심히 했습니다 항상요", "typed")
    fu = [x for x in fdb.qs if x["parent_id"] == q1]
    check("L1 followup inserted", len(fu) == 1 and fdb.att["followups_used"] == 1 and fdb.q(q1)["prescore"]["followup_reason"] == "일반론")
    check("L1 waiting on followup", G.waiting_on(cfg()) == fu[0]["id"], G.waiting_on(cfg()))
    st = G.INTERVIEW_LOOP.get_state(cfg()).values
    check("L1 qa_pairs in state", len(st.get("qa_pairs", [])) == 1 and st["qa_pairs"][0]["followup_reason"] == "일반론", st.get("qa_pairs"))
    n_calls = len(CALLS)
    G.answer_step(fdb.att, fdb.pending(), "구체적으로 120개 세트를 만들었습니다", "typed")   # 꼬리질문 답변 → 판단 안 함
    check("L1 followup answer: no judge, wait q2", len(CALLS) == n_calls and G.waiting_on(cfg()) == q2)
    G.CHECKPOINTER.delete_thread(G._thread(7))                       # L2 재시작 흉내
    fake_llm([ok_result()])
    G.answer_step(fdb.att, fdb.pending(), "Recall 0.62에서 0.81로 올렸습니다", "typed")
    check("L2 rebuilt after loss, wait q3", G.waiting_on(cfg()) == q3 and fdb.q(q2)["answer_status"] == "answered")
    fake_llm([ok_result()])
    G.answer_step(fdb.att, fdb.pending(), "", None)                  # 무응답
    check("L1 end: finished + thread deleted", ("finish", 7, "completed", "interview_done") in EVENTS
          and fdb.q(q3)["answer_status"] == "no_answer" and not G.INTERVIEW_LOOP.get_state(cfg()).next
          and not G.INTERVIEW_LOOP.get_state(cfg()).values)

    # L2 어긋남: 체크포인트는 q1 대기, DB는 q2가 현재(다른 경로로 q1이 저장됨)
    fdb = FakeDB(n=2); use_db(fdb); G.discard(7)
    q1, q2 = [x["id"] for x in fdb.qs]
    G.INTERVIEW_LOOP.invoke({"attempt_id": 7, "qa_pairs": []}, cfg())
    fdb.q(q1)["answer_status"] = "answered"
    fake_llm([ok_result()])
    G.answer_step(fdb.att, fdb.pending(), "충분히 구체적인 답변입니다", "typed")
    check("L2 stale checkpoint → rebuilt", fdb.q(q2)["answer_status"] == "answered")

    # L3 상한: 이미 4개 사용 → 꼬리질문 없음 / make_followup DB 재확인
    fdb = FakeDB(n=2, used=4); use_db(fdb); G.discard(7)
    fake_llm([ok_result(generic_only=True)] * 3)
    G.answer_step(fdb.att, fdb.pending(), "그냥 열심히 했습니다 항상요", "typed")
    check("L3 cap 4", not any(x["parent_id"] for x in fdb.qs) and fdb.att["followups_used"] == 4)
    fdb = FakeDB(n=1); use_db(fdb)
    q1 = fdb.qs[0]["id"]
    fdb._add(1, q1, "followup", "이미 있는 꼬리질문")
    r = G.make_followup({"att": fdb.att, "q": fdb.q(q1), "proposal": {"question": "또?", "reason": "일반론"}})
    check("L3 one per question (DB recheck)", r["followup_id"] is None and len(fdb.qs) == 2)

    # L4 루프 실패 → 한 걸음 그래프
    fdb = FakeDB(n=2); use_db(fdb); G.discard(7)
    orig = G.waiting_on
    G.waiting_on = lambda c: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        fake_llm([ok_result(generic_only=True)])
        G.answer_step(fdb.att, fdb.pending(), "그냥 열심히 했습니다 항상요", "typed")
    finally:
        G.waiting_on = orig
    q1 = fdb.qs[0]["id"]
    check("L4 fallback saved + followup", fdb.q(q1)["answer_status"] == "answered" and any(x["parent_id"] == q1 for x in fdb.qs))

    # L5 코딩 전환
    fdb = FakeDB(n=1, coding=True); use_db(fdb); G.discard(7); EVENTS.clear()
    fake_llm([ok_result()])
    G.answer_step(fdb.att, fdb.pending(), "충분히 구체적인 답변입니다", "typed")
    check("L5 coding", fdb.att["status"] == "coding" and ("assign", 7) in EVENTS and not G.INTERVIEW_LOOP.get_state(cfg()).next)


if __name__ == "__main__":
    config.FOLLOWUP_TIMEOUT = 5
    test_judge()
    test_loop()
    print(f"\n{'OK' if not FAILS else 'FAILED'} — {len(FAILS)} fail")
    sys.exit(1 if FAILS else 0)
