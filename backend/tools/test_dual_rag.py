"""Dual RAG(Redis 질문 은행 + Chroma 사내문서) 그래프 1-A 통합 검사 (2026-11-16, ADLC v0.8 6.18) — 모델·DB·Redis·Chroma 없이 돈다.

    cd backend && python -m tools.test_dual_rag            # 호스트(langgraph 없으면 가짜 그래프)
    docker exec -w /srv headhunterAI-api python -m tools.test_dual_rag   # 컨테이너(진짜 langgraph)

test_internal_rag_graph의 가짜 환경(Env)을 그대로 쓰고, 그 위에 직무 문항 Dual RAG 응답·Multi-Query 임베딩·장애를 더한다.
  D1 검증 노드 유지: start → extract_keywords → evaluate_trust → retrieve → retrieve_internal → generate
     → evaluate_philosophy → evaluate_answerability → guard_disclosure → finalize
  D2 Dual(Redis 성공 + Chroma 성공, Multi-Query 3개 + RRF): Redis 검색은 벡터 3개 RRF, Chroma는 쿼리 3개로 3번 검색,
     직무 2·기업 1 문항이 사내문서 근거로 생성, 직무 프롬프트에 4재료(은행 문항·조각·공고·이력서)가 구분돼 들어감, 출처 ID 보존
  D3 Redis 실패 + Chroma 성공 → DB 은행 문항 + 사내문서 맥락으로 생성(chroma_only), 8문항
  D4 Redis 성공 + Chroma 실패 → 사내문서 없이 Redis 기반(redis_only), 8문항, 기존 섞어 쓰기
  D5 둘 다 실패(임베딩 실패) → 안전 경로(safe), 8문항, 사내문서 프롬프트 0
  D6 restricted 조각 혼입(Multi-Query 경로) → 컨텍스트 폐기, 그 본문이 어떤 프롬프트·최종 질문에도 없음
  D7 지원자 경로의 Chroma 검색 함수는 public 컬렉션 고정(코드 검사) · 그래프·검색 모듈이 restricted 이름을 참조하지 않음
  D8 허위 경험 전제(사내문서 사실 + '진행하셨던') → 직무 Dual RAG 거절 → 기존 섞어 쓰기
  D9 이력서·공고·조각에 없는 기술명(지어낸 경험) → 거절
  D10 7-B: 직무 Dual RAG 문항에 금지 용어 → 은행 원문, 이력서 토큰만 쓴 정상 문항은 통과
  D11 RAG_DUAL_JOB=0 → 직무 문항은 기존 경로(사내문서 프롬프트 없음), 기업 문항만 A15
  D12 Multi-Query 묶음 임베딩 실패 → 단일 쿼리로 내려가도 Dual RAG 유지
"""
import inspect
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tools import test_internal_rag_graph as T  # noqa: E402  (가짜 모듈 설치 포함)

from app import config, internal_store, llm, questions as Q  # noqa: E402
from app.agents import internal_search, question_graph as QG  # noqa: E402

try:
    from app.agents import question_chain as _QC  # noqa: F401
except Exception:  # noqa: BLE001 — 호스트에 langchain_core가 없으면 쿼리 조립만 하는 가짜
    qc = types.ModuleType("app.agents.question_chain")
    qc.QUERY_INSTRUCT = "Instruct: q\nQuery: "
    qc._query = lambda d: " ".join([d["job"].get("role_name") or "", d["job"].get("duties") or ""] + list(d.get("resume_keywords") or []))
    sys.modules["app.agents.question_chain"] = qc
    import app.agents as _pkg
    _pkg.question_chain = qc

JOB_BANK = "job 문항 {i}번을 설명해 주세요."
JOB_RAG_Q = "헤드헌터랩_서울에서 상담 문서 검색 품질을 매주 평가셋으로 점검한다면 이력서의 LangChain 경험을 바탕으로 어떻게 설계할지 설명해 주세요."
JOB_FACT = "상담 문서 검색 품질을 매주 평가셋으로 점검한다"


