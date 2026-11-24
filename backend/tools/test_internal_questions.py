"""사내문서 질문(A17 생성 · A18 검증 · 그래프 1-A 1순위 출처) 검사 — ADLC v0.8 6.19, 2026-11-17.
모델·DB·Redis·**실제 Chroma 없이** 돈다(가짜 환경은 test_internal_rag_graph.Env를 그대로 쓴다).

    cd backend && python -m tools.test_internal_questions
    docker exec -w /srv headhunterAI-api python -m tools.test_internal_questions

  I1  q_cat 매핑: public 47종 doc_type 전부 배정 · project는 job·company 양쪽 · 모르는 종류는 company ·
      저장된 q_cat 메타가 doc_type보다 우선 · matches(None)은 항상 참
  I2  A18 검증: 통과 1건 + 탈락 사유 10종이 **사유 문자열과 함께** 나온다(A15가 사유를 안 남긴 QO-13 반복 금지)
  I3  search_questions: 유사도 하한 · 역량 중복 제거 · used 제외 · 부족하면 None(→ 백업) ·
      disclosure/status 혼입이면 None(B-09) · q_cat 불일치면 None · 금지 용어·마스킹 표식 질문 제거 · dept 가산점
  I4  그래프 1-A 출처 순서: 사내문서 질문이 있으면 인성·직무·기업 3카테고리 모두 그것을 쓰고 Redis를 부르지 않는다,
      gen_method가 `internal_q:<ID>`, 다시 쓰기(A15·인성 다듬기) 모델 호출 0회, 8문항
  I5  백업 층: 사내문서 질문이 모자라면 그 카테고리만 Redis → Redis도 없으면 DB 은행
  I6  스위치 `RAG_QUESTION_SOURCE=redis` → 사내문서 질문 경로를 쓰지 않는다(2026-11-16까지의 동작)
  I7  7-B가 사내문서 질문을 차단 → finalize가 백업 문항으로 바꿔 **8문항을 유지**한다
  I8  `RAG_IQ_REWRITE=1` → 사내문서 질문도 면접마다 다시 쓴다(A15 경로를 탄다)
  I9  Redis 비율 지표(M-55·M-58) 계산이 gen_method 문자열과 맞는다
  I10 이력서 참고 문항도 사내문서 질문을 먼저 쓴다(2026-11-18, M-55 0.625 → 0.875 목표):
      `_resume_bank_refs`를 **실제로 돌려** ① 참고 문항이 internal_q로 잡히고 ② dual_report.resume_iq_ref가 세어지고
      ③ 이력서 문항 gen_method에 `|internal:`이 붙는지 · 역량이 모자라면 그 문항만 Redis 백업으로 내려가는지
  I11 A6(철학 적합성)이 되돌린 문항 처리(2026-11-18): 같은 `text == orig`가 이력서 문항에서는 "템플릿으로 되돌려졌다"
      (→ 집계에서 뺀다), A17 카테고리 문항에서는 "검증된 질문을 그대로 출제"(→ 출처 유지)라는 정반대 뜻이다
  I12 `bank_reference`가 사내문서 질문의 `model_answer`·`expected_keywords`를 읽는다(2026-11-18) — 이게 없어서
      꼬리질문 판단 ②(기대 키워드)·③(구체성)이 주 출처에서 돌지 않았다. 다른 출처(Redis·DB·이력서)는 그대로
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tools import test_internal_rag_graph as T  # noqa: E402  (가짜 모듈 설치 포함)

from app import config, internal_store, llm, questions as Q  # noqa: E402
from app.agents import internal_qcat as QC, internal_question_agent as IQ, question_graph as QG  # noqa: E402

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))


# ---------------- I1 q_cat 매핑 ----------------
# 2026-11-17 internal_docs_public 실측 47종
PUBLIC_TYPES = [
    "talent", "revops", "hr", "playbook", "incident", "project", "unit_code", "jd", "approval", "sales_standard",
    "tech_card", "attendance", "sales_playbook", "handoff_process", "contract_guide", "decision_guide",
    "proposal_guide", "open_challenge", "meeting_rule", "manpower_request", "bid_process", "triage_rule",
    "evaluation", "notice", "hiring_guide", "casebook", "bid_decision", "retrospective", "pricing_model",
    "foreign_tx", "asset_registry", "receivable", "release_rule", "planning", "costing", "budget",
    "review_report_standard", "revenue", "assessment_standard", "approval_authority", "sales_plan",
    "pre_review_guide", "poc_standard", "closing", "settlement", "discovery_guide", "feasibility_checklist"]


def t_qcat():
    missing = [t for t in PUBLIC_TYPES if t not in QC.TYPE_MAP]
    check("I1 public doc_type 47종 전부 배정", not missing, f"미배정 {missing}")
    check("I1 종류 수 47", len(PUBLIC_TYPES) == 47, str(len(PUBLIC_TYPES)))
    check("I1 project는 job·company 양쪽", set(QC.from_doc_type("project")) == {"job", "company"})
    check("I1 talent → personality", QC.from_doc_type("talent") == ["personality"])
    check("I1 jd → job", QC.from_doc_type("jd") == ["job"])
    check("I1 모르는 종류 → company", QC.from_doc_type("무슨종류") == ["company"])
    check("I1 저장된 q_cat이 doc_type보다 우선",
          QC.qcat_of({"doc_type": "jd", "q_cat": "|personality|"}) == ["personality"])
    check("I1 q_cat 값이 깨져 있으면 doc_type으로",
          QC.qcat_of({"doc_type": "jd", "q_cat": "|엉뚱한값|"}) == ["job"])
    check("I1 matches(None)은 항상 참", QC.matches({"doc_type": "jd"}, None))
    check("I1 matches 불일치", not QC.matches({"doc_type": "jd"}, "personality"))
    check("I1 pipe/unpipe 왕복", QC.unpipe(QC.pipe(["job", "company"])) == ["job", "company"])
    # 세 카테고리 모두 재료가 있는지(배정 결과가 한쪽으로 쏠리지 않는지)
    per = {c: sum(1 for t in PUBLIC_TYPES if c in QC.from_doc_type(t)) for c in QC.CATS}
    check("I1 세 카테고리 모두 doc_type 5종 이상", all(v >= 5 for v in per.values()), str(per))


# ---------------- I2 A18 검증 ----------------
CHUNK = {"chunk_id": "JD-AIAX-01#003#0", "body":
         "AI/AX 엔지니어는 고객사 요구사항을 기술 명세로 바꾸고, 검색증강생성과 전통 ML 모델을 조합해 운영 가능한 시스템을 만듭니다. "
         "모델 정확도뿐 아니라 응답 일관성, 처리 시간, 비용, 개인정보 보호, 장애 시 대체 경로까지 함께 설계합니다.",
         "meta": {"doc_id": "JD-AIAX-01", "sec": "s3", "doc_title": "AI/AX 엔지니어 직무기술서",
                  "sec_title": "직무 개요", "doc_type": "jd", "disclosure": "public", "status": "active",
                  "dept": "|ai_ax|", "dept_ai_ax": True}}
TERMS = {"고객사명치환": ["X"], "주소": ["Y"]}


def cand(q, facts=(), **kw):
    d = {"question": q, "intent": "판단 기준", "competency": "문제 해결", "signals": "기준을 세워 설명", "keywords": ["기준"],
         "facts": list(facts)}
    d.update(kw)
    return d


def t_validate():
    good = cand("응답 일관성과 처리 시간 중 하나를 포기해야 한다면 무엇을 기준으로 정하시겠습니까?",
                ["응답 일관성, 처리 시간"])
    ok, why = IQ.validate(good, CHUNK, "job", TERMS)
    check("I2 정상 질문 통과", ok, why)

    cases = [
        ("length", cand("짧다")),
        ("not_one_sentence", cand("기준을 설명해 주세요. 그리고 이유도 설명해 주세요.")),
        ("blocklist", cand("지원자의 나이와 학벌을 고려해 어떤 기준을 세우시겠습니까?")),
        ("interview_meta", cand("면접 평가 기준 루브릭을 어떻게 설계하시겠습니까?")),
        ("premise", cand("고객사 요구사항을 기술 명세로 바꾸는 일을 담당하셨던 방식을 설명해 주세요.")),
        ("mentions_internal_doc", cand("사내문서에 적힌 처리 시간 기준을 어떻게 보시는지 설명해 주세요.")),
        ("facts_not_grounded", cand("응답 일관성을 어떤 기준으로 보시는지 설명해 주세요.", ["조각에 없는 문구입니다"])),
        ("bad_tokens", cand("Kubernetes 환경에서 처리 시간을 어떻게 줄이시겠습니까?")),
        ("mask_marker", cand("처리 시간 개선에 [금액]을 쓸 수 있다면 어떻게 하시겠습니까?")),
        ("empty", cand("")),
    ]
    for want, c in cases:
        ok, why = IQ.validate(c, CHUNK, "job", TERMS)
        check(f"I2 탈락 사유 {want}", (not ok) and why.split(":")[0] == want, f"→ ok={ok} why={why!r}")
    ok, why = IQ.validate(good, CHUNK, "job", {})
    check("I2 금지 용어 사전 없음 → forbid_unavailable", (not ok) and why == "forbid_unavailable", why)
    ok, why = IQ.validate(cand("주소를 어떻게 처리 시간 기준에 쓰시겠습니까?"), CHUNK, "job", TERMS)
    check("I2 금지 용어 → forbid_term", (not ok) and why == "forbid_term", why)


# ---------------- I3 search_questions ----------------
def _hit(i, sim=0.5, cat="job", comp=None, disclosure="public", status="active", text=None, dept_ai_ax=False):
    return {"id": f"IQ-{cat[:4]}-x-{i}", "sim": sim,
            "document": text or f"사내문서 질문 {i}번을 어떻게 보시는지 설명해 주세요.",
            "metadata": {"q_cat": cat, "question": text or f"사내문서 질문 {i}번", "intent": "의도",
                         "competency": comp if comp is not None else f"역량{i}", "doc_id": f"DOC-{i}",
                         "disclosure": disclosure, "status": status, "chunk_id": f"C{i}",
                         "doc_refs": f"|DOC-{i}#s1|", "company_facts": "", "dept_ai_ax": dept_ai_ax,
                         "model_answer": "요지", "expected_keywords": "가, 나"}}


class Store:
    """internal_store.query_questions 가짜."""

    def __init__(self, hits, fail=False):
        self.hits, self.fail, self.calls = hits, fail, 0

    def __call__(self, vector, q_cat, k=12, where=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("chroma down")
        return [h for h in self.hits if h["metadata"]["q_cat"] == q_cat] or list(self.hits)


def with_store(hits, fail=False):
    st = Store(hits, fail)
    old = internal_store.query_questions
    internal_store.query_questions = st
    return st, (lambda: setattr(internal_store, "query_questions", old))


def t_search():
    hits = [_hit(i, sim=0.6) for i in range(6)]
    st, undo = with_store(hits)
    try:
        got = IQ.search_questions([0.1, 0.2], "job", 2, (), role_code="ai_ax", forbid_terms=TERMS)
        check("I3 후보 n개", got is not None and len(got) == 2, str(got and len(got)))
        check("I3 반환 모양이 _pick_redis와 같다",
              bool(got) and all({"text", "doc_id", "dup_group", "sim", "iq_id"} <= set(b) for b in got))
        check("I3 doc_refs가 목록으로 풀린다", bool(got) and got[0]["doc_refs"] == [f"DOC-{got[0]['iq_id'][-1]}#s1"]
              or bool(got) and len(got[0]["doc_refs"]) == 1)

        got = IQ.search_questions([0.1], "job", 2, (), min_sim=0.9, forbid_terms=TERMS)
        check("I3 유사도 하한 미달 → None(백업으로)", got is None)

        same = [_hit(i, sim=0.6, comp="협업") for i in range(6)]
        internal_store.query_questions = Store(same)
        got = IQ.search_questions([0.1], "job", 2, (), forbid_terms=TERMS)
        check("I3 같은 역량은 1개로 묶여 부족 → None", got is None)

        internal_store.query_questions = Store([_hit(i, sim=0.6) for i in range(6)])
        got = IQ.search_questions([0.1], "job", 2, used={"역량0", "역량1", "역량2", "역량3", "역량4"}, forbid_terms=TERMS)
        check("I3 used 묶음 제외", got is None)

        internal_store.query_questions = Store([_hit(0, disclosure="internal")] + [_hit(i) for i in range(1, 6)])
        check("I3 disclosure 혼입 → None(B-09)",
              IQ.search_questions([0.1], "job", 2, (), forbid_terms=TERMS) is None)

        internal_store.query_questions = Store([_hit(0, status="open")] + [_hit(i) for i in range(1, 6)])
        check("I3 status open 혼입 → None",
              IQ.search_questions([0.1], "job", 2, (), forbid_terms=TERMS) is None)

        internal_store.query_questions = lambda v, q_cat, k=12, where=None: [_hit(i, cat="company") for i in range(6)]
        check("I3 q_cat 불일치 → None",
              IQ.search_questions([0.1], "job", 2, (), forbid_terms=TERMS) is None)

        bad = [_hit(i, text=f"주소를 어떻게 쓰시는지 설명해 주세요 {i}.") for i in range(6)]
        internal_store.query_questions = Store(bad)
        check("I3 금지 용어가 든 질문 제거 → None",
              IQ.search_questions([0.1], "job", 2, (), forbid_terms=TERMS) is None)

        mask = [_hit(i, text=f"[금액]을 어떻게 보시는지 설명해 주세요 {i}.") for i in range(6)]
        internal_store.query_questions = Store(mask)
        check("I3 마스킹 표식이 든 질문 제거 → None",
              IQ.search_questions([0.1], "job", 2, (), forbid_terms=TERMS) is None)

        internal_store.query_questions = Store([], fail=True)
        check("I3 Chroma 오류 → None(그래프는 멈추지 않는다)",
              IQ.search_questions([0.1], "job", 2, (), forbid_terms=TERMS) is None)

        mixed = [_hit(0, sim=0.40, dept_ai_ax=True), _hit(1, sim=0.42), _hit(2, sim=0.41)]
        internal_store.query_questions = Store(mixed)
        got = IQ.search_questions([0.1], "job", 3, (), role_code="ai_ax", forbid_terms=TERMS)
        best = max(got, key=lambda b: b["sim"])
        check("I3 dept 일치 가산점(+0.05)", best["iq_id"].endswith("-0"), str([(b["iq_id"], b["sim"]) for b in got]))
    finally:
        undo()


# ---------------- 그래프 1-A (I4~I8) ----------------
class IQEnv(T.Env):
    """Env에 사내문서 질문 출처를 더한다. iq_per_cat = {카테고리: 돌려줄 문항 수}."""

    def __init__(self, iq_per_cat=None, source="chroma", rewrite=False, guard_answer=None, **kw):
        super().__init__(guard_answer=guard_answer, **kw)
        self.iq_per_cat = {"personality": 2, "job": 3, "company": 1} if iq_per_cat is None else iq_per_cat
        self.source, self.rewrite = source, rewrite
        self.iq_calls, self.redis_calls = [], []

    def chat(self, system, user, schema, **kw):
        """검수 노드(A5·A6·A7)는 '통과'로 답하고, 나머지는 Env의 판단(A15·7-B)에 넘긴다."""
        props = schema.get("properties") or {}
        if "quality" in props:                      # A5 키워드 신뢰도 — 받은 키워드를 그대로 돌려준다(근거 검사 통과)
            self.prompts.append(system)
            line = [x for x in user.splitlines() if x.startswith("키워드: ")]
            kws = [k.strip() for k in (line[0][4:].split(",") if line else []) if k.strip()]
            return {"quality": 5, "quantity_ok": True, "removed": [], "added": [], "keywords": kws}
        if "scores" in props:                       # A6 철학 적합성 — 통과
            self.prompts.append(system)
            return {"scores": [5] * 6, "pass": True, "problems": [], "revised_question": ""}
        if "answerable" in props:                   # A7 답변 가능성 — 통과
            self.prompts.append(system)
            return {"answerable": True, "asks": [], "revised_question": ""}
        return super().chat(system, user, schema, **kw)

    def pick_internal(self, vecs, role, cat, n, used, terms, min_sim=None):
        self.iq_calls.append(cat)
        have = self.iq_per_cat.get(cat, 0)
        if terms is None or have < n:
            return None
        return [{"id": None, "doc_id": f"DOC-{cat}-{i}", "iq_id": f"IQ-{cat[:4]}-abc-{i}",
                 "text": f"{cat} 사내문서 질문 {i}번을 어떻게 보시는지 설명해 주세요.", "intent": "의도",
                 "competency": f"{cat}역량{i}", "subcategory": "의도", "dup_group": f"iq-{cat}-{i}", "sim": 0.5,
                 "chunk_id": f"C-{cat}-{i}", "doc_refs": [f"DOC-{cat}-{i}#s1"], "company_facts": [],
                 "model_answer": "요지", "expected_keywords": "가"} for i in range(n)]

    def pick_redis(self, v, role, cat, n, used):
        self.redis_calls.append(cat)
        return [{"id": None, "doc_id": f"{cat}:{i}", "text": f"{cat} 은행 문항 {i}번을 설명해 주세요.",
                 "subcategory": "관심", "dup_group": f"{cat}{i}"} for i in range(n)]

    def __enter__(self):
        out = super().__enter__()
        self._saved2 = [(config, "RAG_QUESTION_SOURCE", config.RAG_QUESTION_SOURCE),
                        (config, "RAG_IQ_REWRITE", config.RAG_IQ_REWRITE),
                        (Q, "pick_bank", Q.pick_bank)]
        config.RAG_QUESTION_SOURCE = self.source
        config.RAG_IQ_REWRITE = self.rewrite
        Q.pick_bank = lambda category, n, exclude_groups=(): [
            {"id": 100 + i, "text": f"{category} DB 은행 문항 {i}번을 설명해 주세요.", "subcategory": "기본",
             "dup_group": f"db-{category}-{i}"} for i in range(n)]
        QG._pick_internal = self.pick_internal
        QG._pick_redis = self.pick_redis
        return out

    def __exit__(self, *a):
        for m, n, v in self._saved2:
            setattr(m, n, v)
        return super().__exit__(*a)


def _run(env):
    with env:
        qs, info = QG.generate_set(dict(T.APP), use_llm=True)
    return qs, info


def t_graph_order():
    env = IQEnv()
    qs, info = _run(env)
    check("I4 8문항", len(qs) == 8, str(len(qs)))
    iq = [q for q in qs if (q.get("gen_method") or "").startswith("internal_q:")]
    check("I4 인성2·직무3·기업1 = 6문항이 사내문서 질문", len(iq) == 6, str([q.get("gen_method") for q in qs]))
    check("I4 gen_method에 질문 ID", all("internal_q:IQ-" in q["gen_method"] for q in iq))
    check("I4 Redis를 부르지 않는다", env.redis_calls == [], str(env.redis_calls))
    check("I4 세 카테고리 모두 사내문서 질문 시도", set(env.iq_calls) == {"personality", "job", "company"}, str(env.iq_calls))
    rr = info.get("dual_report") or {}
    check("I4 dual_report.internal_q_total = 6", rr.get("internal_q_total") == 6, str(rr))
    check("I4 사내문서 질문은 다시 쓰지 않는다(A15·인성 다듬기 호출 0)", env.old_calls == 0, str(env.old_calls))
    texts = [q["text"] for q in iq]
    check("I4 질문 문장이 그대로 나간다", all("사내문서 질문" in t for t in texts), str(texts[:2]))


def t_backup():
    env = IQEnv(iq_per_cat={"personality": 2, "job": 0, "company": 1})
    qs, info = _run(env)
    check("I5 사내문서 질문이 모자란 직무만 Redis로", env.redis_calls == ["job"], str(env.redis_calls))
    check("I5 8문항 유지", len(qs) == 8, str(len(qs)))
    job = [q for q in qs if q["category"] == "job"]
    check("I5 직무 문항은 은행 출처", all((q.get("gen_method") or "").startswith("redis:") or q.get("bank_id")
                                      for q in job), str([q.get("gen_method") for q in job]))

    env = IQEnv(iq_per_cat={})
    env.pick_redis = lambda v, role, cat, n, used: None
    qs, _ = _run(env)
    check("I5 Redis도 없으면 DB 은행·인성 md", len(qs) == 8 and any("DB 은행" in q["text"] or "인성 질문" in q["text"]
                                                                for q in qs), str([q["text"][:18] for q in qs]))


def t_switch():
    env = IQEnv(source="redis")
    qs, _ = _run(env)
    check("I6 스위치 redis → 사내문서 질문 미사용", all(not (q.get("gen_method") or "").startswith("internal_q:")
                                                for q in qs))
    check("I6 스위치 redis → Redis 3카테고리 호출", set(env.redis_calls) == {"personality", "job", "company"},
          str(env.redis_calls))


def t_guard_blocked():
    # 7-B 모델 검수를 켜고 누출 판정을 내리게 한다 → 사내문서 질문이 차단되면 백업 문항으로 교체
    env = IQEnv(review=True, guard_answer={"leak": True, "kinds": ["company"]})
    qs, info = _run(env)
    check("I7 차단 뒤에도 8문항", len(qs) == 8, str(len(qs)))
    check("I7 차단된 사내문서 질문은 남지 않는다",
          not any((q.get("gen_method") or "").startswith("internal_q:") and "internal:" in (q.get("gen_method") or "")
                  for q in qs))
    gr = info.get("guard_report") or {}
    check("I7 guard_report가 차단을 기록", gr.get("blocked", 0) >= 1, str(gr))
    check("I7 백업 문항으로 채워짐", any("은행 문항" in q["text"] or "DB 은행" in q["text"] or "인성 질문" in q["text"]
                                   for q in qs), str([q["text"][:20] for q in qs]))


def t_rewrite():
    env = IQEnv(rewrite=True)
    qs, _ = _run(env)
    check("I8 RAG_IQ_REWRITE=1 → 다시 쓰기 경로를 탄다", env.old_calls >= 1, str(env.old_calls))


# ---------------- I9 지표 ----------------
def t_metric():
    from tools import eval_source_mix as M
    people = [{"questions": [{"category": "personality", "gen_method": "internal_q:IQ-pers-1"},
                             {"category": "personality", "gen_method": "internal_q:IQ-pers-2"},
                             {"category": "resume", "gen_method": "llm"},
                             {"category": "resume", "gen_method": "llm"},
                             {"category": "resume", "gen_method": "template"},
                             {"category": "job", "gen_method": "internal_q:IQ-job-1"},
                             {"category": "job", "gen_method": "redis:ai_ax:ai-009"},
                             {"category": "company", "gen_method": "redis:common:cm-c003|internal:TAL-02#s5"}]}]
    r = M.summarize(people)
    check("I9 문항 8개", r["questions"] == 8, str(r))
    check("I9 사내문서 출처 4개(질문 3 + 근거 1) = 50%", r["internal"] == 4 and abs(r["internal_ratio"] - 0.5) < 1e-9, str(r))
    check("I9 Redis 원문 2개 = 25%", r["redis"] == 2 and abs(r["redis_ratio"] - 0.25) < 1e-9, str(r))
    check("I9 M-55 미달·M-58 미달 판정", r["m55_pass"] is False and r["m58_pass"] is False, str(r))


# ---------------- I10 이력서 참고 문항도 사내문서 질문을 먼저 (2026-11-18, M-55) ----------------
# I4~I8 환경(T.Env)은 `_resume_bank_refs`를 통째로 가짜로 바꿔 버려서 이 경로가 한 줄도 지나가지 않았다.
# 여기서는 **진짜 함수**를 돌린다 → 호스트에 없는 모듈만 가짜로 채운다(컨테이너에서는 진짜를 쓴다).
_REAL_RESUME_BANK_REFS = QG._resume_bank_refs        # 어떤 Env도 켜지지 않은 지금이 진짜다

try:
    from app import vectorstore as _VS               # noqa: E402
except Exception:                                    # noqa: BLE001 — 호스트에 redis 패키지가 없다
    _VS = types.ModuleType("app.vectorstore")
    _VS.knn = lambda *a, **k: []
    sys.modules["app.vectorstore"] = _VS
try:
    from app.agents import question_chain as _QCH    # noqa: E402
except Exception:                                    # noqa: BLE001 — 호스트에 langchain_core가 없다
    _QCH = types.ModuleType("app.agents.question_chain")
    _QCH.QUERY_INSTRUCT = "검색 질의: "
    sys.modules["app.agents.question_chain"] = _QCH


class ResumeIQEnv(IQEnv):
    """`_resume_bank_refs`를 진짜로 돌리는 환경.

    ref_pool = 이력서 참고 문항으로 내줄 역량 목록. `used`를 **실제로 지켜서** 한 번 쓴 역량은
    다시 내주지 않는다 → 목록이 짧으면 뒤쪽 이력서 문항이 Redis 백업으로 내려가는 걸 재현한다.
    iq_on=False면 사내문서 질문이 아예 없는 환경(참고 문항 전부 Redis).
    """

    def __init__(self, ref_pool=None, knn_sim=0.70, iq_on=True, **kw):
        super().__init__(**kw)
        self.ref_pool = ["ref0", "ref1", "ref2"] if ref_pool is None else list(ref_pool)
        self.knn_sim, self.iq_on = knn_sim, iq_on
        self.ref_calls = self.knn_calls = self.embed_calls = 0

    def pick_internal(self, vecs, role, cat, n, used, terms, min_sim=None, k=None):
        # 이력서 참고 문항 호출만 가로챈다 — `_resume_bank_refs`는 cat="job" · **n=1 + k(검색 깊이)**로 부른다.
        # 2026-11-23: ⑧을 처음 n=5로 넓혔더니 "하한 통과 후보가 5개 있어야 한다"가 돼 참고가 한 건도 붙지
        # 않았다(4차 재측정). 깊이는 `k`로만 넓히고 요구 개수는 1이다. 그래서 여기서는 **k가 있는 호출**로 가른다.
        # 카테고리 문항(직무 n=2 등)은 IQEnv의 기존 가짜 그대로.
        if not (cat == "job" and n == 1 and k):
            return super().pick_internal(vecs, role, cat, n, used, terms, min_sim)
        self.ref_calls += 1
        if terms is None or not self.iq_on:
            return None
        left = [g for g in self.ref_pool if g not in set(used)]
        if not left:
            return None                              # 역량이 다 쓰였다 → 호출 쪽이 Redis 백업으로
        g = left[0]
        return [{"id": None, "doc_id": f"DOC-{g}", "iq_id": f"IQ-{g}",
                 "text": f"{g} 사내문서 참고 문항을 설명해 주세요.", "intent": "의도", "competency": g,
                 "subcategory": "의도", "dup_group": g, "sim": 0.5, "chunk_id": f"C-{g}",
                 "doc_refs": [f"DOC-{g}#s1"], "company_facts": [], "model_answer": "요지",
                 "expected_keywords": "가"}]

    def embed(self, texts, **kw):
        self.embed_calls += 1
        return [[0.1, 0.2, 0.3] for _ in texts]

    def knn(self, vec, k=8, **kw):
        self.knn_calls += 1
        # dup_group을 호출마다 다르게 → `seen` 중복 제거에 걸리지 않게(백업 경로 자체를 보려는 검사)
        return [{"doc_id": f"ai_ax:ai-90{self.knn_calls}", "question_ko": "Redis 백업 참고 문항을 설명해 주세요.",
                 "dup_group": f"redis-ref-{self.knn_calls}", "intent_ko": "관심", "sim": self.knn_sim}]

    def __enter__(self):
        out = super().__enter__()
        self._saved3 = [(QG, "_resume_bank_refs", QG._resume_bank_refs), (llm, "embed", llm.embed),
                        (_VS, "knn", getattr(_VS, "knn", None))]
        QG._resume_bank_refs = _REAL_RESUME_BANK_REFS
        llm.embed = self.embed
        _VS.knn = self.knn
        return out

    def __exit__(self, *a):
        for m, n, v in self._saved3:
            setattr(m, n, v)
        return super().__exit__(*a)


def _resume_qs(qs):
    return [q for q in qs if q["category"] == "resume"]


def _retrieve_log(info):
    """`generate_set`은 retrieve_report를 돌려주지 않는다(2026-11-18 확인) → 관측 가능한 건 로그 줄뿐.
    `retrieve: … resume_bank 3(internal_q) …` 형태."""
    return next((ln for ln in (info.get("log") or []) if ln.startswith("retrieve:")), "")


def t_resume_internal():
    """ⓐ 사내문서 참고 문항이 넉넉하면 이력서 3문항 전부 사내문서 출처."""
    env = ResumeIQEnv()
    qs, info = _run(env)
    rq = _resume_qs(qs)
    check("I10 8문항 · 이력서 2문항", len(qs) == 8 and len(rq) == 2, str([q["category"] for q in qs]))
    check("I10 이력서 항목마다 참고 문항을 찾는다(2회)", env.ref_calls == 2, str(env.ref_calls))
    check("I10 참고 문항은 사내문서 질문 — Redis 백업 미호출", env.knn_calls == 0, str(env.knn_calls))
    check("I10 로그 resume_bank 출처 = internal_q", "resume_bank 2(internal_q)" in _retrieve_log(info),
          _retrieve_log(info))
    dr = info.get("dual_report") or {}
    check("I10 dual_report.resume_iq_ref = 2", dr.get("resume_iq_ref") == 2, str(dr))
    check("I10 internal_total = 8(카테고리 질문 6 + 이력서 2)", dr.get("internal_total") == 8, str(dr))
    check("I10 이력서 문항 gen_method에 |internal: 조각 ID",
          all((q.get("gen_method") or "").startswith("llm|internal:DOC-ref") for q in rq),
          str([q.get("gen_method") for q in rq]))
    # M-55 집계와 실제로 맞물리는지(eval_source_mix가 세는 그 문자열인지)를 같이 본다
    from tools import eval_source_mix as M
    r = M.summarize([{"pid": "T", "questions": [{"category": q["category"], "gen_method": q.get("gen_method")}
                                                for q in qs]}])
    check("I10 M-55 = 1.000(8/8) · M-58 = 0", r["internal"] == 8 and r["redis"] == 0, str(r))


def t_resume_internal_short():
    """ⓑ 역량이 1개뿐 → 첫 문항만 사내문서, 나머지는 Redis 백업(문항 수는 유지)."""
    env = ResumeIQEnv(ref_pool=["ref0"])
    qs, info = _run(env)
    rq = _resume_qs(qs)
    # 2026-11-20: 이력서 문항 수는 PLAN에서 읽는다(2026-11-19 PLAN 변경 이력서 3 → 2 뒤 이 상수가 낡아 있었다)
    check("I10 역량 부족에도 8문항", len(qs) == 8 and len(rq) == Q.RESUME_N, str(len(qs)))
    check("I10 모자란 문항만 Redis 백업 호출", env.knn_calls == Q.RESUME_N - 1, str(env.knn_calls))
    dr = info.get("dual_report") or {}
    check("I10 dual_report.resume_iq_ref = 1", dr.get("resume_iq_ref") == 1, str(dr))
    internal = [q for q in rq if "|internal:" in (q.get("gen_method") or "")]
    check("I10 이력서 문항 중 1개만 사내문서 출처", len(internal) == 1,
          str([q.get("gen_method") for q in rq]))


def t_resume_internal_off():
    """ⓒ 사내문서 질문이 없으면 참고 문항은 전부 Redis(2026-11-16까지의 동작) — 되돌릴 길이 살아 있는지."""
    env = ResumeIQEnv(iq_on=False)
    qs, info = _run(env)
    rq = _resume_qs(qs)
    check("I10 사내문서 질문 없음 → Redis 이력서 문항 수만큼", env.knn_calls == Q.RESUME_N, str(env.knn_calls))
    check("I10 로그 resume_bank 출처 = redis", f"resume_bank {Q.RESUME_N}(redis)" in _retrieve_log(info), _retrieve_log(info))
    check("I10 dual_report.resume_iq_ref = 0", (info.get("dual_report") or {}).get("resume_iq_ref") == 0)
    check("I10 이력서 문항에 |internal: 없음", not any("internal:" in (q.get("gen_method") or "") for q in rq),
          str([q.get("gen_method") for q in rq]))

    # 유사도 하한 미달 → 참고 문항 없이(이력서만으로) 생성, 그래도 8문항
    env = ResumeIQEnv(iq_on=False, knn_sim=QG.RESUME_BANK_MIN_SIM - 0.05)
    qs, info = _run(env)
    check("I10 Redis도 하한 미달 → 참고 문항 없이 8문항", len(qs) == 8, str(len(qs)))
    check("I10 참고 문항 없음 → 로그 resume_bank 출처 = '-'",
          f"resume_bank {Q.RESUME_N}(-)" in _retrieve_log(info), _retrieve_log(info))


# ---------------- I11 A6 되돌림: 이력서는 집계에서 빼고 A17은 유지 (2026-11-18) ----------------
class PhilFailEnv(ResumeIQEnv):
    """A6(철학 적합성)이 **전부 거절**하는 환경 → 모든 문항이 `orig`로 되돌아간다.

    이때 같은 `text == orig`가 두 가지 **정반대** 뜻을 갖는다:
      · 이력서 문항  → "A6이 순수 템플릿으로 되돌렸다"(사내문서 내용 0) → 집계에서 **빼야** 한다
      · A17 카테고리 → "검증된 질문을 그대로 출제했다"(정상)            → 출처를 **유지해야** 한다
    2026-11-18 수정(`question_graph.py:679`)이 이 둘을 가르는지 검사한다.
    """

    def __init__(self, **kw):
        kw.setdefault("review", True)        # A6를 켜야 되돌림 경로를 탄다(QGRAPH_REVIEW)
        super().__init__(**kw)

    def chat(self, system, user, schema, **kw):
        props = schema.get("properties") or {}
        if "scores" in props:                # A6 — 거절 + 고쳐 쓴 문장도 주지 않음 → orig로 되돌림
            self.prompts.append(system)
            return {"scores": [1] * 6, "pass": False, "problems": ["시험용 거절"], "revised_question": ""}
        return super().chat(system, user, schema, **kw)


def t_resume_revert_not_counted():
    from tools import eval_source_mix as M
    env = PhilFailEnv()
    qs, info = _run(env)
    rq = _resume_qs(qs)
    cat = [q for q in qs if q["category"] in ("job", "company")]
    gm_r = [q.get("gen_method") or "" for q in rq]
    gm_c = [q.get("gen_method") or "" for q in cat]
    check("I11 A6 전부 거절해도 8문항", len(qs) == 8, str(len(qs)))

    # ⓐ 잡아야 하는 경우 — 되돌려진 이력서 문항
    check("I11 되돌린 이력서 문항은 생성 문장이 사라짐",
          not any("이력서 질문입니다" in q["text"] for q in rq), str([q["text"][:22] for q in rq]))
    check("I11 되돌린 이력서 문항 gen_method = template", all(g == "template" for g in gm_r), str(gm_r))
    check("I11 되돌린 이력서 문항에 |internal: 없음 ← 이번 수정의 핵심",
          not any("|internal:" in g for g in gm_r), str(gm_r))

    # ⓑ 건드리면 안 되는 경우 — A17 카테고리 문항(text == orig가 정상)
    n_cat = dict(Q.PLAN)["job"] + dict(Q.PLAN)["company"]          # 2026-11-20: PLAN에서 읽는다(직무 2 → 3 변경 반영)
    n_a17 = n_cat + dict(Q.PLAN)["personality"]
    check("I11 A17 직무·기업 문항 출처 유지(|internal:)",
          len(cat) == n_cat and all("|internal:" in g for g in gm_c), str(gm_c))
    check("I11 A17 문항은 질문 문장 그대로", all("사내문서 질문" in q["text"] for q in cat),
          str([q["text"][:22] for q in cat]))

    # 7-B는 되돌려진 이력서 문항을 **검사 대상에서 뺀다**(차단이 아니라 제외).
    # 검사 대상 5 = A17 인성 2 + 직무 2 + 기업 1(인성도 `_internal_draft`가 doc_refs를 단다).
    # 이력서 3문항은 지름길에서 걸러져 `checked`에 들어가지 않는다 — 8 − 3 = 5.
    gr = info.get("guard_report") or {}
    check("I11 7-B 검사 = A17 문항 수(이력서 제외) · 차단 0",
          gr.get("checked") == n_a17 and gr.get("blocked") == 0, str(gr))

    r = M.summarize([{"pid": "T", "questions": [{"category": q["category"], "gen_method": q.get("gen_method")}
                                                for q in qs]}])
    check("I11 M-55 = A17 문항만(인성+직무+기업) · 이력서 0", r["internal"] == n_a17, str(r))

    # 같은 환경에서 A6만 통과시키면 이력서 3문항이 다시 집계된다(수정이 과도하지 않다는 확인)
    env2 = ResumeIQEnv(review=True)
    qs2, _ = _run(env2)
    r2 = M.summarize([{"pid": "T", "questions": [{"category": q["category"], "gen_method": q.get("gen_method")}
                                                 for q in qs2]}])
    check("I11 A6 통과 환경에서는 8/8 그대로", r2["internal"] == 8, str(r2))


# ---------------- I12 bank_reference가 사내문서 질문의 모범답안·키워드를 읽는다 (2026-11-18) ----------------
class _FakeColl:
    """Chroma 질문 컬렉션 대역. `get(ids=…)`가 메타 1건 또는 빈 목록을 돌려준다."""

    def __init__(self, meta=None):
        self.meta, self.asked = meta, None

    def get(self, ids=None, include=None):
        self.asked = (ids, include)
        return {"metadatas": [dict(self.meta)] if self.meta else []}


def t_bank_reference():
    """꼬리질문 판단 ②③의 재료를 사내문서 질문에서 꺼내는지. Chroma·모델 없이 돈다."""
    from app import internal_store as S
    META = {"model_answer": "핵심 지표를 먼저 말하고 근거를 덧붙이는 답변이 좋습니다.",
            "expected_keywords": "지표 정의, 재현율|리콜, 검증 절차"}
    saved, calls = S.question_collection, []
    # 2026-11-18 뒤 이 분기는 `FOLLOWUP_INTERNAL_REF`(기본 0) 스위치 안으로 들어갔다(B-07 힌트 위험 측정 전까지 꺼 둠).
    # 이 시험은 분기 자체를 보므로 켜고 돈다(2026-11-20: 스위치가 생긴 뒤 이 시험이 TypeError로 멈춰 있었다).
    saved_flag = config.FOLLOWUP_INTERNAL_REF
    config.FOLLOWUP_INTERNAL_REF = True

    def use(coll):
        def fn(cat):
            calls.append(cat)
            return coll
        S.question_collection = fn

    try:
        coll = _FakeColl(META)
        use(coll)
        r = QG.bank_reference({"gen_method": "internal_q:IQ-job-abc-0|internal:DOC-1#s1", "category": "job"})
        check("I12 사내문서 질문 → 모범답안을 읽는다", r["ideal"] == META["model_answer"], str(r))
        check("I12 기대 키워드 3묶음(| = 동의어 분리)",
              r["keywords"] == [["지표 정의"], ["재현율", "리콜"], ["검증 절차"]], str(r["keywords"]))
        check("I12 카테고리에 맞는 컬렉션으로 조회", calls == ["job"], str(calls))
        check("I12 `|internal:` 접미를 떼고 질문 ID만 조회", coll.asked[0] == ["IQ-job-abc-0"], str(coll.asked))

        calls.clear()
        use(_FakeColl(None))                      # 그 ID가 없음
        r = QG.bank_reference({"gen_method": "internal_q:IQ-job-없음-9", "category": "job"})
        check("I12 ID가 없으면 빈 값(그 판단만 건너뜀)", r == {"keywords": [], "ideal": ""}, str(r))

        def boom(cat):
            raise RuntimeError("chroma down")
        S.question_collection = boom
        r = QG.bank_reference({"gen_method": "internal_q:IQ-job-abc-0", "category": "job"})
        check("I12 Chroma 오류 → 빈 값(예외 전파 없음, 면접 계속)", r == {"keywords": [], "ideal": ""}, str(r))

        # 다른 출처는 건드리지 않았다
        r = QG.bank_reference({"gen_method": "db", "category": "job"})
        check("I12 DB 은행 출처는 그대로 빈 값", r == {"keywords": [], "ideal": ""}, str(r))
        r = QG.bank_reference({"gen_method": "template", "category": "resume",
                               "resume_ref": {"quote": "LangChain으로 RAG 파이프라인 구현"}})
        check("I12 이력서 질문은 근거 항목 토큰 유지", [g[0] for g in r["keywords"]] == ["LangChain", "RAG"],
              str(r["keywords"]))
    finally:
        S.question_collection = saved
        config.FOLLOWUP_INTERNAL_REF = saved_flag


def main():
    t_qcat()
    t_validate()
    t_search()
    t_graph_order()
    t_backup()
    t_switch()
    t_guard_blocked()
    t_rewrite()
    t_metric()
    t_resume_internal()
    t_resume_internal_short()
    t_resume_internal_off()
    t_resume_revert_not_counted()
    t_bank_reference()
    print(f"\n{len(OK)}/{len(OK) + len(FAIL)}" + (f"  실패: {FAIL}" if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
