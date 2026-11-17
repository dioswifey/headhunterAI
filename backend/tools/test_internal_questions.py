"""사내문서 질문(A17 생성 · A18 검증 · 그래프 1-A 1순위 출처) 검사 — ADLC v0.8 6.19, 2026-11-17.
모델·DB·Redis·**실제 Chroma 없이** 돈다(가짜 환경은 test_internal_rag_graph.Env를 그대로 쓴다).

    cd backend && python -m tools.test_internal_questions
    docker exec -w /srv AI-Interview-project-api python -m tools.test_internal_questions

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
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tools import test_internal_rag_graph as T  # noqa: E402  (가짜 모듈 설치 포함)

from app import config, internal_store, questions as Q  # noqa: E402
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
        self.iq_per_cat = {"personality": 2, "job": 2, "company": 1} if iq_per_cat is None else iq_per_cat
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

    def pick_internal(self, vecs, role, cat, n, used, terms):
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
    check("I4 인성2·직무2·기업1 = 5문항이 사내문서 질문", len(iq) == 5, str([q.get("gen_method") for q in qs]))
    check("I4 gen_method에 질문 ID", all("internal_q:IQ-" in q["gen_method"] for q in iq))
    check("I4 Redis를 부르지 않는다", env.redis_calls == [], str(env.redis_calls))
    check("I4 세 카테고리 모두 사내문서 질문 시도", set(env.iq_calls) == {"personality", "job", "company"}, str(env.iq_calls))
    rr = info.get("dual_report") or {}
    check("I4 dual_report.internal_q_total = 5", rr.get("internal_q_total") == 5, str(rr))
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
    print(f"\n{len(OK)}/{len(OK) + len(FAIL)}" + (f"  실패: {FAIL}" if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