class DualEnv(T.Env):
    def __init__(self, job_answer=None, redis_fail=False, chroma_fail=False, embed_fail=False, batch_fail=False,
                 dual=True, mq=True, **kw):
        super().__init__(**kw)
        self.job_answer = job_answer or {"question": JOB_RAG_Q, "used_terms": ["LangChain"], "company_facts": [JOB_FACT],
                                         "doc_refs": ["PRJ-CC-PUB#s2"]}
        self.redis_fail, self.chroma_fail, self.embed_fail, self.batch_fail = redis_fail, chroma_fail, embed_fail, batch_fail
        self.dual, self.mq = dual, mq
        self.knn_calls, self.embed_calls, self.mix_calls, self.bank_calls = [], [], 0, 0

    def chat(self, system, user, schema, **kw):
        if "[재료 2 — 사내문서(Chroma)" in system:
            self.prompts.append(system + "\n" + user)
            return self.job_answer
        return super().chat(system, user, schema, **kw)

    def query_public(self, vector, k=12, where=None):
        if self.chroma_fail:
            self.searched += 1
            raise RuntimeError("chroma down")
        return super().query_public(vector, k, where)

    def embed(self, texts, *a, **k):
        self.embed_calls.append(len(texts))
        if self.embed_fail or (self.batch_fail and len(texts) > 1):
            raise llm.LLMError("embed down")
        return [[0.1 * (i + 1), 0.2, 0.3] for i in range(len(texts))]

    def knn(self, vec, k=16, role_codes=None, categories=None, translated_only=True):
        self.knn_calls.append((categories[0], tuple(vec)))
        if self.redis_fail:
            raise ConnectionError("redis down")
        cat = categories[0]
        base = [{"doc_id": f"{cat}:{i}", "question_ko": (T.COMPANY_BANK if cat == "company" else JOB_BANK.format(i=i) if cat == "job"
                                                         else f"인성 문항 {i}번을 설명해 주세요."),
                 "intent_ko": "관심", "dup_group": f"{cat}{i}", "competency": "", "sim": 0.8 - 0.01 * i} for i in range(8)]
        return base[int(vec[0] * 10) % 3:] + base[:int(vec[0] * 10) % 3]   # 쿼리마다 순서가 조금 다름

    def mix(self, q, job, snap, intent=""):
        self.mix_calls += 1
        return "MIX:" + q

    def pick_bank(self, cat, n, exclude=()):
        self.bank_calls += 1
        return [{"id": 100 + i, "text": (T.COMPANY_BANK if cat == "company" else f"DB {cat} 문항 {i}번을 설명해 주세요."),
                 "subcategory": "관심", "dup_group": f"db{cat}{i}"} for i in range(n)]

    def __enter__(self):
        super().__enter__()
        from app import vectorstore
        extra = [(llm, "embed"), (vectorstore, "knn"), (config, "RAG_MULTI_QUERY"), (config, "RAG_DUAL_JOB"), (Q, "pick_bank")]
        self._saved += [(m, n, getattr(m, n)) for m, n in extra]
        # Env가 바꾼 _pick_redis·_redis_query_vector는 진짜로 되돌려 Multi-Query + RRF 경로를 실제로 탄다
        for m, n, v in self._saved:
            if m is QG and n in ("_pick_redis", "_redis_query_vector"):
                setattr(m, n, v)
        llm.embed = self.embed
        vectorstore.knn = self.knn
        config.RAG_MULTI_QUERY, config.RAG_DUAL_JOB = self.mq, self.dual
        Q.contextualize_job = self.mix
        Q.pick_bank = self.pick_bank
        return self


def run(env, app=T.APP):
    with env:
        return QG.generate_set(app, use_llm=True)


def by_cat(qs, cat):
    return [q for q in qs if q["category"] == cat]


