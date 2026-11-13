"""사내문서 RAG 그래프 연결 단위 검사 (2026-11-13, ADLC v0.7 6.13.6~6.14) — 모델·DB·Redis·Chroma·langgraph 없이 호스트에서 돈다.

    cd backend && python -m tools.test_internal_rag_graph

가짜 llm.chat_json · 가짜 Chroma 검색(internal_store.query_public) · 가짜 금지 용어 사전으로 다음을 확인한다.
  R1 그래프 1-A 엣지: retrieve → retrieve_internal → generate … evaluate_answerability → guard_disclosure → finalize
  R2 근거 있음 → A15(company_question_rag) 프롬프트에 공개 조각이 주입되고, 기업 문항 gen_method에 |internal:<ref>
  R3 근거 없음(유사도 하한 미달) → A15 호출 없이 기존 contextualize, gen_method 그대로
  R4 관문 닫힘(rag_ready False · 사전 없음) / INTERNAL_DOCS_RAG=0 → 검색도 하지 않고 기존 경로
  R5 restricted 조각이 검색 결과에 섞임 → 컨텍스트 전체 폐기, 그 본문이 어떤 프롬프트에도 들어가지 않음
  R6 금지 용어가 본문에 남은 공개 조각 → search가 버림, 프롬프트에 없음
  R7 guard_disclosure: 금지 용어·근거 불일치·없는 조각 ID·모델 경고 → 그 문항만 은행 원문, 보고서에 사유 종류만
  R8 guard_disclosure: 모델 오류 → 규칙 결과만으로 통과 / A6이 원문으로 되돌린 문항 → 검사 대상 아님
  R10 일반 단어(주소): 'API 주소' 공개 조각은 버리지 않고 주입 · 질문 문장에 주소가 들어가면 7-B가 차단
  R11 pregenerate가 pregen_questions(JSONB)에 v2 {questions, guard_report, internal_refs} 저장(사유 종류·건수·조각 ID만)
      · 그래프 실패 대체 경로도 v2 · pregen_list가 옛 형식(list)·v2 둘 다 읽음
  R12 status 하드 필터에 resolved 추가(open 제외) · 마스킹 표식([금액] 등)·가명(고객사A)이 든 질문은 A15가 버리고 7-B가 원문으로
  R13 면접 방식·평가 기준·질문 설계를 묻는 질문(면접·평가 기준·루브릭·STAR 등) → A15 None · 7-B interview_meta
  R9 contextualize_rag 규칙: 지어낸 수치 · 컨텍스트 밖 사실 · 주입 안 한 doc_refs · 금지 용어 · doc_refs 비어 있음 → None
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


# ---------- 호스트에 없는 패키지 가짜 ----------
def _fake_modules():
    if "app.db" not in sys.modules:
        db = types.ModuleType("app.db")
        db.one = db.run = db.all_ = lambda *a, **k: None
        db.J = lambda x: x
        sys.modules["app.db"] = db
    try:
        import langsmith  # noqa: F401
    except ImportError:
        ls = types.ModuleType("langsmith")
        ls.traceable = lambda *a, **k: (a[0] if a and callable(a[0]) and not k else (lambda f: f))
        sys.modules["langsmith"] = ls
    try:
        import langgraph  # noqa: F401
        return False
    except ImportError:
        pass

    class _Compiled:
        def __init__(self, g):
            self.g = g

        def invoke(self, state, config=None, **kw):
            st, node = dict(state), self.g.next_of("__start__", state)
            while node != "__end__":
                st.update(self.g.nodes[node](st) or {})
                node = self.g.next_of(node, st)
            return st

    class StateGraph:
        def __init__(self, schema):
            self.nodes, self.edges, self.cond = {}, {}, {}

        def add_node(self, name, fn):
            self.nodes[name] = fn

        def add_edge(self, a, b):
            self.edges[a] = b

        def add_conditional_edges(self, a, fn, mapping=None):
            self.cond[a] = (fn, mapping or {})

        def next_of(self, a, st):
            if a in self.cond:
                fn, m = self.cond[a]
                k = fn(st)
                return m.get(k, k)
            return self.edges[a]

        def compile(self, **kw):
            c = _Compiled(self)
            c.graph = self
            return c

    lg = types.ModuleType("langgraph")
    g = types.ModuleType("langgraph.graph")
    g.StateGraph, g.START, g.END = StateGraph, "__start__", "__end__"
    cp = types.ModuleType("langgraph.checkpoint")
    mem = types.ModuleType("langgraph.checkpoint.memory")
    mem.InMemorySaver = type("InMemorySaver", (), {"__init__": lambda self, *a, **k: None})
    ty = types.ModuleType("langgraph.types")
    ty.Command = type("Command", (), {"__init__": lambda self, *a, **k: None})
    ty.interrupt = lambda *a, **k: None
    for name, mod in (("langgraph", lg), ("langgraph.graph", g), ("langgraph.checkpoint", cp),
                      ("langgraph.checkpoint.memory", mem), ("langgraph.types", ty)):
        sys.modules[name] = mod
    return True


FAKE_LG = _fake_modules()

from app import config, internal_store, llm, questions as Q  # noqa: E402
from app.agents import internal_forbid, internal_search, question_graph as QG  # noqa: E402

TERMS = ["대한전자", "한빛유통", "PRJ-CC", "INC-AI", "주소"]   # 주소 = 일반 단어(GENERIC_TERMS)
JOB = {"id": 3, "role_code": "ai_ax", "company_name": "헤드헌터랩_서울", "role_name": "AI/AX 엔지니어",
       "company_intro": "고객사 상담 문서 기반 RAG 서비스를 만드는 회사", "duties": "RAG 파이프라인 설계·개발",
       "requirements": "Python, LLM 경험"}
COMPANY_BANK = "우리 회사의 서비스나 사업 중 관심 있는 부분과 그 이유를 설명해 주세요."
PUB_BODY = "# 프로젝트 공개판 > ## 목표\n\n상담 문서 검색 품질을 매주 평가셋으로 점검한다."
TAL_BODY = "# 인재상 > ## 핵심 가치\n\n고객 문제를 끝까지 파고드는 집요함을 첫째 가치로 둔다."
SECRET_BODY = "# 상세판 > ## 계약\n\nA사 콜센터 계약 범위는 상담사 300석이다."   # 금지 용어가 없어 사전 검사로는 못 잡는 조각
API_BODY = "# 백엔드 JD > ## 운영\n\n개발·운영 환경의 API 주소와 인증정보를 분리한다."   # 일반 단어 주소만 있는 공개 조각
LEFTOVER_BODY = "# 공개판 > ## 사례\n\n한빛유통 매장 수요 예측 사례를 정리한다."
RAG_Q = "헤드헌터랩_서울에서 상담 문서 검색 품질을 매주 평가셋으로 점검하는 업무를 맡는다면 무엇을 먼저 확인할지 설명해 주세요."
RAG_FACT = "상담 문서 검색 품질을 매주 평가셋으로 점검한다"


def chunk(cid, doc_id, sec, body, disclosure="public", sim=0.8, status="active"):
    return {"id": cid, "document": body, "sim": sim,
            "metadata": {"doc_id": doc_id, "sec": sec, "doc_title": doc_id, "sec_title": sec, "disclosure": disclosure,
                         "status": status, "doc_type": "project", "dept": "|ai_ax|", "dept_ai_ax": True, "forbid_terms": ""}}


class Env:
    """가짜 환경 한 벌 — with 블록이 끝나면 원래 함수로 되돌린다."""

    def __init__(self, hits=(), ready=(True, ""), terms=TERMS, rag_answer=None, guard_answer=None, review=False, rag_on=True):
        self.hits, self.ready, self.terms = list(hits), ready, terms
        self.rag_answer = rag_answer or {"question": RAG_Q, "company_facts": [RAG_FACT], "doc_refs": ["PRJ-CC-PUB#s2"]}
        self.guard_answer = guard_answer or {"leak": False, "kinds": []}
        self.review, self.rag_on = review, rag_on
        self.prompts, self.searched, self.old_calls = [], 0, 0

    def chat(self, system, user, schema, **kw):
        self.prompts.append(system + "\n" + user)
        if "기밀 누출 검사관" in system:
            if isinstance(self.guard_answer, Exception):
                raise self.guard_answer
            return self.guard_answer
        if "doc_refs" in (schema.get("properties") or {}):
            if isinstance(self.rag_answer, Exception):
                raise self.rag_answer
            return self.rag_answer
        raise AssertionError("예상하지 않은 모델 호출")

    def query_public(self, vector, k=12, where=None):
        self.searched += 1
        return list(self.hits)

    def load_terms(self, r=None):
        if self.terms is None:
            raise internal_forbid.ForbidUnavailable("empty")
        return list(self.terms)

    def old_contextualize(self, q, job, intent=""):
        self.old_calls += 1
        return "OLD:" + q

    def __enter__(self):
        self._saved = [(llm, "chat_json"), (internal_store, "query_public"), (internal_forbid, "rag_ready"),
                       (internal_forbid, "load_terms"), (Q, "contextualize"), (Q, "contextualize_character"),
                       (Q, "contextualize_job"), (Q, "make_resume_question"), (Q, "pick_character"),
                       (QG, "REVIEW"), (config, "INTERNAL_DOCS_RAG"), (QG, "_redis_query_vector"), (QG, "_pick_redis"),
                       (QG, "_resume_bank_refs"), (QG.db, "one")]
        self._saved = [(m, n, getattr(m, n)) for m, n in self._saved]
        llm.chat_json = self.chat
        internal_store.query_public = self.query_public
        internal_forbid.rag_ready = lambda r=None: self.ready
        internal_forbid.load_terms = self.load_terms
        Q.contextualize = self.old_contextualize
        Q.contextualize_character = lambda item, job, snap: item["text"]
        Q.contextualize_job = lambda q, job, snap, intent="": q
        Q.make_resume_question = lambda cand, angle, snap, bank=None, role="": {"text": "이력서 질문입니다 설명해 주세요.",
                                                                                 "resume_ref": {}, "gen_method": "llm"}
        Q.pick_character = lambda n, used=(), snap=None: [{"id": None, "text": f"인성 질문 {i}번을 설명해 주세요.",
                                                            "subcategory": "협업", "dup_group": f"c{i}"} for i in range(n)]
        QG.REVIEW = self.review
        config.INTERNAL_DOCS_RAG = self.rag_on
        QG._redis_query_vector = lambda s: [0.1, 0.2, 0.3]
        QG._pick_redis = lambda v, role, cat, n, used: [{"id": None, "doc_id": f"{cat}:{i}",
                                                          "text": COMPANY_BANK if cat == "company" else f"{cat} 문항 {i}번을 설명해 주세요.",
                                                          "subcategory": "관심", "dup_group": f"{cat}{i}"} for i in range(n)]
        QG._resume_bank_refs = lambda s, role, used: [None, None, None]
        QG.db.one = lambda *a, **k: dict(JOB)
        return self

    def __exit__(self, *exc):
        for m, n, v in self._saved:
            setattr(m, n, v)


APP = {"id": 1, "job_id": 3, "resume_snapshot": {"projects": [{"name": "사내 검색 RAG", "role": "개발",
                                                                "detail": "LangChain으로 검색 파이프라인 구현", "tech": ["LangChain"]}]}}


def run_graph(env):
    with env:
        qs, info = QG.generate_set(APP, use_llm=True)
    return qs, info


def company(qs):
    return [q for q in qs if q["category"] == "company"][0]


def main():
    res = []
    pub = [chunk("PRJ-CC-PUB#001#0", "PRJ-CC-PUB", "s2", PUB_BODY), chunk("TAL-01#000#0", "TAL-01", "s1", TAL_BODY, sim=0.7)]

    # R1 엣지
    if FAKE_LG:
        e = QG.GENERATION.graph.edges
        res.append(("R1 엣지 retrieve→retrieve_internal→generate · answerability→guard→finalize",
                    e.get("retrieve") == "retrieve_internal" and e.get("retrieve_internal") == "generate"
                    and e.get("evaluate_answerability") == "guard_disclosure" and e.get("guard_disclosure") == "finalize"))

    # R2 근거 있음 → A15
    env = Env(hits=pub)
    qs, info = run_graph(env)
    c = company(qs)
    a15 = [p for p in env.prompts if "[사내문서 근거" in p]
    res.append(("R2 A15 프롬프트에 공개 조각 주입", len(a15) == 1 and "매주 평가셋" in a15[0] and "집요함" in a15[0]))
    res.append(("R2 기업 문항 = A15 결과, gen_method |internal:", c["text"] == RAG_Q and c["gen_method"].endswith("|internal:PRJ-CC-PUB#s2")
                and c["gen_method"].startswith("redis:company:")))
    res.append(("R2 검색 1회 · 기존 contextualize 0회 · 문항 8개", env.searched == 1 and env.old_calls == 0 and len(qs) == 8))
    res.append(("R2 guard_report 검사 1 · 차단 0", info["guard_report"].get("checked") == 1 and info["guard_report"].get("blocked") == 0))
    res.append(("R2 로그에 조각 본문 없음(B-08)", not any("매주 평가셋" in line for line in info["log"])))

    # R3 근거 없음(하한 미달)
    env = Env(hits=[chunk("X#000#0", "TAL-01", "s1", TAL_BODY, sim=0.40)])
    qs, info = run_graph(env)
    c = company(qs)
    res.append(("R3 근거 0조각 → 기존 contextualize, gen_method 그대로",
                c["text"] == "OLD:" + COMPANY_BANK and "internal" not in c["gen_method"] and env.old_calls == 1
                and not any("[사내문서 근거" in p for p in env.prompts)))

    # R4 관문 닫힘 · 사전 없음 · 스위치 끔
    for name, kw in (("rag_ready False", {"ready": (False, "audit_failed")}), ("사전 없음", {"terms": None}),
                     ("INTERNAL_DOCS_RAG=0", {"rag_on": False})):
        env = Env(hits=pub, **kw)
        qs, _ = run_graph(env)
        res.append((f"R4 {name} → 검색 안 함 · 기존 경로", env.searched == 0 and company(qs)["text"] == "OLD:" + COMPANY_BANK))

    # R5 restricted 조각 혼입
    env = Env(hits=pub + [chunk("PRJ-CC#003#0", "PRJ-CC", "s4", SECRET_BODY, disclosure="internal", sim=0.95)])
    qs, info = run_graph(env)
    res.append(("R5 restricted 혼입 → 컨텍스트 폐기 · 본문이 어떤 프롬프트에도 없음",
                company(qs)["text"] == "OLD:" + COMPANY_BANK and not any("300석" in p for p in env.prompts)
                and any("공개판이 아닌 조각" in line for line in info["log"])))

    # R6 금지 용어가 남은 공개 조각 → 버림
    env = Env(hits=pub + [chunk("PRJ-FC-PUB#002#0", "PRJ-FC-PUB", "s3", LEFTOVER_BODY, sim=0.9)])
    qs, _ = run_graph(env)
    a15 = [p for p in env.prompts if "[사내문서 근거" in p]
    res.append(("R6 금지 용어 남은 조각은 프롬프트에 없음", len(a15) == 1 and "한빛유통" not in a15[0] and "매주 평가셋" in a15[0]))

    # R10 일반 단어 '주소' — 조각은 통과(chunk_terms 제외), 질문 문장은 차단(terms 전부)
    env = Env(hits=pub + [chunk("JD-BE-01#004#0", "JD-BE-01", "s5", API_BODY, sim=0.75)])
    qs, _ = run_graph(env)
    a15 = [p for p in env.prompts if "[사내문서 근거" in p]
    res.append(("R10 'API 주소' 공개 조각은 버리지 않고 A15에 주입", len(a15) == 1 and "API 주소" in a15[0]))

    # R7 guard 차단
    base = {"job": dict(JOB), "use_llm": True, "forbid_terms": TERMS, "forbid_ready": True,
            "internal_hits": [{"text": PUB_BODY, "ref": "PRJ-CC-PUB#s2"}], "internal_refs": ["PRJ-CC-PUB#s2"],
            "internal_context": "[근거 PRJ-CC-PUB#s2 · x > y]\n" + PUB_BODY, "log": []}

    def draft(text=RAG_Q, facts=(RAG_FACT,), refs=("PRJ-CC-PUB#s2",)):
        return {"category": "company", "eval_items": Q.EVAL_ITEMS["company"], "text": text, "orig": COMPANY_BANK,
                "bank_id": None, "gen_method": "redis:company:0", "company_facts": list(facts), "doc_refs": list(refs)}

    cases = (("forbid_term", draft(text="대한전자 상담 문서 검색 품질을 매주 점검하는 업무를 맡는다면 어떻게 할지 설명해 주세요.")),
             ("facts_unmatched", draft(facts=("상담사 300석 규모로 운영한다",))),
             ("doc_ref_invalid", draft(refs=("TAL-02#s9",))),
             ("forbid_term", draft(text="상담 문서 검색 서버 주소를 어떻게 관리할지 설명해 주세요.")),
             ("rule", draft(text="상담 문서 검색 품질을 매주 평가셋으로 점검할 때 정확도 85%를 어떻게 지킬지 설명해 주세요.")))
    for reason, d in cases:
        with Env(review=False):
            out = QG.guard_disclosure({**base, "drafts": [d]})
        dd, rep = out["drafts"][0], out["guard_report"]
        res.append((f"R7 {reason} → 은행 원문 · 근거 필드 제거 · 사유만 기록",
                    dd["text"] == COMPANY_BANK and "doc_refs" not in dd and rep["blocked"] == 1 and reason in rep["reasons"]
                    and not any("대한전자" in line or "300석" in line for line in out["log"])))
    env = Env(review=True, guard_answer={"leak": True, "kinds": [1]})
    with env:
        out = QG.guard_disclosure({**base, "drafts": [draft()]})
    res.append(("R7 모델 보조 경고(model_flag) → 은행 원문", out["drafts"][0]["text"] == COMPANY_BANK
                and "model_flag" in out["guard_report"]["reasons"]))
    with Env(review=False):
        out = QG.guard_disclosure({**base, "forbid_ready": False, "forbid_terms": [], "drafts": [draft()]})
    res.append(("R7 사전 없음 → 사내문서 쓴 문항 되돌림(보수적)", out["drafts"][0]["text"] == COMPANY_BANK
                and "forbid_unavailable" in out["guard_report"]["reasons"]))

    # R8 모델 오류 → 규칙만 / A6이 되돌린 문항
    env = Env(review=True, guard_answer=llm.LLMError("down"))
    with env:
        out = QG.guard_disclosure({**base, "drafts": [draft()]})
    res.append(("R8 모델 오류 → 규칙 결과만으로 통과", out["drafts"][0]["text"] == RAG_Q and out["guard_report"]["model"] == "error"
                and out["drafts"][0]["guard"] == "pass"))
    with Env(review=False):
        out = QG.guard_disclosure({**base, "drafts": [draft(text=COMPANY_BANK)]})
    res.append(("R8 원문으로 되돌린 문항 → 검사 대상 아님 · 근거 필드 제거",
                out["guard_report"]["checked"] == 0 and "doc_refs" not in out["drafts"][0]))
    fin = QG.finalize({"drafts": [dict(draft(), guard="pass")], "log": []})["final_questions"][0]
    res.append(("R8 finalize gen_method에 조각 ID만", fin["gen_method"] == "redis:company:0|internal:PRJ-CC-PUB#s2"
                and set(fin) <= {"category", "eval_items", "text", "bank_id", "gen_method", "resume_ref"}))

    # R12 resolved 허용 · open 제외 · 마스킹 표식/가명 차단
    wheres = []
    env = Env(hits=[chunk("PRJ-CC-PUB#001#0", "PRJ-CC-PUB", "s2", PUB_BODY, status="resolved")])
    _qp = env.query_public
    env.query_public = lambda vector, k=12, where=None: (wheres.append(where), _qp(vector, k, where))[1]
    qs, _ = run_graph(env)
    st = [c["status"]["$in"] for w in wheres for c in w["$and"] if "status" in c]
    res.append(("R12 하드 필터 status = active·done·resolved (open 없음)",
                st == [["active", "done", "resolved"]] and company(qs)["gen_method"].endswith("|internal:PRJ-CC-PUB#s2")))
    env = Env(hits=[chunk("X#000#0", "X", "s1", PUB_BODY, status="open")])
    qs, info = run_graph(env)
    res.append(("R12 open 조각이 섞이면 컨텍스트 폐기", company(qs)["text"] == "OLD:" + COMPANY_BANK))
    for name, q in (("[금액]", "상담 문서 검색 품질을 [금액] 예산 안에서 매주 점검할 방법을 설명해 주세요."),
                    ("가명 고객사A", "고객사A의 상담 문서 검색 품질을 매주 평가셋으로 점검할 방법을 설명해 주세요.")):
        with Env(rag_answer={"question": q, "company_facts": [], "doc_refs": ["PRJ-CC-PUB#s2"]}):
            r = Q.contextualize_rag(COMPANY_BANK, JOB, "관심", base["internal_context"], base["internal_refs"], [PUB_BODY], TERMS)
        with Env(review=False):
            out = QG.guard_disclosure({**base, "drafts": [draft(text=q, facts=())]})
        res.append((f"R12 {name} → A15 None · 7-B mask_marker 차단", r is None and out["drafts"][0]["text"] == COMPANY_BANK
                    and "mask_marker" in out["guard_report"]["reasons"]))
    res.append(("R12 표식 없는 질문은 masked_hits 비어 있음", internal_forbid.masked_hits(RAG_Q) == []))
    with Env(review=False):
        out = QG.guard_disclosure({**base, "drafts": [draft(text="업체B와 상담 문서 검색 품질을 매주 점검할 방법을 설명해 주세요.", facts=())]})
    res.append(("R12 가명 업체B → 7-B mask_marker 차단", out["drafts"][0]["text"] == COMPANY_BANK
                and "mask_marker" in out["guard_report"]["reasons"]))
    with Env(review=False):
        out = QG.guard_disclosure({**base, "drafts": [draft(facts=("[금액] 규모로 운영한다",))]})
    res.append(("R12 company_facts에만 표식 → 7-B mask_marker 차단", out["drafts"][0]["text"] == COMPANY_BANK
                and "mask_marker" in out["guard_report"]["reasons"]))
    with Env(rag_answer={"question": "MSK-03 사례처럼 상담 문서 검색 품질을 점검할 방법을 설명해 주세요.",
                         "company_facts": [], "doc_refs": ["PRJ-CC-PUB#s2"]}):
        r = Q.contextualize_rag(COMPANY_BANK, JOB, "관심", base["internal_context"], base["internal_refs"], [PUB_BODY], TERMS)
    res.append(("R12 사본 ID(MSK-03) 노출 → A15 None", r is None))
    res.append(("R12 A15 프롬프트에 표식·가명 금지 줄", "'고객사A' 같은 가명은 질문에 쓰지 않는다" in Q.prompts.load("generate/company_question_rag")))

    # R13 면접 방식·평가 기준을 묻는 질문 차단
    for name, q in (("평가 기준", "헤드헌터랩_서울의 면접 평가 기준을 어떻게 설계하면 좋을지 설명해 주세요."),
                    ("STAR", "상담 문서 검색 업무 사례를 STAR 기법으로 질문하려면 어떻게 설계할지 설명해 주세요."),
                    ("루브릭", "매주 평가셋 점검 결과를 루브릭으로 채점하는 방법을 설명해 주세요.")):
        with Env(rag_answer={"question": q, "company_facts": [], "doc_refs": ["PRJ-CC-PUB#s2"]}):
            r = Q.contextualize_rag(COMPANY_BANK, JOB, "관심", base["internal_context"], base["internal_refs"], [PUB_BODY], TERMS)
        with Env(review=False):
            out = QG.guard_disclosure({**base, "drafts": [draft(text=q, facts=())]})
        res.append((f"R13 {name} → A15 None · 7-B interview_meta 차단", r is None and out["drafts"][0]["text"] == COMPANY_BANK
                    and "interview_meta" in out["guard_report"]["reasons"]))
    res.append(("R13 일반 업무 질문은 통과", not Q.asks_interview_method(RAG_Q)))
    ok_q = "수요 예측 대시보드 프로젝트에서 모델 평가 기준을 어떻게 정했는지 설명해 주세요."
    with Env(rag_answer={"question": ok_q, "company_facts": [], "doc_refs": ["PRJ-CC-PUB#s2"]}):
        r = Q.contextualize_rag(COMPANY_BANK, JOB, "관심", base["internal_context"], base["internal_refs"], [PUB_BODY], TERMS)
    res.append(("R13 '모델 평가 기준' 정상 질문 통과(A15 채택)", r is not None and r["text"] == ok_q))
    res.append(("R13 약한 키워드 + 채용 맥락만 차단",
                Q.asks_interview_method("지원자 답변의 채점 기준을 어떻게 정할지 설명해 주세요.")
                and not Q.asks_interview_method("데이터 품질 점검 항목의 채점 방식을 설명해 주세요.")))
    res.append(("R13 A15 프롬프트에 면접 방식 금지 줄", "면접 방법·평가 기준·질문 설계 자체를 묻는 질문은 만들지 않는다"
                in Q.prompts.load("generate/company_question_rag")))
    res.append(("R13 pregen 보고서가 interview_meta·mask_marker 사유를 보존",
                Q._safe_guard_report({"reasons": {"interview_meta": 1, "mask_marker": 2}})["reasons"] == {"interview_meta": 1, "mask_marker": 2}))

    # R11 pregenerate → pregen_questions v2
    saved, warmed = [], []
    speech = types.ModuleType("app.speech")
    speech.prewarm = lambda texts: warmed.extend(texts)
    sys.modules["app.speech"] = speech
    import app as _app
    _app.speech = speech
    orig = (Q.db.run, Q.db.one, QG.generate_set, Q.build_set)
    qs1 = [{"category": "company", "eval_items": [], "text": RAG_Q, "gen_method": "redis:company:0|internal:PRJ-CC-PUB#s2"}]
    dirty = {"guard_report": {"checked": 1, "blocked": 1, "reasons": {"forbid_term": 1, "대한전자 원문": 3}, "model": "ok",
                              "text": "대한전자 상담사 300석"},
             "internal_refs": ["PRJ-CC-PUB#s2", "대한전자 계약 범위"], "log": ["retrieve_internal: …"]}
    try:
        Q.db.run = lambda sql, *a: saved.append((sql, a))
        Q.db.one = lambda *a, **k: {"id": 7, "job_id": 3, "resume_snapshot": {}}
        QG.generate_set = lambda app, use_llm=True: (qs1, dirty)
        Q.pregenerate(7)
        ready = [a for sql, a in saved if "pregen_status='ready'" in sql]
        v = ready[0][0] if ready else {}
        res.append(("R11 v2 저장: questions·guard_report·internal_refs",
                    isinstance(v, dict) and v.get("v") == 2 and v["questions"] == qs1
                    and v["guard_report"] == {"checked": 1, "blocked": 1, "reasons": {"forbid_term": 1}, "model": "ok"}
                    and v["internal_refs"] == ["PRJ-CC-PUB#s2"]))
        res.append(("R11 저장본에 원문·금지 용어·로그 없음", "대한전자" not in repr(v) and "300석" not in repr(v) and "log" not in v
                    and warmed == [RAG_Q]))
        saved.clear()

        def boom(app, use_llm=True):
            raise RuntimeError("graph down")
        QG.generate_set, Q.build_set = boom, (lambda app, use_llm=True: qs1)
        Q.pregenerate(7)
        v = [a for sql, a in saved if "pregen_status='ready'" in sql][0][0]
        res.append(("R11 그래프 실패 대체 경로도 v2 · 빈 guard_report", v["questions"] == qs1 and v["guard_report"]["checked"] == 0
                    and v["internal_refs"] == []))
    finally:
        Q.db.run, Q.db.one, QG.generate_set, Q.build_set = orig
    res.append(("R11 pregen_list: 옛 list · v2 dict · None",
                Q.pregen_list(qs1) == qs1 and Q.pregen_list(Q.pack_pregen(qs1)) == qs1 and Q.pregen_list(None) == []))

    # R9 contextualize_rag 규칙
    ctx, refs, bodies = base["internal_context"], base["internal_refs"], [PUB_BODY]
    for name, ans, want_none in (
            ("통과", {"question": RAG_Q, "company_facts": [RAG_FACT], "doc_refs": ["PRJ-CC-PUB#s2"]}, False),
            ("지어낸 수치", {"question": "상담 문서 검색 품질을 매주 점검하며 정확도 85%를 지킬 방법을 설명해 주세요.",
                         "company_facts": [], "doc_refs": ["PRJ-CC-PUB#s2"]}, True),
            ("컨텍스트 밖 사실", {"question": RAG_Q, "company_facts": ["상담사 300석"], "doc_refs": ["PRJ-CC-PUB#s2"]}, True),
            ("주입 안 한 doc_refs", {"question": RAG_Q, "company_facts": [RAG_FACT], "doc_refs": ["HR-05#s1"]}, True),
            ("금지 용어", {"question": "대한전자 상담 문서 검색 품질을 어떻게 점검할지 설명해 주세요.",
                       "company_facts": [], "doc_refs": ["PRJ-CC-PUB#s2"]}, True),
            ("doc_refs 비어 있음", {"question": RAG_Q, "company_facts": [RAG_FACT], "doc_refs": []}, True),
            ("두 문장", {"question": "검색 품질을 점검합니다. 어떻게 할지 설명해 주세요.", "company_facts": [], "doc_refs": ["PRJ-CC-PUB#s2"]}, True)):
        with Env(rag_answer=ans):
            r = Q.contextualize_rag(COMPANY_BANK, JOB, "관심", ctx, refs, bodies, TERMS)
        res.append((f"R9 {name} → {'None' if want_none else '채택'}", (r is None) == want_none))
    with Env(rag_answer=llm.LLMError("down")):
        r = Q.contextualize_rag(COMPANY_BANK, JOB, "관심", ctx, refs, bodies, TERMS)
    res.append(("R9 모델 오류 → None", r is None))
    with Env():
        r = Q.contextualize_rag(COMPANY_BANK, JOB, "관심", ctx, refs, bodies, [])
    res.append(("R9 사전 비어 있음 → 호출 없이 None", r is None))

    for name, ok in res:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(ok for _, ok in res)
    print(f"{n}/{len(res)}" + ("  (langgraph 가짜로 실행)" if FAKE_LG else ""))
    return 0 if n == len(res) else 1


if __name__ == "__main__":
    sys.exit(main())