def main():
    res = []
    pub = [T.chunk("PRJ-CC-PUB#001#0", "PRJ-CC-PUB", "s2", T.PUB_BODY), T.chunk("TAL-01#000#0", "TAL-01", "s1", T.TAL_BODY, sim=0.7)]

    # D1 검증 노드 유지
    order = ["start", "extract_keywords", "evaluate_trust", "retrieve", "retrieve_internal", "generate",
             "evaluate_philosophy", "evaluate_answerability", "guard_disclosure", "finalize"]
    if T.FAKE_LG:
        e = QG.GENERATION.graph.edges
        chain = [e.get("__start__")]
        while chain[-1] in e and e[chain[-1]] != "__end__":
            chain.append(e[chain[-1]])
    else:
        edges = {a.source: a.target for a in QG.GENERATION.get_graph().edges}
        chain = [edges.get("__start__")]
        while chain[-1] in edges and edges[chain[-1]] != "__end__":
            chain.append(edges[chain[-1]])
    res.append(("D1 검증 노드 순서 유지(A5·A6·A7·7-B)", chain == order))

    # D2 Dual + Multi-Query
    env = DualEnv(hits=pub)
    qs, info = run(env)
    jobs, comp = by_cat(qs, "job"), by_cat(qs, "company")[0]
    jp = [p for p in env.prompts if "[재료 2 — 사내문서(Chroma)" in p]
    res.append(("D2 쿼리 3개 묶음 임베딩 1회 · mq multi", env.embed_calls[:1] == [3] and info["mq_report"].get("mode") == "multi"))
    res.append(("D2 Redis: 카테고리마다 벡터 3개로 KNN(RRF 입력)",
                sum(1 for c, _ in env.knn_calls if c == "job") == 3 and sum(1 for c, _ in env.knn_calls if c == "company") == 3))
    res.append(("D2 Chroma: 쿼리 3개로 3번 검색(search_multi)", env.searched == 3))
    res.append(("D2 직무 2·기업 1 = 사내문서 근거, 출처 ID 보존",
                len(jobs) == 2 and all(q["text"] == JOB_RAG_Q and q["gen_method"].startswith("redis:job:")
                                       and q["gen_method"].endswith("|internal:PRJ-CC-PUB#s2") for q in jobs)
                and comp["gen_method"].endswith("|internal:PRJ-CC-PUB#s2")))
    p0 = jp[0] if jp else ""
    res.append(("D2 직무 프롬프트 4재료 구분(은행 문항·조각·공고·이력서)",
                len(jp) == 2 and "질문 은행 문항: job 문항" in p0 and "매주 평가셋" in p0 and T.JOB["duties"] in p0 and "LangChain" in p0
                and "[재료 3 — 채용공고" in p0 and "[재료 4 — 지원자 이력서" in p0))
    # 2026-11-17(ADLC 6.19): dual_report에 사내문서 질문 집계가 더해졌다 → 기존 5칸 + 새 4칸을 모두 고정한다
    dr = info["dual_report"]
    res.append(("D2 dual_report = dual · job_rag 2 · company_rag 1 · 8문항",
                {k: dr.get(k) for k in ("mode", "redis", "chroma", "job_rag", "company_rag")}
                == {"mode": "dual", "redis": True, "chroma": True, "job_rag": 2, "company_rag": 1} and len(qs) == 8))
    res.append(("D2 dual_report 사내문서 질문 집계(A17 미사용 환경) = 0 · 근거 3 · 문항 8",
                dr.get("internal_q") == {"personality": 0, "job": 0, "company": 0} and dr.get("internal_q_total") == 0
                and dr.get("internal_total") == 3 and dr.get("questions") == 8))
    res.append(("D2 7-B 검사 3 · 차단 0 · 로그에 조각 본문 없음",
                info["guard_report"].get("checked") == 3 and info["guard_report"].get("blocked") == 0
                and not any("매주 평가셋" in line for line in info["log"])))

    # D3 Redis 실패 + Chroma 성공
    env = DualEnv(hits=pub, redis_fail=True)
    qs, info = run(env)
    jobs = by_cat(qs, "job")
    res.append(("D3 Redis 실패 → DB 은행 + 사내문서 맥락(chroma_only), 8문항",
                info["dual_report"].get("mode") == "chroma_only" and len(qs) == 8 and env.bank_calls >= 2
                and all(q["text"] == JOB_RAG_Q and q["gen_method"].startswith("db|internal:") for q in jobs)))

    # D4 Redis 성공 + Chroma 실패
    env = DualEnv(hits=pub, chroma_fail=True)
    qs, info = run(env)
    res.append(("D4 Chroma 실패 → Redis 기반(redis_only), 8문항, 기존 섞어 쓰기",
                info["dual_report"].get("mode") == "redis_only" and len(qs) == 8 and env.mix_calls == 2
                and not any("internal" in (q.get("gen_method") or "") for q in qs)
                and not any("[재료 2" in p or "[사내문서 근거" in p for p in env.prompts)))

    # D5 둘 다 실패
    env = DualEnv(hits=pub, embed_fail=True)
    qs, info = run(env)
    res.append(("D5 임베딩 실패 → safe, 8문항, 사내문서 프롬프트 0, 검색 0",
                info["dual_report"].get("mode") == "safe" and len(qs) == 8 and env.searched == 0 and not env.knn_calls
                and not any("[재료 2" in p or "[사내문서 근거" in p for p in env.prompts)))

    # D6 restricted 혼입(Multi-Query 경로)
    env = DualEnv(hits=pub + [T.chunk("PRJ-CC#003#0", "PRJ-CC", "s4", T.SECRET_BODY, disclosure="internal", sim=0.95)])
    qs, info = run(env)
    res.append(("D6 restricted 혼입 → 폐기, 본문이 프롬프트·질문에 없음",
                not any("300석" in p for p in env.prompts) and not any("300석" in q["text"] for q in qs)
                and not any("internal" in (q.get("gen_method") or "") for q in qs)
                and any("공개판이 아닌 조각" in line for line in info["log"])))

    # D7 public 고정(코드 검사)
    src_q = inspect.getsource(internal_store.query_public)
    mods = inspect.getsource(QG) + inspect.getsource(internal_search)
    res.append(("D7 query_public = PUBLIC 고정 · 그래프·검색 모듈에 restricted 참조 없음",
                "collection(PUBLIC)" in src_q and "RESTRICTED" not in mods and "internal_docs_restricted" not in mods))

    # D8 허위 경험 전제
    bad = {"question": "헤드헌터랩_서울에서 상담 문서 검색 품질을 매주 평가셋으로 점검한다는 업무를 진행하셨던 경험을 설명해 주세요.",
           "used_terms": [], "company_facts": [JOB_FACT], "doc_refs": ["PRJ-CC-PUB#s2"]}
    env = DualEnv(hits=pub, job_answer=bad)
    qs, info = run(env)
    res.append(("D8 사내문서 사실을 지원자 경험처럼 전제 → 거절 → 기존 섞어 쓰기",
                info["dual_report"].get("job_rag") == 0 and env.mix_calls == 2
                and all(q["text"].startswith("MIX:") for q in by_cat(qs, "job"))))

    # D9 지어낸 기술명
    bad = {"question": "헤드헌터랩_서울에서 상담 문서 검색 품질을 매주 평가셋으로 점검한다면 Kubernetes 경험으로 어떻게 할지 설명해 주세요.",
           "used_terms": [], "company_facts": [JOB_FACT], "doc_refs": ["PRJ-CC-PUB#s2"]}
    env = DualEnv(hits=pub, job_answer=bad)
    qs, info = run(env)
    res.append(("D9 이력서·공고·조각에 없는 기술명 → 거절", info["dual_report"].get("job_rag") == 0 and env.mix_calls == 2))

    # D10 7-B 직무 Dual RAG 문항
    base = {"job": dict(T.JOB), "use_llm": True, "forbid_terms": T.TERMS, "forbid_ready": True, "snapshot": T.APP["resume_snapshot"],
            "internal_hits": [{"text": T.PUB_BODY, "ref": "PRJ-CC-PUB#s2"}], "internal_refs": ["PRJ-CC-PUB#s2"],
            "internal_context": "[근거 PRJ-CC-PUB#s2 · x > y]\n" + T.PUB_BODY, "log": []}

    def jdraft(text):
        return {"category": "job", "eval_items": Q.EVAL_ITEMS["job"], "text": text, "orig": "job 문항 0번을 설명해 주세요.",
                "bank_id": None, "gen_method": "redis:job:0", "company_facts": [JOB_FACT], "doc_refs": ["PRJ-CC-PUB#s2"]}
    saved = QG.REVIEW
    QG.REVIEW = False
    try:
        ok = QG.guard_disclosure({**base, "drafts": [jdraft(JOB_RAG_Q)]})
        leak = QG.guard_disclosure({**base, "drafts": [jdraft(JOB_RAG_Q.replace("상담 문서", "대한전자 상담 문서"))]})
    finally:
        QG.REVIEW = saved
    res.append(("D10 7-B: 이력서 토큰(LangChain) 문항 통과 · 금지 용어 문항 차단 → 원문",
                ok["drafts"][0].get("guard") == "pass" and leak["drafts"][0]["text"] == "job 문항 0번을 설명해 주세요."
                and leak["guard_report"]["reasons"].get("forbid_term") == 1))

    # D11 스위치 끔
    env = DualEnv(hits=pub, dual=False)
    qs, info = run(env)
    res.append(("D11 RAG_DUAL_JOB=0 → 직무 기존 경로 · 기업만 A15",
                env.mix_calls == 2 and not any("[재료 2" in p for p in env.prompts)
                and by_cat(qs, "company")[0]["gen_method"].endswith("|internal:PRJ-CC-PUB#s2")
                and info["dual_report"].get("job_rag") == 0 and info["dual_report"].get("company_rag") == 1))

    # D12 묶음 임베딩 실패 → 단일 쿼리 fallback에도 Dual 유지
    env = DualEnv(hits=pub, batch_fail=True)
    qs, info = run(env)
    res.append(("D12 묶음 임베딩 실패 → 단일 쿼리(fallback) · Dual 유지",
                info["mq_report"].get("mode") == "fallback" and env.searched == 1 and info["dual_report"].get("mode") == "dual"
                and info["dual_report"].get("job_rag") == 2))

    for name, ok_ in res:
        print(("PASS " if ok_ else "FAIL ") + name)
    n = sum(1 for _, ok_ in res if ok_)
    print(f"{n}/{len(res)}" + ("  (langgraph 가짜로 실행)" if T.FAKE_LG else ""))
    return 0 if n == len(res) else 1


if __name__ == "__main__":
    sys.exit(main())
