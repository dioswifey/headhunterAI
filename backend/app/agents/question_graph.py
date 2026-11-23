"""그래프 1 — 질문 생성 + 면접 루프 (ADLC v0.5 6.1 · 6.1-B, LangGraph).

출처 순서(2026-11-17 사용자 지시, ADLC v0.8 6.19): 인성·직무·기업 문항은 ① **사내문서 질문 컬렉션**(A17이 미리 만들어 둔
질문, Chroma `internal_questions_*`) → ② Redis 질문 은행(백업) → ③ DB `question_bank`·인성 md 순으로 찾는다.
스위치 `RAG_QUESTION_SOURCE=redis`면 ①을 건너뛰어 2026-11-16까지의 동작이 된다.

A. 질문 생성 그래프 GENERATION (환경 점검 중 questions.pregenerate가 부름, 결과 → applications.pregen_questions)
   start → extract_keywords → evaluate_trust(A5, ⟲1) → retrieve(RAG ① 사내문서 질문 → 질문 은행) → retrieve_internal(4-B, RAG ② 사내문서 조각)
         → generate(A2 · 기업 문항은 근거가 있으면 A15) → evaluate_philosophy(A6, ⟲2) → evaluate_answerability(A7)
         → guard_disclosure(7-B, A16 누출 관문) → finalize → END
   (4-B·7-B·A15·A16 = ADLC v0.7 6.13.6~6.14, 2026-11-13 구현. 끄기 = INTERNAL_DOCS_RAG=0)
B. 면접 루프 INTERVIEW_LOOP (2026-11-13, ADLC 6.1-B 노드 9~13 — 답변이 들어올 때마다 answer_step이 재개)
   START → ask_question(interrupt 대기) → record_answer → judge_followup(A3) ─(꼬리질문)→ make_followup → ask_question
                                                                       └(없음)→ next_question ─(남음)→ ask_question
                                                                                              └(끝)→ END
   체크포인터 = 프로세스 메모리(InMemorySaver, thread_id = attempt-<id>), 임시 보관만. 기준은 DB(attempt_questions·attempts)이고
   체크포인트가 없거나 어긋나면 DB로 다시 만든다. 루프가 실패하면 체크포인터 없는 한 걸음 그래프 INTERVIEW_STEP으로 대체.

규칙(ADLC 0~2장): 모델 호출은 llm.chat_json만. 점수·합불·응시 상태 전이는 코드. 어떤 노드가 실패해도 규칙·템플릿 경로로 끝까지 간다.
꼬리질문 판단은 **전용 에이전트 `agents/followup_agent.py`(A3)** 가 맡는다(2026-11-17 분리). 노드 계약은 그대로
`judge(att, q, answer) → (질문, 사유) | None`이고, 프롬프트는 `prompts/evaluate/followup_judge.md`(+ `_ideal.md`) 파일이다.
이 모듈은 노드(`judge_followup` · `make_followup`)와 은행 참고 조회(`bank_reference` · `keyword_*`)만 들고 있고,
판단 상수·프롬프트·판단 함수는 followup_agent에서 다시 내보낸다(기존 이름 그대로 — 평가 도구·시험 호환).
"""
import logging
import random
import re
import threading
import time
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .. import config, db, llm, prompts, questions as Q
from . import question_rules as QR

log = logging.getLogger(__name__)


def _is_a17(d):
    """사내문서 질문(A17)을 **그대로 출제하는** 문항인가 — gen_method가 `internal_q:`로 시작한다.
    이력서 문항은 A17을 참고만 하므로(`iq_source`는 붙어도 gen_method는 llm·template) 여기에 해당하지 않는다."""
    return str(d.get("gen_method") or "").startswith("internal_q:")

REVIEW = config.QGRAPH_REVIEW          # 0이면 A5·A6 모델 검수를 건너뛴다(규칙 검사는 그대로)
MIN_KEYWORDS = 2
RESUME_BANK_MIN_SIM = 0.60            # 이력서 항목 ↔ **Redis** 참고 문항 유사도 하한(2026-11-09 측정: 관련 0.65~0.75 · 무관 0.53~0.56)
# 사내문서 질문 경로만 별도 하한(2026-11-23 사용자 승인 ⓒ) — 0.60은 11-09 Redis 문항 실측값이고,
# 사내문서 질문 분포는 11-23 실측에서 **최고 0.598**(깊이 30, 0.60↑ 0개)이라 그 하한으로는 한 건도 통과하지 못했다.
RESUME_BANK_IQ_MIN_SIM = config.RESUME_BANK_IQ_MIN_SIM
MIN_CAND_DEPTH = 8                    # 카테고리별 후보 하한(2026-11-23 ⑦). n=1(기업)이면 n*3=3이라 주제가 겹치면 전멸한다.
RESUME_BANK_DEPTH = 5                 # 참고 문항 후보 수(2026-11-23 ⑧). 1개만 뽑으면 검색 깊이·후보 모음이 같이 좁아져
                                      # 하한을 넘는 후보가 있어도 놓친다 — 여러 개 받아 적합한 첫 1개를 쓴다.
PERSONAL = re.compile(Q.BLOCKLIST.pattern[:-1] + r"|복무|군필|전역|주소|생년)")   # 인적 정보 키워드는 모델이 남겨도 코드가 뺀다


# ======================= A. 질문 생성 =======================
class GenState(TypedDict, total=False):
    application: dict
    use_llm: bool
    job: dict
    snapshot: dict
    candidates: list            # 이력서 질문 후보(경력·프로젝트·학력 항목)
    keywords: list              # [{term, section, source}]
    keyword_report: dict        # A5 결과 {quality, quantity_ok, removed, added, rounds, passed}
    resume_fallback: str        # 비어 있지 않으면 이력서 질문 2개 → 직무 질문(RSQ-04) 사유
    picks: dict                 # category → 질문 은행 행 목록
    drafts: list                # 문항 초안 (finalize 전)
    final_questions: list
    log: list                   # 노드별 처리 기록(문장 원문 없이 종류·개수만)
    # 사내문서 RAG ② (ADLC v0.7 6.14.3, 2026-11-13 구현). 조각 본문은 State 안에만 — 로그·DB에는 조각 ID만(B-08)
    query_vector: list          # retrieve가 만든 질의 벡터(4-B가 재사용 → 임베딩 추가 호출 0회)
    query_vectors: list         # Multi-Query 벡터 목록(2026-11-16, ADLC 6.18). [0] = query_vector(기존 단일 쿼리). 단일 경로면 길이 1
    mq_report: dict             # {mode: baseline|multi|fallback|off, queries, embed_calls, reason} — 쿼리 문장 원문 없음
    internal_hits: list         # [{chunk_id, ref, doc_id, sec, doc_title, sec_title, disclosure, dept, sim, text}] 상위 4개까지
    internal_context: str       # 출처 머리글이 붙은 조립 문자열(2,000자 상한). 비면 기존 contextualize 경로
    internal_refs: list         # 주입한 조각 ID(doc_id#sec) — doc_refs 검사 기준
    forbid_terms: list          # Redis idoc:forbid 용어(질문 검사용 전부)
    forbid_ready: bool          # 사전을 읽었는가. False면 사내문서 경로 미사용(보수적)
    guard_report: dict          # 7-B {checked, blocked, reasons{종류: 건수}, model} — 원문 없음
    retrieve_report: dict       # retrieve 카테고리별 출처 {personality|job|company|resume_bank: redis|db|md|-, error?} (2026-11-16)
    backup_report: dict         # ②(2026-11-23) 백업 층(Redis·DB) 검사 기록 {checked, rule_rejects[], posting_rejects, fallback?}
    dual_report: dict           # Dual RAG 결과 {mode: dual|redis_only|chroma_only|safe, redis, chroma, job_rag, company_rag} (ADLC 6.18.3)


def _snapshot_squash(snapshot):
    return re.sub(r"\s+", "", Q._snapshot_text(snapshot))


def start(s: GenState) -> GenState:
    app = s["application"]
    job = db.one("""SELECT j.*, c.name company_name, r.name role_name FROM jobs j JOIN companies c ON c.id=j.company_id
                    JOIN job_roles r ON r.code=j.role_code WHERE j.id=%s""", app["job_id"])
    return {"job": job, "snapshot": app["resume_snapshot"] or {}, "log": []}


def extract_keywords(s: GenState) -> GenState:
    """코드: 확인 완료 이력서에서 질문 후보 항목과 키워드(이름·역할·기술)·근거 원문을 뽑는다."""
    cands = Q.resume_candidates(s["snapshot"])
    kws, seen = [], set()
    for c in cands:
        it = c["item"]
        for term in [c["label"], it.get("role", "")] + list(it.get("tech", []) or []):
            t = (term or "").strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                kws.append({"term": t, "section": c["section"], "source": (it.get("detail") or it.get("source") or "")[:160]})
    log = s["log"] + [f"extract_keywords: 후보 {len(cands)} · 키워드 {len(kws)}"]
    out = {"candidates": cands, "keywords": kws, "log": log, "resume_fallback": ""}
    if not cands:
        out["resume_fallback"] = "이력서에 경력·프로젝트·학력 내용이 없어 직무 질문으로 대체"
    return out


TRUST_SCHEMA = {"type": "object", "properties": {
    "quality": {"type": "integer", "minimum": 1, "maximum": 5}, "quantity_ok": {"type": "boolean"},
    "removed": {"type": "array", "items": {"type": "object", "properties": {"term": {"type": "string"}, "reason": {"type": "string"}},
                                           "required": ["term", "reason"]}},
    "added": {"type": "array", "items": {"type": "object", "properties": {"term": {"type": "string"}, "source": {"type": "string"}},
                                         "required": ["term", "source"]}},
    "keywords": {"type": "array", "items": {"type": "string"}}},
    "required": ["quality", "quantity_ok", "removed", "added", "keywords"]}


def evaluate_trust(s: GenState) -> GenState:
    """A5 키워드 신뢰도(prompts/evaluate/keyword_trust.md). 통과 = 최종 키워드가 모두 원문에 있음 · 양 충분.
    1회 재검수. 끝내 키워드가 부족하면 이력서 질문을 직무 질문으로 대체(RSQ-04). 품질 점수만 낮으면 기록만 남긴다."""
    if s["resume_fallback"] or not (s["use_llm"] and REVIEW):
        return {"keyword_report": {"skipped": True}}
    squash = _snapshot_squash(s["snapshot"])
    system = prompts.load("evaluate/keyword_trust", min_keywords=MIN_KEYWORDS)
    body = "키워드: " + ", ".join(k["term"] for k in s["keywords"]) + "\n\n이력서 원문:\n" + Q._snapshot_text(s["snapshot"])[:2500]
    report, problem = {}, ""
    for rnd in range(2):
        try:
            r = llm.chat_json(system, body + (f"\n\n이전 결과의 문제: {problem}" if problem else ""), TRUST_SCHEMA, timeout=90, temperature=0)
        except llm.LLMError:
            report = {"error": "llm"}
            break
        final = [k for k in r["keywords"] if k and re.sub(r"\s+", "", k.lower()) in squash and not PERSONAL.search(k)]
        ungrounded = [k for k in r["keywords"] if k and k not in final]
        report = {"quality": r["quality"], "quantity_ok": r["quantity_ok"] and len(final) >= MIN_KEYWORDS,
                  "removed": r["removed"][:20], "added": [a for a in r["added"] if re.sub(r"\s+", "", a["term"].lower()) in squash][:20],
                  "keywords": final, "rounds": rnd + 1}
        if report["quantity_ok"] and not ungrounded:
            break
        problem = ("원문에 없는 키워드: " + ", ".join(ungrounded[:5])) if ungrounded else f"근거 키워드가 {MIN_KEYWORDS}개 미만"
    out = {"keyword_report": report, "log": s["log"] + [f"evaluate_trust: {report.get('quality', '-')}점 · {report.get('rounds', 0)}회"]}
    if report.get("error"):
        return out                                        # 모델 실패 → 검수 없이 기존 후보로 진행
    removed = {re.sub(r"\s+", "", x["term"].lower()) for x in report.get("removed", [])}
    cands = [c for c in s["candidates"] if re.sub(r"\s+", "", c["label"].lower()) not in removed] or s["candidates"]
    out["candidates"] = cands
    if not report.get("quantity_ok"):
        out["resume_fallback"] = "이력서에서 질문 근거가 될 키워드가 부족해 직무 질문으로 대체(A5 검수)"
    return out


def _group(b):
    return b.get("dup_group") or (f"id{b['id']}" if b.get("id") else b.get("doc_id"))


def _redis_query_vector(s):
    """검색어 = 직무 이름·주요 업무·자격 요건 + A5를 통과한 이력서 키워드(없으면 추출 키워드). 임베딩 1번."""
    from . import question_chain as QC
    kws = (s.get("keyword_report") or {}).get("keywords") or [k["term"] for k in s.get("keywords", [])]
    query = QC._query({"job": s["job"], "resume_keywords": kws[:12]})
    return llm.embed([QC.QUERY_INSTRUCT + query])[0]


def _multi_queries(s):
    """Multi-Query(2026-11-16, ADLC 6.18) — 규칙으로 만드는 관점별 검색 쿼리. LLM을 쓰지 않는다(HyDE 아님).
    ① 기존 단일 쿼리(공고 직무명·업무·요건 + A5 키워드 12개, `question_chain._query` 그대로) — 언제나 맨 앞
    ② 공고 관점: 직무명·주요 업무·자격 요건만(이력서 키워드 없이)
    ③ 지원자 관점: 직무명 + A5 키워드 + 이력서 후보 항목(이름·역할·기술·내용 앞 160자) 3개까지
    같은 문장은 한 번만, 최대 config.RAG_MQ_N개. 각 1,200자 상한(①과 같은 기준)."""
    from . import question_chain as QC
    job = s["job"]
    kws = (s.get("keyword_report") or {}).get("keywords") or [k["term"] for k in s.get("keywords", [])]
    q1 = QC._query({"job": job, "resume_keywords": kws[:12]})
    q2 = " ".join(filter(None, [job.get("role_name"), job.get("duties"), job.get("requirements")]))[:1200]
    items = []
    for c in (s.get("candidates") or [])[:3]:
        it = c.get("item") or {}
        items.append(" ".join(filter(None, [c.get("label"), it.get("role"), " ".join(it.get("tech", []) or []),
                                            (it.get("detail") or "")[:160]])))
    q3 = " ".join(filter(None, [job.get("role_name"), ", ".join(kws[:12])] + items))[:1200]
    out = []
    for q in (q1, q2, q3):
        q = (q or "").strip()
        if q and q not in out:
            out.append(q)
    return out[:max(1, config.RAG_MQ_N)]


def _query_vectors(s):
    """(vectors, report). Multi-Query가 꺼져 있으면 기존 단일 쿼리 그대로(임베딩 1회).
    켜져 있으면 쿼리 N개를 한 번의 묶음 호출로 임베딩한다. 그 호출이 실패하거나 쿼리가 1개뿐이면
    기존 단일 쿼리로 내려간다(fallback). 단일 쿼리마저 실패하면 예외를 올린다(→ retrieve가 DB 은행).
    스위치가 꺼져 있으면 기존 _redis_query_vector만 부른다(새 import·호출 없음 — 기존 동작과 같음)."""
    if config.RAG_MULTI_QUERY:
        tried = False
        try:
            from . import question_chain as QC
            qs = _multi_queries(s)
            if len(qs) > 1:
                tried = True
                vecs = llm.embed([QC.QUERY_INSTRUCT + q for q in qs])    # 한 번의 HTTP 호출(묶음)
                if len(vecs) == len(qs):
                    return vecs, {"mode": "multi", "queries": len(qs), "embed_calls": 1}
            reason = "쿼리 1개" if not tried else "벡터 수 불일치"
        except Exception as e:   # noqa: BLE001 — 임베딩·조립 실패 → 기존 단일 쿼리
            reason = type(e).__name__
        return [_redis_query_vector(s)], {"mode": "fallback", "queries": 1, "embed_calls": 2 if tried else 1,
                                          "reason": reason}
    return [_redis_query_vector(s)], {"mode": "baseline", "queries": 1, "embed_calls": 1}


def _knn_fused(vectors, roles, cat, k):
    """벡터가 1개면 기존 KNN 그대로. 여러 개면 쿼리마다 같은 필터·깊이로 병렬 KNN → 문서 ID 기준 RRF(상위 k개).
    일부 쿼리가 실패하면 성공한 목록만 합치고, 모두 실패하면 예외(→ 호출 쪽 DB 은행)."""
    from .. import vectorstore
    kw = dict(role_codes=roles, categories=[cat], translated_only=True)
    if len(vectors) == 1:
        return vectorstore.knn(vectors[0], k=k, **kw)
    from . import rag_fusion
    res = rag_fusion.parallel(lambda v: vectorstore.knn(v, k=k, **kw), [(v,) for v in vectors])
    lists = [r for ok, r in res if ok]
    if not lists:
        raise res[0][1]
    return rag_fusion.rrf(lists, key=lambda h: h["doc_id"], k=config.RAG_RRF_K, limit=k)


def _pick_redis(vector, role_code, cat, n, used):
    """Redis 질문 은행(번역 에이전트를 거친 한·영 문항)에서 가까운 순으로 후보 n×2개 → 그중 n개를 무작위로.
    직무 질문은 지원 직무 문항만, 인성·기업 질문은 공통 + 지원 직무. 모자라면 None(→ DB 은행).
    vector: 벡터 1개(기존) 또는 벡터 목록(Multi-Query, 2026-11-16) — 목록이면 RRF로 합친 순위를 쓴다."""
    vectors = vector if (vector and isinstance(vector[0], (list, tuple))) else [vector]
    roles = [role_code] if cat == "job" else ["common", role_code]
    depth = max(16, n * 6)
    hits = _knn_fused(vectors, roles, cat, max(depth, config.RAG_MQ_K) if len(vectors) > 1 else depth)
    pool, seen = [], set(used)
    for h in hits:
        b = {"id": None, "doc_id": h["doc_id"], "text": (h.get("question_ko") or h.get("question") or "").strip(),
             "subcategory": h.get("intent_ko") or h.get("competency") or "", "dup_group": h.get("dup_group") or "",
             "competency": h.get("competency") or "", "sim": h.get("sim")}
        g = b["dup_group"] or b["competency"] or b["doc_id"]
        # 2026-11-12 RAG 평가: 두 문장짜리 은행 문항(영문 번역 문항 등)은 다시 쓰기가 실패하면 원문 그대로 나가므로 후보에서 뺀다
        if not b["text"] or g in seen or Q.BLOCKLIST.search(b["text"]) or not Q.one_sentence(b["text"]):
            continue
        seen.add(g)
        b["dup_group"] = g
        pool.append(b)
        if len(pool) >= n * 2:
            break
    if len(pool) < n:
        return None
    keep = set(random.sample(range(len(pool)), n))
    return [b for i, b in enumerate(pool) if i in keep]


def backup_issue(text, company=""):
    """② 백업 층(Redis·DB 은행) 문항의 문장 문제 — 사유 문자열, 문제없으면 "".

    2026-11-23 사용자 승인. 지금까지 문장 규칙과 공고 관련성은 **사내문서 질문에만** 걸렸고, 사내문서가
    모자라 백업 층으로 내려가면 아무 검사도 없이 그대로 나갔다. 실측으로 나간 예:
      · 백엔드 공고에 "객체 탐지 모델의 성능을 평가할 때 mAP가 무엇을 의미하는지" (DB 은행)
      · 제조 비전 공고에 "학습이 잘 되지 않을 때 확인하는 순서를 설명해 주세요." (Redis, 회사·공고 맥락 0)
    검사 항목은 사내문서 질문에 쓰는 것과 같다(`question_rules`).
    """
    t = (text or "").strip()
    if not t:
        return "empty"
    core = QR.core(t)
    checks = (("company_past", lambda: QR.COMPANY_PAST.search(t)),
              ("broken_word", lambda: QR.broken_word(t)),
              ("abstract_experience", lambda: QR.ABSTRACT_EXPERIENCE.search(core)),
              ("multi_interrog", lambda: QR.MULTI_INTERROG.search(core)),
              ("bare_ask", lambda: QR.BARE_ASK.search(core)),
              # ⑨(2026-11-23): 의문사로 묻고 명령형으로 맺는 문장. 은행 원문에서 실제로 나갔다
              # ("…어떻게 … 모델 성능을 개선해 주세요.") — 은행 원문은 고치지 않고 출제 때 걸러낸다.
              ("malformed_ask", lambda: QR.malformed_ask(t)),
              # F11(2026-11-23): 회사 이름 + '다시' — 은행 원문에도 나올 수 있다(이전 회사명이 있으면 허용)
              ("company_again", lambda: company and QR.company_again(t, company)),
              # F13(2026-11-23): 추상 명사구 겹침 — 은행 원문에도 있을 수 있다
              ("nested_abstract", lambda: QR.nested_abstract(t)))
    for name, fn in checks:
        try:
            if fn():
                return name
        except Exception:  # noqa: BLE001 — 규칙 하나가 깨져도 질문 생성을 멈추지 않는다
            continue
    if not Q.one_sentence(t):
        return "not_one_sentence"
    if Q.BLOCKLIST.search(t):
        return "blocklist"
    return ""


def screen_backup(cands, job, cat, report=None):
    """② 백업 층 후보에 문장 규칙 + 공고 관련성을 건다. **8문항은 반드시 유지한다**.

    안전망(사용자 원칙 ⓐ): **후보를 버리지 않고 순서만 바꾼다** — 검사를 통과한 것을 앞으로, 걸린 것을 뒤로
    보낸다. 호출 쪽은 필요한 수만큼 앞에서 가져가므로, 깨끗한 후보가 모자라도 **8문항은 그대로 채워진다**
    (면접이 8문항 아래로 내려가는 것보다 덜 나쁜 질문이 나가는 쪽이 낫다). 걸러낸 사유와 몇 개를 뒤로 보냈는지는
    `report`에 남겨 측정할 수 있게 한다(사용자 원칙 ⓑ).
    """
    rep = report if report is not None else {}
    if not cands:
        return cands
    rep.setdefault("checked", 0)
    rep.setdefault("rule_rejects", [])
    rep["checked"] += len(cands)
    kept, demoted = [], []
    for b in cands:
        why = backup_issue(b.get("text") or "", ((job or {}).get("company_name") or ""))
        if why:
            rep["rule_rejects"].append({"cat": cat, "reason": why, "text": (b.get("text") or "")[:80]})
            demoted.append(b)
        else:
            kept.append(b)
    if not kept:                                   # 전부 문장 규칙에 걸렸다 → 그대로(덜 나쁜 것이라도 내보낸다)
        rep["fallback"] = "rule"
        return cands
    if cat != "personality":                       # 인성에는 공고 관련성을 적용하지 않는다(2026-11-20 결정과 같음)
        passed = _filter_by_posting(list(kept), job, cat)
        if passed:
            rep["posting_rejects"] = rep.get("posting_rejects", 0) + (len(kept) - len(passed))
            ids = {id(b) for b in passed}
            demoted = [b for b in kept if id(b) not in ids] + demoted
            kept = list(passed)
        else:                                      # 공고 관련성에 전부 걸렸다 → 문장 검사만 통과한 순서로
            rep["fallback"] = "posting"
            rep["posting_rejects"] = rep.get("posting_rejects", 0) + len(kept)
    return kept + demoted                          # 통과분 먼저, 걸린 것은 뒤에 남겨 수를 보전한다


def _iq_terms():
    """사내문서 질문을 쓸 수 있는가 + 질문 검사용 금지 용어. (terms, 사유). terms가 None이면 쓰지 않는다."""
    if config.RAG_QUESTION_SOURCE != "chroma" or not config.INTERNAL_DOCS_RAG:
        return None, f"source={config.RAG_QUESTION_SOURCE}" if config.INTERNAL_DOCS_RAG else "internal_docs_rag_off"
    try:
        from . import internal_forbid
        ok, why = internal_forbid.rag_ready()
        if not ok:
            return None, why
        return internal_forbid.load_terms(), "ready"
    except Exception as e:  # noqa: BLE001 — 사전을 못 읽으면 쓰지 않는다(보수적, 6.13.8)
        return None, type(e).__name__


def _pick_internal(vectors, role_code, cat, n, used, terms, min_sim=None, k=None):
    """① 사내문서 질문 컬렉션(A17)에서 후보 n개 — 2026-11-17 사용자 지시로 **1순위 출처**(ADLC 6.19.5).
    질문은 이미 A18 검증을 통과한 한 문장이라 Redis 문항과 같은 모양으로 돌아온다.
    꺼짐·부족·오류면 None → 호출 쪽이 Redis 질문 은행(백업)으로 내려간다.

    **`n`과 `k`의 역할이 다르다**(2026-11-23 — 이걸 혼동해 ⓒ가 무효가 된 적이 있다):
      · `n` = **요구 개수**. `search_questions`는 하한을 통과한 후보가 n개보다 적으면 **전부 버리고 None**을 준다.
      · `k` = **검색 깊이**(기본 `max(16, n*6)`). 더 많이 보고 싶을 때는 `n`을 올리지 말고 **`k`를 올려라.**
    `n`을 깊이 삼아 5로 올렸더니 "하한 통과 후보가 5개 있어야 한다"가 돼, 2개뿐인 실제 분포에서 참고 문항이
    한 건도 붙지 않았다(11-23 4차 재측정에서 발견).
    """
    if terms is None:
        return None
    try:
        from . import internal_question_agent as IQ
        return IQ.search_questions(vectors if len(vectors) > 1 else vectors[0], cat, n, used,
                                   role_code=role_code, forbid_terms=terms, min_sim=min_sim, k=k)
    except Exception:  # noqa: BLE001 — 컬렉션 없음·Chroma 오류 → 백업 층으로
        return None


# 2026-11-19 사용자 지시(전제조건 2) — **공고 관련성**도 직무 일치와 같은 1차 필터다.
# 사고: "LLM 서비스" AI/AX 공고에 사내 콜센터 프로젝트 문서에서 나온 "상담사에게 어떻게 유도하는지"
# 질문이 나갔다. 직무 태그(dept_ai_ax)는 맞았지만 **공고 내용과는 무관**했다.
#
# 어휘 겹침으로는 못 가린다(실측: 그 질문이 공고와 '답변'·'생성' 같은 흔한 말로 통과했다).
# 그래서 **공고 본문 벡터와 질문 벡터의 코사인**으로 자른다. 2026-11-19 실측(AI/AX LLM 공고 기준):
#   무관 0.236 · 0.420 · 0.521   |   관련 0.653 · 0.658 · 0.693   → 경계 0.58이 둘을 정확히 가른다.
#
# 2026-11-23 사용자 승인으로 **0.58 → 0.50**(`config.POSTING_MIN_SIM`, 환경변수로 덮어쓸 수 있다).
# 11-19 측정은 사내문서 질문끼리 줄 세울 때의 경계였고, **탈락한 자리에 무엇이 들어오는지**를 보지 않았다.
# 11-23 실측(공고 15개 전수 · 질문 풀 602개, `eval_runs/p1_question_quality_2026-11-23/`):
#   - 0.58이면 직무 3 + 기업 1을 사내문서 질문으로 채우는 공고가 **15개 중 2개**뿐이고, 나머지는 백업 층
#     (Redis·DB 은행)으로 내려간다. 그 자리에 실제로 나간 질문 = 백엔드 공고에 "객체 탐지 mAP가 무엇인지",
#     데이터 분석 공고에 "트랜스포머 셀프 어텐션" — 공고와 무관한 질문이다.
#   - 같은 공고의 0.50~0.58 구간 후보는 전부 그 직무의 일이었다(공고 4 기준 8개: 응답 구조 설계 · 중계 API
#     도입 판단 · ERP 연동 오류 복구 · 지연 메트릭 …). 0.50 아래로 내려가야 다른 팀 일·일반 동기 질문이 섞인다.
#   - 0.50이면 직무 3 + 기업 1을 채우는 공고가 **15개 중 11개**가 된다.
# 즉 기준을 내리면 관련성이 낮아지는 게 아니라, 더 무관한 백업 질문을 밀어낸다.
POSTING_MIN_SIM = config.POSTING_MIN_SIM

# F13(2026-11-23 사용자 승인) — **재순위 감점**(하드 필터 아님). `internal_search.PREFERRED_TYPES`와 같은 구조이고
# 부호만 반대다. 사용자 지적: 기업 질문이 "검증 결과가 누락되지 않도록 기록하는 업무"처럼 추상적이고, 무슨 제품·무슨
# 검증인지가 없다. 그 질문들의 출처가 회계·결재·예산·공지 같은 **관리 문서**라 제품·프로젝트 맥락이 애초에 없다.
# 제외(안1: 기업 질문 251 → 179)는 **ai_ax 태그를 118 → 47로 깨뜨려 폐기**했다 — 커버리지가 무너지면 사용자가 더
# 싫어한 "우리 회사에 지원한 이유"(Redis 백업)가 돌아온다. 그래서 **순서만** 뒤로 보낸다.
#   · 회계·결재·예산·공지류 = 0.03 (관문 2 하한 0.50과 통과 코사인 사이 간격보다 작게 — 통과 여부는 안 바뀐다)
#   · 영업 절차·가이드류 = 0.01 (제품·고객 맥락이 있는 문서가 섞여 있어 더 작게)
DEMOTED_TYPES = {
    "approval": 0.03, "approval_authority": 0.03, "settlement": 0.03, "closing": 0.03, "budget": 0.03,
    "costing": 0.03, "revenue": 0.03, "receivable": 0.03, "foreign_tx": 0.03, "notice": 0.03,
    "manpower_request": 0.03,
    "bid_process": 0.01, "bid_decision": 0.01, "pricing_model": 0.01, "proposal_guide": 0.01,
    "contract_guide": 0.01, "sales_plan": 0.01, "planning": 0.01, "asset_registry": 0.01,
    "decision_guide": 0.01, "discovery_guide": 0.01,
}


def _posting_vector(job):
    """공고 본문 벡터(면접 1건에서 여러 번 쓰므로 State가 아니라 짧은 캐시로 들고 있는다)."""
    key = (job.get("id"), job.get("title"))
    hit = _PV_CACHE.get(key)
    if hit is not None:
        return hit
    from . import question_chain as QC
    txt = " ".join(str(job.get(k) or "") for k in
                   ("title", "role_name", "summary", "duties", "requirements", "preferred", "description"))[:1500]
    if not txt.strip():
        return None
    vec = llm.embed([QC.QUERY_INSTRUCT + txt])[0]
    if len(_PV_CACHE) > 32:
        _PV_CACHE.clear()
    _PV_CACHE[key] = vec
    return vec


_PV_CACHE = {}


def _cos(a, b):
    import math
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return s / (na * nb)


_SLOT_HEAD = re.compile(r"^\s*\{회사\}(?:에서|의|가|는|에)?\s*")


def _gate_text(t):
    """관문 2 비교용 문장 — 회사 자리와 허용 덧붙임(경험 안내·답변 형식 예시)을 뗀 본문."""
    return QR.core(_SLOT_HEAD.sub("", t or "")).replace("{회사}", "").strip()


def _filter_by_posting(cands, job, cat):
    """공고 본문과 유사도가 낮은 후보를 버린다. 전부 버려지면 None(→ 백업 층).
    임베딩·공고 벡터를 못 구하면 **막지 않는다**(면접이 멈추면 안 된다)."""
    if not cands:
        return cands
    try:
        jv = _posting_vector(job)
        if jv is None:
            return cands
        from . import question_chain as QC
        # 2026-11-20: A17 질문 앞의 회사 자리("{회사}에서 ~를 맡는다면")는 공고 내용이 아니라 질문 틀이다. 그대로 넣으면
        # 공고와의 코사인이 0.03~0.10 낮아져(실측: 공고 1 LLM 서비스 0.58 이상 1개 → 떼면 5개) 관련 질문까지 버려졌다 → 떼고 잰다.
        qvs = llm.embed([QC.QUERY_INSTRUCT + _gate_text(c.get("text") or "") for c in cands])
    except Exception as e:  # noqa: BLE001 — 임베딩 실패 → 거르지 않고 그대로 간다
        log.info("공고 관련성 검사 생략(%s): %s", cat, type(e).__name__)
        return cands
    kept = []
    for c, qv in zip(cands, qvs):
        sim = _cos(jv, qv)
        if sim >= POSTING_MIN_SIM:                      # **통과 판정은 원시 코사인으로만** — 감점이 뒤집지 않는다
            kept.append((sim - DEMOTED_TYPES.get((c.get("doc_type_code") or c.get("doc_type") or ""), 0.0), sim, c))
        else:
            log.info("공고 무관 질문 제외(%s, sim=%.3f): %s", cat, sim, (c.get("text") or "")[:50])
    # 2026-11-20: 공고와 가까운 순(호출 쪽이 앞에서 n개를 쓴다).
    # 2026-11-23 F13: 같은 구간이면 **관리 문서(회계·결재·예산·공지) 출신 질문을 뒤로** 보낸다. 감점은 이 정렬에만
    # 쓰고 위 통과 판정에는 쓰지 않으므로 **커버리지가 줄지 않는다**(기업 질문을 251 → 179로 줄이는 제외안은
    # ai_ax 태그가 118 → 47로 깨져 폐기했다 — 사용자가 싫어한 '지원한 이유' Redis 질문이 돌아온다).
    kept.sort(key=lambda x: (-x[0], -x[1]))
    return [c for _, _, c in kept] or None


def retrieve(s: GenState) -> GenState:
    """카테고리별 문항(QST-01). 이력서 대체 시 직무 문항을 더 뽑는다.
    1순위 = **사내문서 질문 컬렉션**(A17이 public 조각에서 미리 만들어 둔 질문, Chroma `internal_questions_*`).
    2순위 = Redis 질문 은행(백업, questionbank/regularDB 원본 → 번역 에이전트 → Redis).
    3순위 = DB `question_bank`(적게 쓴 순·dup_group 중복 제거) · 인성은 `character_assessment` md.
    어느 층이 비어도 면접은 끝까지 간다(H-01~H-09). 질의 벡터는 세 층이 **같은 것을 재사용**한다(임베딩 추가 0회)."""
    used, picks, src = set(), {}, {}
    seen_topics = set()                      # F4(2026-11-20): 세트 안에서 같은 프로젝트·같은 조각의 사내문서 질문은 1개만
    backup_report = {"checked": 0, "rule_rejects": []}   # ②(2026-11-23) 백업 층 검사 기록(측정용)
    need = dict(Q.PLAN)
    vector, vectors, role = None, None, s["job"]["role_code"]
    mq = {"mode": "off"}
    iq_terms, iq_why = _iq_terms()
    if s["use_llm"]:
        try:
            vectors, mq = _query_vectors(s)       # Multi-Query + RRF(6.18)면 여러 개, 아니면 1개
            vector = vectors[0]
        except Exception as e:                      # Redis·임베딩 모델이 없어도 면접은 진행
            src["error"] = type(e).__name__
    order = (["resume_fallback"] if s["resume_fallback"] else []) + ["personality", "job", "company"]
    for key in order:
        cat, n = ("job", Q.RESUME_N) if key == "resume_fallback" else (key, need[key])
        got, how = None, None
        if vector is not None:
            vecs = vectors if (vectors and len(vectors) > 1) else [vector]
            # 2026-11-23 사용자 승인(⑦) — 후보를 `n*3`으로 뽑으면 **기업처럼 n=1인 카테고리는 후보가 3개뿐**이다.
            # 실측: 공고 3의 기업 후보 3개가 전부 콜센터 묶음이라 `_distinct_topics`에 전부 걸려 Redis로 샜다
            # (직무 질문이 그 묶음을 먼저 가져간 뒤였다). 깊이를 8로 올리면 묶음 밖 후보(MSK-57)가 **항상**
            # 후보에 들어온다(깊이 3에서는 무작위 표집 때문에 빠질 수 있다, 깊이 3/8/12 실측).
            depth = max(n * 3, MIN_CAND_DEPTH)
            if cat != "personality":
                # 공고 무관이면 버린다(전제조건 2). 2026-11-20 리드 결정: **인성에는 적용하지 않는다** — 인성은
                # 공고 적합성 질문이 아니고, 가치·태도 문서 질문은 공고 본문과 유사도가 낮게 나와 대부분 버려졌다.
                # 2026-11-20: 전에는 이미 뽑힌 n개만 걸러 통과 질문이 풀에 있어도 백업으로 내려갔다 → 후보를 3배 뽑아
                # 관문 2를 통과한 것 중 공고와 가까운 순으로 n개를 쓴다(후보가 모자라면 n개로 다시 뽑는다).
                got = (_pick_internal(vecs, role, cat, depth, used, iq_terms)
                       or _pick_internal(vecs, role, cat, n, used, iq_terms))  # ① 사내문서 질문(주 출처)
                got = _filter_by_posting(got, s["job"], cat)
            else:
                got = (_pick_internal(vecs, role, cat, depth, used, iq_terms)
                       or _pick_internal(vecs, role, cat, n, used, iq_terms))  # ① 사내문서 질문(주 출처)
            got = _distinct_topics(got, n, seen_topics)
            how = "internal_q" if got else None
            short = n - len(got or [])
            if short > 0:
                # 2026-11-20: 관문 2가 **일부만** 버리면 전에는 모자란 채로 출제돼 기본 질문이 8개 아래로 내려갔다
                # (`kept or None` → 보충 없음). 모자란 수만 Redis에서 채운다(AC-107).
                taken = used | {_group(b) for b in (got or [])}
                try:
                    more = _pick_redis(vecs if len(vecs) > 1 else vector, role, cat, short, taken)   # ② Redis(백업)
                    more = screen_backup(more, s["job"], cat, backup_report)     # ②(2026-11-23) 백업 층도 검사
                    more = list(more or [])[:short]
                except Exception as e:
                    more = None
                    src["error"] = type(e).__name__
                if more:
                    got = (got or []) + more
                    how = "internal_q+redis" if how else "redis"
        src[key] = how or "db"
        short = n - len(got or [])
        if short > 0:   # 인성 대체 = character_assessment md(없으면 DB), 나머지 = DB 은행 — 모자란 수만큼
            taken = used | {_group(b) for b in (got or [])}
            fill = Q.pick_character(short, taken, s["snapshot"]) if cat == "personality" else Q.pick_bank(cat, short, taken)
            # ②(2026-11-23) DB 은행·인성 md도 같은 검사를 거친다. 모자라면 **검사를 건너뛴 원래 후보**가 돌아온다
            # (안전망) — 여기서 8문항이 깨지면 면접 자체가 성립하지 않기 때문이다.
            fill = list(screen_backup(list(fill), s["job"], cat, backup_report) or fill)[:short]
            if cat == "personality" and any(b.get("char_id") for b in fill):
                src[key] = "md" if not got else src[key] + "+md"
            elif got:
                src[key] = src[key] + "+db"
            got = (got or []) + list(fill)
        if cat == "personality":
            got = [_enrich_character(b) for b in got]
        picks[key] = got
        used |= {_group(b) for b in got}
    if not s["resume_fallback"]:                    # 이력서 질문 3개마다 질문 은행 참고 문항(RAG)
        picks["resume_bank"] = _resume_bank_refs(s, role, used, iq_terms, seen_topics) if s["use_llm"] else []
        _rb = [b for b in picks["resume_bank"] if b]
        src["resume_bank"] = ("internal_q" if any(b.get("iq_id") for b in _rb)
                              else "redis" if any(b.get("doc_id") for b in _rb) else "-")
    src["internal_q"] = iq_why
    return {"picks": picks, "query_vector": vector, "query_vectors": vectors or ([vector] if vector is not None else []),
            "mq_report": mq, "retrieve_report": dict(src), "backup_report": dict(backup_report),
            "log": s["log"] + ["retrieve: " + " · ".join(f"{k} {len(v)}({src.get(k)})" for k, v in picks.items())
                               + f" · 사내문서 질문 {iq_why}"
                               + (f" · redis 오류 {src['error']}" if "error" in src else "")
                               + f" · 쿼리 {mq.get('mode')} {mq.get('queries', 0)}개"
                               + (f"({mq['reason']})" if mq.get("reason") else "")
                               + (f" · 백업 검사 {backup_report['checked']}건 중 탈락 "
                                  f"{len(backup_report['rule_rejects'])}" if backup_report["checked"] else "")
                               + (f"(안전망 {backup_report['fallback']})" if backup_report.get("fallback") else "")]}


def retrieve_internal(s: GenState) -> GenState:
    """4-B RAG ② (ADLC v0.7 6.13.6 · 6.14.2): 사내문서 Chroma(public 컬렉션만)에서 기업 문항 1개의 근거 조각.
    질의 벡터는 retrieve가 만든 것을 재사용(임베딩 추가 호출 0회). 맨 앞에서 internal_forbid.rag_ready()
    (금지 용어 사전 idoc:forbid를 읽을 수 있고 + 마지막 적재 감사 게이트 idoc:audit 통과)가 False면 쓰지 않는다(H-08, 6.13.8).
    어떤 예외든 빈 컨텍스트로 두고 그래프를 멈추지 않는다 → generate는 기존 contextualize(공고 정보만)."""
    from . import internal_forbid, internal_search
    empty = {"internal_hits": [], "internal_context": "", "internal_refs": [], "forbid_terms": [], "forbid_ready": False}

    def done(msg, **extra):
        return {**empty, **extra, "log": s["log"] + ["retrieve_internal: " + msg]}

    if not config.INTERNAL_DOCS_RAG:
        return done("꺼짐(INTERNAL_DOCS_RAG=0)")
    if not s.get("use_llm") or s.get("query_vector") is None:
        return done("질의 벡터 없음 → 사용 안 함")
    picks = s.get("picks", {})
    if not (picks.get("company") or (config.RAG_DUAL_JOB and picks.get("job"))):
        return done("기업·직무 문항 없음 → 사용 안 함")
    try:
        ok, why = internal_forbid.rag_ready()
        if not ok:
            return done(f"관문 닫힘({why}) → 사용 안 함")
        terms = internal_forbid.load_terms()
    except Exception as e:  # noqa: BLE001 — ForbidUnavailable 포함, 검사할 수 없으면 쓰지 않는다
        return done(f"금지 용어 사전 없음({type(e).__name__}) → 사용 안 함")
    try:
        # 조각 검사는 일반 단어(주소·전화번호) 제외(chunk_terms) — 질문 문장 검사(_rule_ok·7-B·A15)는 terms 전부
        vecs = s.get("query_vectors") or [s["query_vector"]]
        how, r = "단일", None
        if len(vecs) > 1:                              # Multi-Query + RRF(6.18). 실패하면 기존 단일 쿼리로
            try:
                r = internal_search.search_multi(vecs, role_code=s["job"].get("role_code"),
                                                 forbid_terms=internal_forbid.chunk_terms(terms))
                how = f"RRF {r['mq']['ok']}/{r['mq']['queries']}"
            except Exception:  # noqa: BLE001
                r, how = None, "RRF 실패 → 단일"
        if r is None:
            r = internal_search.search(s["query_vector"], role_code=s["job"].get("role_code"),
                                       forbid_terms=internal_forbid.chunk_terms(terms))
    except Exception as e:  # noqa: BLE001
        return done(f"검색 오류 {type(e).__name__} → 사용 안 함", forbid_terms=terms, forbid_ready=True)
    hits = []
    for h in r.get("hits") or []:
        m = h["metadata"]
        if m.get("disclosure") != "public" or m.get("status") not in internal_search.ALLOWED_STATUS:
            return done("공개판이 아닌 조각 혼입 → 사용 안 함(B-09)", forbid_terms=terms, forbid_ready=True)
        body = h.get("body") or h["document"]
        if internal_forbid.find_terms(body, internal_forbid.chunk_terms(terms)):
            continue                                   # search가 이미 버렸어야 함 — 두 번째 확인
        hits.append({"chunk_id": h["id"], "ref": f"{m['doc_id']}#{m.get('sec', '')}", "doc_id": m["doc_id"], "sec": m.get("sec", ""),
                     "doc_title": m.get("doc_title", ""), "sec_title": m.get("sec_title", ""), "disclosure": m["disclosure"],
                     "dept": [x for x in (m.get("dept") or "").split("|") if x], "sim": round(float(h["sim"]), 4), "text": body})
    if not hits:
        return done("근거 0조각 → 기존 경로", forbid_terms=terms, forbid_ready=True)
    ctx = "\n\n".join(f"[근거 {h['ref']} · {h['doc_title']} > {h['sec_title']}]\n{h['text']}" for h in hits)
    refs = list(dict.fromkeys(h["ref"] for h in hits))
    return {"internal_hits": hits, "internal_context": ctx, "internal_refs": refs, "forbid_terms": terms, "forbid_ready": True,
            "log": s["log"] + [f"retrieve_internal: {len(hits)}조각({how}) · " + ", ".join(refs)]}


def _enrich_character(b):
    """Redis에서 온 인성 문항(common:CA-xxx)에 md 원본의 맞춤 범위·바꾸면 안 되는 것·의도를 붙인다."""
    if b.get("char_id"):
        return b
    cid = (b.get("doc_id") or "").split(":", 1)[-1]
    it = {x["char_id"]: x for x in Q.load_character_bank()}.get(cid)
    return {**b, **{k: it[k] for k in ("char_id", "competency", "intent", "scope", "keep", "signals", "probes")}} if it else b


def _resume_bank_refs(s, role, used, iq_terms=None, seen_topics=None):
    """이력서 질문 짝(항목·관점)마다 그 항목 내용으로 참고 문항 1개를 찾는다(이미 쓴 묶음 제외).

    출처 순서는 다른 카테고리와 같다(2026-11-18, ADLC 6.19.5 · M-55):
      ① 사내문서 질문(A17, Chroma `internal_questions_job`) → ② Redis 질문 은행(백업).
    질문 **본체는 계속 이력서**다(RSQ-01~07) — 여기서 찾는 것은 '곁들이는 참고 문항'뿐이고,
    참고 문항이 사내문서 출처면 그 이력서 문항도 사내문서 출처로 집계된다(M-55가 0.625 → 0.875).
    질의 벡터는 항목 내용으로 그때그때 만들며(이력서마다 다르다), 실패·없음이면 None(→ 이력서만으로 생성)."""
    from .. import vectorstore
    pairs, refs, seen = Q.resume_pairs(s["candidates"]), [], set(used)
    for cand, _angle in pairs:
        it = cand["item"]
        text = " ".join([cand["label"], it.get("role", ""), it.get("detail", ""), " ".join(it.get("tech", []) or [])])[:500]
        ref = None
        try:
            from . import question_chain as QC
            vec = llm.embed([QC.QUERY_INSTRUCT + text])[0]
            if iq_terms is not None:                      # ① 사내문서 질문 먼저
                # 2026-11-19 사용자 지시 — 이력서 항목과 **상관없는** 사내문서 질문이 붙는 사고
                # (Mercari 가격예측 이력서에 '신상품 출시 후 경과 일수'(수요예측 문서) 질문이 합성됨).
                # 원인: 사내문서 경로가 전역 하한 0.30만 썼다. Redis 경로와 **같은 0.60**을 쓴다.
                # 2026-11-23 사용자 승인(⑧) — **후보를 1개가 아니라 여러 개 받아** 적합한 첫 1개를 쓴다.
                # 하한(0.60)은 그대로다. 11-23 실측: 이력서 질문 14개 중 사내문서 참고가 붙은 것이 **0개**였는데,
                # 원인은 하한이 높아서가 아니라 **후보를 1개만 뽑아서**였다. `n=1`이면 검색 깊이가 16으로 좁아지고
                # 후보 모음도 4개에서 끊긴 뒤 그중 하나를 무작위로 고르기 때문에(`search_questions`), 0.60을 넘는
                # 후보가 풀에 있어도 후보군에 들어오지 못했다(YS-J3·NH-J4·OY-J5에서 0.60↑ 후보 존재를 실측).
                # ⓒ 하한은 사내문서 경로만 0.55. **요구 개수는 1개**이고 ⑧은 `k`(검색 깊이)로만 넓힌다 —
                # `n`을 깊이로 쓰면 "하한 통과 후보가 n개 있어야 한다"가 돼 한 건도 못 붙는다(4차 재측정에서 확인).
                got = _pick_internal([vec], role, "job", 1, seen, iq_terms,
                                     min_sim=RESUME_BANK_IQ_MIN_SIM, k=RESUME_BANK_DEPTH * 6) or []
                # 2026-11-23 사용자 승인(④) — 참고 문항도 **세트 안 중복 검사**를 거친다.
                # 사고: 공고 3 세트에서 이력서 참고 문항과 직무 질문이 둘 다 `JD-AIAX-01`에서 나와
                # 같은 주제(회귀 평가 / 평가 데이터셋)를 두 번 물었다. F4가 지적한 '같은 주제 두 번'이
                # 직무·기업끼리는 막혀 있었지만 **이력서 참고 문항 ↔ 직무 질문** 사이에는 열려 있었다.
                # ⑧으로 후보가 여러 개가 됐으므로, 중복에 걸리면 **버리지 않고 다음 후보로 넘어간다**.
                if got and seen_topics is not None:
                    got = _distinct_topics(got, 1, seen_topics)
                got = list(got or [])[:1]
                if got:
                    b = got[0]
                    g = b.get("dup_group") or b.get("competency") or b.get("iq_id")
                    seen.add(g)
                    ref = {"id": None, "iq_id": b.get("iq_id"), "doc_id": b.get("doc_id"), "text": b["text"],
                           "subcategory": b.get("subcategory") or b.get("competency") or "",
                           "doc_refs": list(b.get("doc_refs") or []), "company_facts": list(b.get("company_facts") or [])}
            if ref is None:                               # ② Redis 질문 은행(백업)
                for h in vectorstore.knn(vec, k=8, role_codes=[role], categories=["job"], translated_only=True):
                    q = (h.get("question_ko") or h.get("question") or "").strip()
                    g = h.get("dup_group") or h.get("competency") or h["doc_id"]
                    if float(h.get("sim") or 0) < RESUME_BANK_MIN_SIM:
                        break                    # 가장 가까운 것도 관련이 약하면 참고 문항 없이(이력서만으로) 생성
                    if q and g not in seen and not Q.BLOCKLIST.search(q):
                        seen.add(g)
                        ref = {"id": None, "doc_id": h["doc_id"], "text": q, "subcategory": h.get("intent_ko") or h.get("competency") or ""}
                        break
        except Exception:
            ref = None
        refs.append(ref)
    return refs


def _bank_fields(b):
    """은행 행 → 문항 출처 필드. DB 은행은 bank_id, 그 밖은 gen_method에 출처 ID를 남긴다(bank_id는 DB 행 전용 FK).
    사내문서 질문(A17)은 `internal_q:<질문 ID>` — `doc_id`도 같이 있으므로 이 검사가 먼저다."""
    if b.get("iq_id"):
        return {"bank_id": None, "gen_method": "internal_q:" + b["iq_id"]}
    if b.get("doc_id"):
        return {"bank_id": None, "gen_method": "redis:" + b["doc_id"]}
    if b.get("char_id"):
        return {"bank_id": None, "gen_method": "character:" + b["char_id"]}
    return {"bank_id": b["id"], "gen_method": "db"}


_SEVERE = ("company_past", "broken_word")   # 다시 쓴 문장에 있으면 무조건 원문으로(원문이 어떻든)
# 문장 구조가 깨진 경우(두 개 묻기·지시어·조각) — 원문도 구조가 깨진 게 아니면 원문으로(2026-11-20 재현: 기업 문항 재작성이
# "…발생할 수 있는 문제는 무엇이며, 어떻게 접근할지"로 두 개 묻기가 됐는데 원문은 '우리 회사' 표현만 걸려 재작성이 살아남았다)
_STRUCTURAL = ("multi_ask", "multi_interrog", "dangling_ref", "fragment_colon")


def _keep_rewrite(text, orig, company=None):
    """면접 중 은행 문항을 다시 쓴 문장을 쓸지(2026-11-20). 문장 규칙 위반이면 원문으로 되돌린다.
    단 원문도 같은 정도로 어긴다면(예: 은행 원문 '우리 회사의 …') 되돌려도 나아지지 않으므로 다시 쓴 쪽을 둔다 —
    **회사 경력 전제(F9)·깨진 낱말(F8)** 은 예외 없이 원문으로."""
    if text == orig:
        return True
    why = QR.interview_issue(text, company)
    if not why:
        return True
    head = why.split(":")[0]
    if head in _SEVERE:
        return False
    orig_why = QR.interview_issue(orig, company)
    if head in _STRUCTURAL:
        return orig_why.split(":")[0] in _STRUCTURAL + _SEVERE
    return bool(orig_why)


def _posting_text(job):
    """F4 주제 비교에서 뺄 공고 어휘(공고에 원래 있는 말은 '같은 주제'의 근거가 아니다)."""
    return " ".join(str((job or {}).get(k) or "") for k in ("title", "role_name", "summary", "duties", "requirements", "preferred"))


def _overlaps(text, drafts, job):
    """앞서 만든 문항 중 같은 핵심 주제를 공유하는 것이 있는가(회사 이름·공고 어휘는 빼고, F4)."""
    ign = [(job or {}).get("company_name") or "", _posting_text(job)]
    return any(QR.shared_topic(text, d["text"], ignore=ign) for d in drafts)


# 같은 프로젝트의 상세판·공개판·장애 기록·단위코드 문서는 문서 ID가 달라도 한 주제다(2026-11-20 실측: '콜센터 AI' 4문서,
# '수요예측' 대시보드·배치·배치 서버 장애 4문서). 제목에 이 낱말이 있으면 그 묶음 이름을 주제 열쇠로 쓴다.
_TOPIC_FAMILIES = (("callcenter", ("콜센터",)), ("forecast", ("수요예측", "배치 서버")))


def _topic_key(b):
    """사내문서 질문의 주제 열쇠 = 프로젝트 묶음 이름, 묶음이 없으면 출처 문서 ID(같은 문서 = 같은 주제)."""
    title = b.get("doc_title") or ""
    for name, words in _TOPIC_FAMILIES:
        if any(w in title for w in words):
            return name
    return b.get("doc_id") or b.get("chunk_id") or b.get("iq_id") or ""


def _distinct_topics(cands, n, seen_topics):
    """후보(이미 좋은 순) 중 앞서 뽑은 문항과 **같은 프로젝트·같은 조각**이 아닌 것만 n개. 모자라면 모자란 채로 돌려준다
    (호출 쪽이 Redis·DB로 채운다). 2026-11-20 사용자 지적: 직무 '상담사 답변을 돕는 AI 검증 시나리오'와 기업 '상담사가 AI 추천
    답변을 검토하는 서비스의 운영 지표'가 같은 콜센터 AI 프로젝트에서 나와 본질문 2개가 겹쳤다(질문 문장 단어 비교는 공고 어휘를
    빼고 보느라 놓쳤다 → 출처 문서로 가른다)."""
    if not cands:
        return cands
    out = []
    for b in cands:
        if not b.get("iq_id"):
            out.append(b)
            continue
        keys = {"topic:" + _topic_key(b), "chunk:" + (b.get("chunk_id") or b.get("iq_id"))}
        if keys & seen_topics:
            continue
        seen_topics |= keys
        out.append(b)
        if len(out) >= n:
            break
    return out or None


# F12(2026-11-23 사용자 지적): 한 세트 8문항 중 6개가 "헤드헌터랩_경기에서…"로 시작해 전부 같은 틀로 보였다.
# 회사 이름은 **세트당 이만큼만** 쓰고, 나머지는 회사 이름 없이 직무 맥락으로 돌린다(F1은 유지 — "이 직무에서"로
# 공고·직무 맥락은 남는다). 문두 표현도 돌려 쓴다.
COMPANY_NAME_MAX = 3
# 2026-11-23 리드 승인(ⓑ): 상한을 **세트 전체 공용**으로 넓힌다 — 전에는 A17 질문(`_internal_draft`)만 셌고
# 이력서 템플릿·합성 문항과 백업 층(Redis·DB) 다시 쓰기는 회사 이름을 제한 없이 넣었다(실측: 한 세트 8문항 중
# 6개가 회사 이름으로 시작). 동시에 **하한 2**를 둔다 — 상한만 있으면 A17 문항이 전부 '이 직무에서'로 바뀌어
# 공고·회사 맥락(F1)이 세트에서 완전히 사라질 수 있다.
COMPANY_NAME_MIN = 2
_COMPANY_HEADS = ("{회사}에서", "{회사}에 입사해", "{회사}의 이 직무에서")


def strip_company(text, name):
    """문장에서 회사 이름을 '이 직무'로 바꾼다(F12 상한 초과분 정리 · `fill_company`의 내림 분기와 같은 치환)."""
    t, n = (text or ""), (name or "").strip()
    if not n or n not in t:
        return t
    for suf, rep in (("에서", "이 직무에서"), ("의", "이 직무의"), ("에", "이 직무에"), ("은", "이 직무는"),
                     ("는", "이 직무는"), ("이", "이 직무가"), ("가", "이 직무가")):
        t = t.replace(n + suf, rep)
    return t.replace(n, "이 직무")


def normalize_company_mentions(drafts, job):
    """세트 전체에서 회사 이름을 쓰는 문항 수를 `COMPANY_NAME_MIN`~`COMPANY_NAME_MAX`로 맞춘다(모델 호출 0회).

    초과하면 **뒤쪽 문항부터** 이름을 '이 직무'로 바꾸고, 모자라면 A17 문항 중 상한 때문에 이름을 뺐던 것
    (`company_slot`)을 되살린다. 질문 내용은 바꾸지 않는다 — 회사 이름 자리만 다룬다.
    반환: {used, dropped, restored} (측정용).
    """
    name = ((job or {}).get("company_name") or "").strip()
    rep = {"used": 0, "dropped": 0, "restored": 0}
    if not name or not drafts:
        return rep
    has = [i for i, d in enumerate(drafts) if name in (d.get("text") or "")]
    for i in reversed(has[COMPANY_NAME_MAX:]):            # 초과분: 뒤에서부터 뺀다(앞 문항이 맥락을 유지)
        drafts[i]["text"] = strip_company(drafts[i]["text"], name)
        rep["dropped"] += 1
    if len(has) < COMPANY_NAME_MIN:                       # 모자라면 뺐던 A17 문항을 되살린다
        for d in drafts:
            if len(has) + rep["restored"] >= COMPANY_NAME_MIN:
                break
            slot = d.get("company_slot")
            if slot and name not in (d.get("text") or ""):
                d["text"] = fill_company(slot, job)       # state 없이 = 그대로 회사 이름으로
                rep["restored"] += 1
    rep["used"] = sum(1 for d in drafts if name in (d.get("text") or ""))
    return rep


def fill_company(text, job, state=None):
    """A17 질문의 `{회사}` 자리를 채운다(2026-11-20 F1·F3, 문자열 치환 — 모델 호출 0회).

    `state`(세트 단위 dict)를 주면 F12를 적용한다: 회사 이름을 **세트당 `COMPANY_NAME_MAX`개 문항까지만** 쓰고,
    그 뒤에는 "이 직무에서"로 바꿔 같은 문두가 반복되지 않게 한다. 회사 이름을 쓰는 문항도 문두 표현을 돌려 쓴다.
    `state`가 없으면(단독 호출·시험) 기존 동작 그대로 — 전부 회사 이름으로 채운다.
    """
    t = text or ""
    name = ((job or {}).get("company_name") or "").strip() or "지원하신 회사"
    if state is None:
        return t.replace("{회사}", name)
    used = state.get("company_used", 0)
    if "{회사}" not in t:
        return t
    if used >= COMPANY_NAME_MAX:
        # 회사 이름을 더 쓰지 않는다 — 문두는 '이 직무에서', 문장 중간의 회사 자리는 '이 직무'로 받는다.
        state["company_dropped"] = state.get("company_dropped", 0) + 1
        return t.replace("{회사}에서", "이 직무에서").replace("{회사}의", "이 직무의").replace("{회사}", "이 직무")
    state["company_used"] = used + 1
    head = _COMPANY_HEADS[used % len(_COMPANY_HEADS)]
    if t.startswith("{회사}에서") and head != "{회사}에서":
        t = head + t[len("{회사}에서"):]
    return t.replace("{회사}", name)


def _internal_draft(b, cat, job=None, state=None):
    """A17 질문은 **면접마다 다시 쓰지 않는다**(기본, `RAG_IQ_REWRITE=0`). 이미 A18 검증(한 문장·금지 용어·
    마스킹 표식·조각 근거·지어낸 토큰·허위 경험 전제)을 통과한 문장이고, 면접 중 다시 쓰는 경로는 20명 중
    12명이 실패했다(QO-13). 조각 출처(`doc_refs`)·근거 문구(`company_facts`)를 붙여 7-B 관문 대상으로 남긴다.
    회사 자리(`{회사}`)만 지원 회사 이름으로 바꾼다."""
    text = fill_company(b["text"], job, state)       # F12(2026-11-23): 회사 이름은 세트당 상한까지만
    # 상한 때문에 이름을 뺀 문항은 원문(`{회사}` 자리 그대로)을 들고 있는다 —
    # 세트 전체 집계에서 하한(COMPANY_NAME_MIN)에 못 미치면 `normalize_company_mentions`가 되살린다.
    return {"category": cat, "eval_items": Q.EVAL_ITEMS[cat], "text": text, "orig": text,
            "company_slot": b["text"] if "{회사}" in (b.get("text") or "") else "",
            **_bank_fields(b), "intent": b.get("intent") or b.get("subcategory") or "",
            "company_facts": list(b.get("company_facts") or []), "doc_refs": list(b.get("doc_refs") or []),
            "iq_source": True}


def generate(s: GenState) -> GenState:
    """A2 출제 (2026-11-09 구성 8개):
    인성 2 = 인성 은행(RAG) → 인성 다듬기 에이전트 / 이력서 3 = 이력서 항목 + 은행 참고 문항(RAG) → 합치는 생성 에이전트
    직무 2 = 은행(RAG) + 공고 + 이력서 → 섞어 다시 쓰기 / 기업 1 = 은행(RAG) → 회사 맞춤."""
    drafts, use_llm = [], s["use_llm"]
    role_name = s["job"].get("role_name") or ""
    n_iq = {"personality": 0, "job": 0, "company": 0}
    n_iq_resume = 0                       # 이력서 문항 중 참고 문항이 사내문서 질문이었던 수(2026-11-18, M-55)
    # F12(2026-11-23): 세트 단위 회사 이름 카운터 — `seen_topics`처럼 이 세트 안에서만 들고 다닌다.
    comp_state = {"company_used": 0, "company_dropped": 0}
    for b in s["picks"].get("personality", []):
        if b.get("iq_id") and not config.RAG_IQ_REWRITE:          # 사내문서 질문(인성) — 그대로 출제
            n_iq["personality"] += 1
            drafts.append(_internal_draft(b, "personality", s["job"], comp_state))
            continue
        text = Q.contextualize_character(b, s["job"], s["snapshot"]) if use_llm else b["text"]
        if not _keep_rewrite(text, b["text"], s["job"].get("company_name")):   # 다시 쓴 문장이 문장 규칙을 어기면 원문(2026-11-20)
            text = b["text"]
        drafts.append({"category": "personality", "eval_items": Q.EVAL_ITEMS["personality"], "text": text, "orig": b["text"],
                       **_bank_fields(b), "intent": b.get("intent") or b.get("subcategory") or ""})
    if s["resume_fallback"]:
        for b in s["picks"].get("resume_fallback", []):
            ref = {"fallback_reason": s["resume_fallback"], **({"bank_doc": b["doc_id"]} if b.get("doc_id") else {})}
            drafts.append({"category": "resume", "eval_items": Q.EVAL_ITEMS["job"], "text": b["text"], "orig": b["text"], "bank_id": b["id"],
                           "gen_method": "fallback_job", "resume_ref": ref, "intent": b.get("subcategory") or ""})
    else:
        banks = s["picks"].get("resume_bank") or []
        company = s["job"].get("company_name") or ""
        prev = [d["text"] for d in drafts]            # F4: 이 세트에서 이미 낸 질문(인성 포함)과 같은 주제를 다시 묻지 않는다
        pairs = Q.resume_pairs(s["candidates"])
        spare = [c for c in s["candidates"] if all(c is not p for p, _ in pairs)]   # 짝에 안 들어간 이력서 항목(주제가 겹칠 때 교체용)
        for i, (cand, angle) in enumerate(pairs):
            tpl = Q.template_text(angle, cand["label"], company, cand.get("prev_company"))
            if use_llm:
                bank = banks[i] if i < len(banks) else None
                r = Q.make_resume_question(cand, angle, s["snapshot"], bank, role_name, company, avoid=prev,
                                           ignore_text=_posting_text(s["job"]))
                # F4(2026-11-20 사용자 응시: '반사광' 하나로 이력서 2문항 + 꼬리질문 2개): 그래도 앞 질문과 주제가 겹치면
                # 아직 안 쓴 다른 이력서 항목으로 한 번 바꿔 만든다.
                if spare and any(QR.shared_topic(r["text"], p, ignore=[company, _posting_text(s["job"])]) for p in prev):
                    alt = spare.pop(0)
                    r2 = Q.make_resume_question(alt, angle, s["snapshot"], bank, role_name, company, avoid=prev,
                                                ignore_text=_posting_text(s["job"]))
                    if not any(QR.shared_topic(r2["text"], p, ignore=[company, _posting_text(s["job"])]) for p in prev):
                        cand, r, tpl = alt, r2, Q.template_text(angle, alt["label"], company, alt.get("prev_company"))
            else:
                ref = {"section": cand["section"], "index": cand["index"], "label": cand["label"],
                       "quote": (cand["item"].get("detail") or cand["item"].get("source") or "")[:160]}
                r = {"text": tpl, "resume_ref": ref, "gen_method": "template"}
            if r.get("gen_method") == "template":
                tpl = r["text"]                      # 템플릿이 라벨 없는 대체 문장으로 바뀌었으면 A6 되돌림도 그 문장으로(F4)
            d = {"category": "resume", "eval_items": Q.EVAL_ITEMS["resume"], "bank_id": None, "orig": tpl,
                 "intent": "이력서 항목의 " + Q.ANGLES[angle], **r}
            b = banks[i] if i < len(banks) else None
            if b and b.get("iq_id") and d.get("gen_method") == "llm":
                # 참고 문항이 사내문서 질문(A17)이면 그 출처를 이 문항에도 남긴다(2026-11-18, M-55).
                # iq_source=True — 근거는 A17 질문 자신이고 A18이 이미 대조했으므로, 이 면접에 주입된
                # 조각(internal_refs)과 다시 맞추지 않는다(6.19.5의 A17 분기와 같은 이유).
                # doc_refs를 달면 7-B 누출 관문의 검사 대상이 된다 — 사내문서가 닿는 문항은 전부 검사받는다.
                d.update({"doc_refs": list(b.get("doc_refs") or []), "company_facts": list(b.get("company_facts") or []),
                          "iq_source": True, "ref_text": b.get("text") or ""})
                n_iq_resume += 1
            drafts.append(d)
            prev.append(d["text"])
    n_rag, rag_by = 0, {"job": 0, "company": 0}
    for cat in ("job", "company"):
        for b in s["picks"].get(cat, []):
            if b.get("iq_id") and not config.RAG_IQ_REWRITE:      # 사내문서 질문(직무·기업) — 그대로 출제
                n_iq[cat] += 1
                drafts.append(_internal_draft(b, cat, s["job"], comp_state))
                continue
            rag = None
            if not use_llm:
                text = b["text"]
            elif cat == "job":
                if config.RAG_DUAL_JOB and s.get("internal_context") and s.get("forbid_ready"):
                    # Dual RAG(6.18.3): 은행 문항(Redis) + 사내문서 맥락(Chroma) + 공고 + 이력서. 실패하면 기존 섞어 쓰기
                    rag = Q.contextualize_job_rag(b["text"], s["job"], s["snapshot"], b.get("subcategory") or "",
                                                  s["internal_context"], s.get("internal_refs") or [],
                                                  [h["text"] for h in s.get("internal_hits") or []], s.get("forbid_terms") or [])
                text = rag["text"] if rag else Q.contextualize_job(b["text"], s["job"], s["snapshot"], b.get("subcategory") or "")
            else:
                if s.get("internal_context") and s.get("forbid_ready"):   # A15: 공개판 사내문서 근거로 회사 맞춤
                    rag = Q.contextualize_rag(b["text"], s["job"], b.get("subcategory") or "", s["internal_context"],
                                              s.get("internal_refs") or [], [h["text"] for h in s.get("internal_hits") or []],
                                              s.get("forbid_terms") or [])
                text = rag["text"] if rag else Q.contextualize(b["text"], s["job"], b.get("subcategory") or "")
            if not _keep_rewrite(text, b["text"], s["job"].get("company_name")):
                # 2026-11-20 F9: 은행 문항을 회사 맞춤으로 다시 쓴 문장이 "헤드헌터랩_경기에서 … 맡아, … 어떤 접근을 취했는지"처럼
                # 그 회사 경력을 전제하거나 두 개를 묻으면 쓰지 않고 은행 원문으로 둔다(사용자 응시 attempt 14의 기업 문항).
                log.info("다시 쓴 %s 문항 버림(%s): %s", cat, QR.interview_issue(text), text[:50])
                text, rag = b["text"], None
            if text != b["text"] and _overlaps(text, drafts, s["job"]) and not _overlaps(b["text"], drafts, s["job"]):
                # F4(2026-11-20 재현): 직무 문항을 이력서 키워드와 섞어 다시 쓰다 이력서 문항과 같은 주제("소비기한 인쇄 불량
                # 검사 프로젝트에서 …")가 됐다 → 섞어 쓴 문장 대신 은행 원문을 쓴다(세트 안 같은 주제 1문항).
                # 원문도 이미 겹치면 되돌려도 나아지지 않으므로 그대로 둔다(다시 쓰기가 겹침을 '들여온' 경우만 되돌린다).
                text, rag = b["text"], None
            d = {"category": cat, "eval_items": Q.EVAL_ITEMS[cat], "text": text, "orig": b["text"],
                 **_bank_fields(b), "intent": b.get("subcategory") or ""}
            if rag:
                n_rag += 1
                rag_by[cat] += 1
                d.update(company_facts=rag["company_facts"], doc_refs=rag["doc_refs"])
            drafts.append(d)
    rr = s.get("retrieve_report") or {}
    redis_ok = any("redis" in str(rr.get(k) or "") for k in ("personality", "job", "company", "resume_bank"))   # "internal_q+redis" 포함
    chroma_ok = bool(s.get("internal_context") and s.get("forbid_ready")) or sum(n_iq.values()) > 0 or n_iq_resume > 0
    mode = {(True, True): "dual", (True, False): "redis_only", (False, True): "chroma_only", (False, False): "safe"}[(redis_ok, chroma_ok)]
    total_iq = sum(n_iq.values())
    # 사내문서 출처 문항 = A17 질문 + 조각 근거가 남은 문항(이 둘은 겹치지 않는다 — A17 문항은 다시 쓰지 않으므로)
    # ⓑ(2026-11-23): 회사 이름 상한·하한을 **세트 전체**로 맞춘다(A17·이력서·백업 층 전부 포함, 모델 호출 0회)
    comp_rep = normalize_company_mentions(drafts, s["job"])
    dual = {"mode": mode, "redis": redis_ok, "chroma": chroma_ok, "job_rag": rag_by["job"],
            "company_rag": rag_by["company"], "internal_q": dict(n_iq), "internal_q_total": total_iq,
            "resume_iq_ref": n_iq_resume,
            "internal_total": total_iq + n_rag + n_iq_resume, "questions": len(drafts),
            "company_mentions": comp_rep}
    return {"drafts": drafts, "dual_report": dual,
            "log": s["log"] + [f"generate: {len(drafts)}문항" + (f" · 사내문서 질문 {total_iq}" if total_iq else "")
                               + (f" · 사내문서 근거 {n_rag}" if n_rag else "") + f" · RAG {mode}"
                               + f" · 회사명 {comp_rep['used']}문항"
                               + (f"(뺀 것 {comp_rep['dropped']})" if comp_rep["dropped"] else "")
                               + (f"(되살림 {comp_rep['restored']})" if comp_rep["restored"] else "")]}


PHIL_SCHEMA = {"type": "object", "properties": {
    "scores": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 5}},
    "pass": {"type": "boolean"}, "problems": {"type": "array", "items": {"type": "string"}},
    "revised_question": {"type": "string"}}, "required": ["scores", "pass", "problems", "revised_question"]}


def _rule_ok(d, text, snapshot, forbid_terms=()):
    """A2·A6 공통 규칙: 길이 10~160자, 차단 목록, 이력서 질문은 고유 명칭·수치가 이력서에 있어야 함.
    사내문서 근거 문항(doc_refs)은 A6·A7이 고친 문장에도 금지 용어 사전 검사(ADLC 6.13.7 조기 차단, 최종 관문은 7-B)."""
    if not (10 <= len(text) <= 160) or Q.BLOCKLIST.search(text):
        return False
    if d.get("doc_refs"):
        from . import internal_forbid
        if not forbid_terms or internal_forbid.find_terms(text, forbid_terms):   # 사전 없음 = 검사 불가 → 실패(보수적)
            return False
        if internal_forbid.masked_hits(text):                                   # 마스킹 표식·가명·사본 ID
            return False
    if d["category"] == "resume" and d.get("gen_method") == "llm":
        return Q.grounded(text, [], snapshot)
    return True


def _prev_names(d):
    """F11(2026-11-23): 이력서 질문의 '다시'를 허용할 근거 이름 — 이전 회사명과 항목 라벨.
    A6가 고쳐 쓴 문장을 다시 검사할 때도 같은 기준을 쓴다(허용 범위가 생성 때와 달라지면 안 된다)."""
    ref = (d or {}).get("resume_ref") or {}
    return ((ref.get("prev_company") or ""), (ref.get("label") or ""))


def evaluate_philosophy(s: GenState) -> GenState:
    """A6 철학 적합성(prompts/evaluate/philosophy_fit.md): 원칙 6개 모두 4점 이상이면 통과.
    고친 질문은 규칙을 다시 검사하고 최대 2번 고친다. 끝내 실패하면 은행 원문·템플릿으로 되돌린다. 인성 문항(은행 원문)은 검사하지 않는다."""
    if not (s["use_llm"] and REVIEW):
        return {}
    job, drafts, stats = s["job"], [], {"pass": 0, "revised": 0, "reverted": 0, "error": 0}
    for d in s["drafts"]:
        d = dict(d)
        if d["category"] == "personality" or _is_a17(d):
            # 2026-11-20: 사내문서 질문(A17)도 건너뛴다 — 오프라인에서 A18 검증을 통과한 문장이고(QST-14 ③ 면접 중 다시
            # 쓰지 않음), 전에는 로컬 4B가 여기서 고쳐 쓰며 회사명·과거 전제·두 개 묻기를 새로 넣었다
            # (qa_full_2026-11-19: A17 직무 9개 중 3개 재작성, 예 "헤드헌터랩_경기에서 … 어떤 경험과 판단 과정을 거쳤는지").
            drafts.append(d)
            continue
        system = prompts.load("evaluate/philosophy_fit", role_name=job.get("role_name") or "", company_name=job.get("company_name") or "",
                              eval_items=", ".join(d["eval_items"]))
        info = f"회사 정보: {(job.get('company_intro') or '')[:300]} / 주요 업무: {(job.get('duties') or '')[:300]}"
        text, ok = d["text"], False
        for _ in range(3):                     # 처음 검사 + 고친 질문 재검사 최대 2번
            try:
                r = llm.chat_json(system, f"{info}\n평가 의도: {d.get('intent') or '-'}\n질문: {text}", PHIL_SCHEMA, timeout=60, temperature=0)
            except llm.LLMError:
                stats["error"] += 1
                ok = True                      # 검수 모델 실패 → 앞 단계 규칙을 통과한 질문 그대로
                break
            if r["pass"] and all(x >= 4 for x in r["scores"][:6] or [0]):
                ok = True
                break
            nq = (r.get("revised_question") or "").strip()
            if not nq or nq == text or not _rule_ok(d, nq, s["snapshot"], s.get("forbid_terms") or ()) or QR.resume_issue(nq, job.get("company_name"), _prev_names(d)):
                break                          # 2026-11-20: 고친 문장도 질문 문장 규칙(QST-20)을 지켜야 받는다
            text = nq
        if ok:
            stats["revised" if text != d["text"] else "pass"] += 1
            d["text"] = text
        else:
            stats["reverted"] += 1
            if Q.one_sentence(d["orig"]) or not (Q.one_sentence(d["text"]) and _rule_ok(d, d["text"], s["snapshot"], s.get("forbid_terms") or ())):
                d["text"] = d["orig"]
                if d["category"] == "resume" and d.get("gen_method") == "llm":
                    d["gen_method"] = "template"
            # else: 2026-11-12 RAG 평가 — 원문이 두 문장(번역 문항 등)이면 원문으로 되돌리지 않고,
            #       규칙(길이·차단 목록·근거)과 한 문장을 통과한 A2 초안을 그대로 둔다
        d["philosophy"] = "pass" if ok else "reverted"
        drafts.append(d)
    return {"drafts": drafts, "log": s["log"] + ["evaluate_philosophy: " + " · ".join(f"{k} {v}" for k, v in stats.items())]}


ANS_SCHEMA = {"type": "object", "properties": {"answerable": {"type": "boolean"}, "asks": {"type": "array", "items": {"type": "string"}},
              "revised_question": {"type": "string"}}, "required": ["answerable", "asks", "revised_question"]}


def evaluate_answerability(s: GenState) -> GenState:
    """A7 답변 가능성(prompts/evaluate/answerability.md): 규칙 _too_broad를 먼저 보고, 넓을 때만 모델로 좁힌다.
    좁힌 질문도 넓거나 규칙 위반이면 첫 요구 과제만 남긴다(기존 fit_scope와 같은 대체 경로)."""
    secs, drafts, n = config.QUESTION_SECONDS, [], 0
    for d in s["drafts"]:
        d = dict(d)
        if s["use_llm"] and not _is_a17(d) and Q._too_broad(d["text"], secs):   # A17은 다시 쓰지 않는다(2026-11-20, A6와 같은 이유)
            n += 1
            nq = ""
            try:
                r = llm.chat_json(prompts.load("evaluate/answerability", seconds=secs, intent=d.get("intent") or ", ".join(d["eval_items"])),
                                  f"질문: {d['text']}", ANS_SCHEMA, timeout=60, temperature=0)
                nq = (r.get("revised_question") or "").strip()
            except llm.LLMError:
                pass
            if not nq or Q._too_broad(nq, secs) or not _rule_ok(d, nq, s["snapshot"], s.get("forbid_terms") or ()):
                nq = re.split(r"(?<=[?요])\s", d["text"])[0]
            d["text"] = nq
        drafts.append(d)
    return {"drafts": drafts, "log": s["log"] + [f"evaluate_answerability: 좁힘 {n}"]}


GUARD_SCHEMA = {"type": "object", "properties": {"leak": {"type": "boolean"},
                "kinds": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 6}}}, "required": ["leak", "kinds"]}


def _drop_rag(d):
    d.pop("company_facts", None)
    d.pop("doc_refs", None)
    return d


def guard_disclosure(s: GenState) -> GenState:
    """7-B A16 (ADLC v0.7 6.13.7 ④ · 6.14.2): 마지막 누출 관문. A6·A7 수정까지 반영된 최종 문장을 본다.
    대상 = 사내문서 근거(doc_refs)가 남은 기업 문항. 규칙이 1차:
      ⓐ 질문·company_facts에 idoc:forbid 용어 0건 · 질문·company_facts에 마스킹 표식·가명 0건(mask_marker) ⓑ company_facts ⊂ (공고 + 주입 조각 본문)
      ⓒ doc_refs ⊂ 실제 주입한 조각 ID ⓓ BLOCKLIST·영문·숫자 토큰(은행 원문 + 공고 + 조각 본문)
    규칙 통과 + 모델 검수(QGRAPH_REVIEW)면 보조 프롬프트 evaluate/internal_doc_guard.md. 모델 실패 → 규칙 결과만(6.14.2).
    하나라도 실패 → 그 문항만 은행 원문으로. guard_report에는 사유 종류·건수만(원문 금지, B-08)."""
    from . import internal_forbid
    job, terms = s["job"], s.get("forbid_terms") or []
    bodies = [h["text"] for h in s.get("internal_hits") or []]
    allowed = re.sub(r"\s+", "", " ".join([job.get("company_intro") or "", job.get("duties") or "", job.get("requirements") or ""] + bodies))
    injected = set(s.get("internal_refs") or [])
    rep = {"checked": 0, "blocked": 0, "reasons": {}, "model": "skipped"}
    drafts = []
    for d in s.get("drafts") or []:
        d = dict(d)
        if not d.get("doc_refs"):
            drafts.append(d)
            continue
        iq = bool(d.get("iq_source"))                     # A17 질문: text == orig가 정상이라 아래 지름길을 타지 않는다
        # 이력서 문항만 예외(2026-11-18): A17 질문을 참고만 하므로 text == orig는 "정상"이 아니라
        # "A6이 템플릿으로 되돌렸다"는 뜻이다(:611) — 그 문장에는 사내문서 내용이 없어 집계에서 뺀다.
        if (not iq or d["category"] == "resume") and d["text"] == d["orig"]:
            drafts.append(_drop_rag(d))
            continue
        rep["checked"] += 1
        why = []
        if not s.get("forbid_ready") or not terms:
            why.append("forbid_unavailable")
        elif internal_forbid.find_terms(d["text"], terms) or any(internal_forbid.find_terms(f, terms) for f in d.get("company_facts") or []):
            why.append("forbid_term")
        if internal_forbid.masked_hits(d["text"]) or any(internal_forbid.masked_hits(f) for f in d.get("company_facts") or []):
            why.append("mask_marker")                   # 마스킹 표식([금액] 등)·가명(고객사A…)·사본 ID → 원문(2026-11-13 사용자 결정)
        if Q.asks_interview_method(d["text"]):         # 면접 방식·평가 기준·질문 설계를 묻는 질문 → 원문(2026-11-13 사용자 결정)
            why.append("interview_meta")
        if not iq:
            # A17 질문의 근거는 **자기 조각**이고 A18이 이미 대조했다. 이 면접에 주입된 조각(injected)과는
            # 다를 수 있으므로 아래 두 검사는 다시 쓴 문항(A15·Dual)에만 적용한다(6.19.5).
            if not all(re.sub(r"\s+", "", f) in allowed for f in d.get("company_facts") or []):
                why.append("facts_unmatched")
            if not set(d["doc_refs"]) <= injected:
                why.append("doc_ref_invalid")
        # 직무 Dual RAG 문항(6.18.3)과 이력서 문항(2026-11-18)은 본문이 이력서라 이력서 토큰을 허용한다
        snap = (s.get("snapshot") or {}) if d["category"] in ("job", "resume") else {}
        # ref_text = 이력서 문항이 참고한 A17 질문 원문(A18 검증을 통과한 문장) — 거기서 온 용어를 지어낸 것으로 보지 않는다
        base = " ".join([d["orig"], d.get("ref_text") or ""] + ((d.get("company_facts") or []) if iq else []) + bodies)
        if Q.BLOCKLIST.search(d["text"]) or Q._bad_tokens(d["text"], base, job, snap):
            why.append("rule")
        if not why and s.get("use_llm") and REVIEW:
            try:
                system = prompts.load("evaluate/internal_doc_guard", company_name=job.get("company_name") or "",
                                      internal_context=s.get("internal_context") or "")
                r = llm.chat_json(system, f"질문: {d['text']}\ncompany_facts: {d.get('company_facts') or []}", GUARD_SCHEMA,
                                  timeout=60, temperature=0)
                rep["model"] = "ok"
                if r.get("leak"):
                    why.append("model_flag")
            except llm.LLMError:
                rep["model"] = "error"                    # 규칙이 1차 — 규칙 결과만으로 판단
        if why:
            rep["blocked"] += 1
            for w in why:
                rep["reasons"][w] = rep["reasons"].get(w, 0) + 1
            d["text"] = d["orig"]
            d["guard"] = "blocked"
            if iq and d["category"] != "resume":
                # A17 질문이 관문에 걸렸다 = 그 질문 자체를 쓰면 안 된다. 되돌릴 은행 원문이 없으므로
                # 문항을 버리고 백업 층(Redis·DB 은행)에서 같은 카테고리 문항으로 채운다(6.19.5).
                d["drop"] = True
            # 이력서 문항은 되돌릴 자리가 있다 — orig가 이력서 항목으로 만든 템플릿 질문이고(RSQ 기본 대체 경로),
            # 은행·DB에는 resume 카테고리 문항이 없다. 그래서 교체하지 않고 템플릿으로 남긴다(문항 수 유지).
            _drop_rag(d)
        else:
            d["guard"] = "pass"
        drafts.append(d)
    msg = f"guard_disclosure: 검사 {rep['checked']} · 차단 {rep['blocked']}"
    if rep["reasons"]:
        msg += " (" + ", ".join(f"{k} {v}" for k, v in sorted(rep["reasons"].items())) + ")"
    return {"drafts": drafts, "guard_report": rep, "log": s["log"] + [msg]}


def _backup_question(cat, used, snapshot):
    """7-B가 사내문서 질문(A17)을 차단했을 때 그 자리를 채운다 — 백업 층만 쓴다(모델 호출 0회, 6.19.5).
    인성은 `character_assessment` md → 없으면 DB 은행, 나머지는 DB `question_bank`. 비어 있으면 None(문항 수가 줄어든다)."""
    got = Q.pick_character(1, used, snapshot) if cat == "personality" else Q.pick_bank(cat, 1, used)
    if not got:
        return None
    b = got[0]
    return {"category": cat, "eval_items": Q.EVAL_ITEMS[cat], "text": b["text"], "orig": b["text"],
            **_bank_fields(b), "intent": b.get("intent") or b.get("subcategory") or "", "replaced": "guard_blocked"}


def finalize(s: GenState) -> GenState:
    """코드: 순서 고정(Q.PLAN = 인성 2 → 이력서 3 → 직무 2 → 기업 1). 저장 형식은 기존 build_set과 같다.
    사내문서 근거가 관문을 통과한 문항은 gen_method 뒤에 `|internal:<doc_id#sec>,…`(조각 ID만, ADLC 6.14.3 — 스키마 변경 없음).
    7-B가 차단한 사내문서 질문(`drop`)은 백업 층 문항으로 바꿔 8문항을 유지한다(6.19.5)."""
    order = {c: i for i, (c, _) in enumerate(Q.PLAN)}
    keep = ("category", "eval_items", "text", "bank_id", "gen_method", "resume_ref")
    final, used, replaced = [], set(), 0
    for d in sorted(s["drafts"], key=lambda d: order[d["category"]]):
        if d.get("drop"):
            rep = _backup_question(d["category"], used, s.get("snapshot") or {})
            if rep:
                replaced += 1
                d = rep
        if d.get("dup_group"):
            used.add(d["dup_group"])
        q = {k: d.get(k) for k in keep if k in d}
        q["text"] = QR.end_punct(q.get("text"))     # 끝 문장부호 맞춤(2026-11-20)
        if d.get("doc_refs") and d.get("guard") == "pass":
            q["gen_method"] = f"{q.get('gen_method') or 'db'}|internal:" + ",".join(d["doc_refs"])
        final.append(q)
    return {"final_questions": final,
            "log": s["log"] + [f"finalize: {len(final)}문항" + (f" · 백업 교체 {replaced}" if replaced else "")]}


def _build_generation():
    g = StateGraph(GenState)
    for name, fn in (("start", start), ("extract_keywords", extract_keywords), ("evaluate_trust", evaluate_trust),
                     ("retrieve", retrieve), ("retrieve_internal", retrieve_internal), ("generate", generate),
                     ("evaluate_philosophy", evaluate_philosophy), ("evaluate_answerability", evaluate_answerability),
                     ("guard_disclosure", guard_disclosure), ("finalize", finalize)):
        g.add_node(name, fn)
    g.add_edge(START, "start")
    for a, b in (("start", "extract_keywords"), ("extract_keywords", "evaluate_trust"), ("evaluate_trust", "retrieve"),
                 ("retrieve", "retrieve_internal"), ("retrieve_internal", "generate"),
                 ("generate", "evaluate_philosophy"), ("evaluate_philosophy", "evaluate_answerability"),
                 ("evaluate_answerability", "guard_disclosure"), ("guard_disclosure", "finalize"), ("finalize", END)):
        g.add_edge(a, b)
    return g.compile()


GENERATION = _build_generation()


def generate_set(application, use_llm=True):
    """그래프 1-A 실행 → 10문항(기존 build_set과 같은 형식) + 처리 기록."""
    out = GENERATION.invoke({"application": application, "use_llm": use_llm},
                            {"run_name": "question_graph", "tags": ["graph1", "generate"], "metadata": {"application_id": application["id"]}})
    return out["final_questions"], {"log": out.get("log", []), "keyword_report": out.get("keyword_report", {}),
                                    "internal_refs": out.get("internal_refs", []), "guard_report": out.get("guard_report", {}),
                                    "dual_report": out.get("dual_report", {}), "mq_report": out.get("mq_report", {}),
                                    "backup_report": out.get("backup_report", {})}


# ======================= B. 면접 루프 (답변 1개마다 한 걸음) =======================
# ── A3 꼬리질문: 판단 상수·프롬프트·판단 함수는 전용 에이전트(agents/followup_agent.py)에 있다.
# 여기서는 **같은 이름으로 다시 내보내기만** 한다 — tools/eval_followup.py · sim_applicants.py ·
# test_graph1b_loop.py가 `question_graph.PRE_SYSTEM` 같은 이름(식별까지)을 그대로 쓰기 때문이다.
from . import followup_agent as FA  # noqa: E402

PRE_SCHEMA = FA.PRE_SCHEMA
PRE_SCHEMA_IDEAL = FA.PRE_SCHEMA_IDEAL
PRE_SYSTEM = FA.PRE_SYSTEM
PRE_SYSTEM_IDEAL = FA.PRE_SYSTEM_IDEAL
SPECIFICITY_MAX = FA.SPECIFICITY_MAX
REF_TIMEOUT = 0.3            # 실시간 경로: 은행 문항 조회(Redis) 제한 시간(초). 넘으면 ②③ 없이 판단
_RESUME_KW = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]*[A-Za-z0-9+#]")
_KW_SPLIT = re.compile(r"[,;\n]+")


def _norm(s):
    return re.sub(r"\s+", "", (s or "").lower())


def keyword_groups(raw):
    """은행 expected_keywords 'RAG|검색 증강, Recall@k' → [['RAG','검색 증강'], ['Recall@k']] (| = 동의어)."""
    out = []
    for part in _KW_SPLIT.split(raw or ""):
        g = [w.strip() for w in part.split("|") if w.strip()]
        if g:
            out.append(g)
    return out


def keyword_hits(groups, answer):
    """판단 ② 코드 대조: 답변에 나온 키워드 묶음의 대표어 목록(대소문자·띄어쓰기 무시, 동의어 포함)."""
    a = _norm(answer)
    return [g[0] for g in groups if any(_norm(w) and _norm(w) in a for w in g)]


def _resume_keywords(q):
    """이력서 질문: 근거 항목 인용의 영문 기술 토큰(2자 이상)을 키워드로 쓴다. 숫자·날짜는 쓰지 않는다."""
    quote = (q.get("resume_ref") or {}).get("quote") or ""
    seen, out = set(), []
    for t in _RESUME_KW.findall(quote):
        if len(t) >= 2 and t.lower() not in seen:
            seen.add(t.lower())
            out.append([t])
    return out[:8]


def bank_reference(q):
    """판단 ②③ 재료: {'keywords': [[동의어…]…], 'ideal': 모범 답변 요지}. 없거나 조회 실패면 빈 값(그 판단만 건너뜀).

    출처마다 꺼내는 곳이 다르다(2026-11-18 사내문서 질문 추가):
      · 사내문서 질문 `internal_q:<질문 ID>` → Chroma 질문 컬렉션 메타. A17이 생성 때 `model_answer`·
        `expected_keywords`를 **528개 전부** 채워 두는데 여기서 읽지 않아, 사내문서 질문이 주 출처가 된 뒤
        판단 ②(기대 키워드)·③(구체성)이 돌지 않고 있었다. ID로 바로 꺼내므로 임베딩·모델 호출은 0회.
      · Redis 은행 문항 `redis:<문서 ID>` → Redis 해시(기존 그대로).
      · 이력서 질문 → 근거 항목 토큰.
    실시간 경로이므로 어느 쪽이든 실패는 조용히 빈 값으로 둔다(꼬리질문 판단만 건너뛴다)."""
    ref = {"keywords": [], "ideal": ""}
    gm = q.get("gen_method") or ""
    # FOLLOWUP_INTERNAL_REF(기본 0): B-07 힌트 금지 생략 위험 측정 전까지 이 분기를 끈다(2026-11-18)
    if gm.startswith("internal_q:") and config.FOLLOWUP_INTERNAL_REF:
        try:
            from .. import internal_store
            qid = gm[len("internal_q:"):].split("|")[0]
            got = internal_store.question_collection(q.get("category") or "").get(ids=[qid], include=["metadatas"])
            m = (got.get("metadatas") or [None])[0] or {}
            ref["keywords"] = keyword_groups(m.get("expected_keywords") or "")
            ref["ideal"] = (m.get("model_answer") or "").strip()
        except Exception:  # noqa: BLE001 — 컬렉션 없음·ID 없음·Chroma 오류 → 그 판단만 건너뛴다
            pass
    elif gm.startswith("redis:"):
        try:
            import redis
            r = redis.Redis.from_url(config.REDIS_URL, socket_timeout=REF_TIMEOUT, socket_connect_timeout=REF_TIMEOUT)
            kw, ideal = r.hmget("qb:" + gm[len("redis:"):].split("|")[0], "expected_keywords", "model_answer")
            ref["keywords"] = keyword_groups((kw or b"").decode("utf-8", "ignore"))
            ref["ideal"] = (ideal or b"").decode("utf-8", "ignore").strip()
        except Exception:
            pass
    if not ref["keywords"] and q.get("category") == "resume":
        ref["keywords"] = _resume_keywords(q)
    return ref


class StepState(TypedDict, total=False):
    attempt_id: int
    att: dict
    q: dict
    text: str
    source: str | None
    proposal: dict | None          # judge_followup 결과 {question, reason}
    keyword_hits: list | None      # 판단 ② 답변에 나온 기대 키워드(없으면 None = 판단 안 함)
    specificity: int | None        # 판단 ③ 모범 답변 대비 구체성(없으면 None)
    followup_id: int | None
    outcome: str                   # next | coding | finished | ended
    qa_pairs: list                 # 루프 동안 임시로 두는 질문–답변 쌍(기준은 DB attempt_questions)


def current_question(attempt_id):
    return db.one("""SELECT * FROM attempt_questions WHERE attempt_id=%s AND answer_status='pending'
                     ORDER BY seq, parent_id NULLS FIRST, id LIMIT 1""", attempt_id)


def _load_attempt(attempt_id):
    return db.one("""SELECT a.*, ap.user_id, ap.job_id FROM attempts a JOIN applications ap ON ap.id=a.application_id
                     WHERE a.id=%s""", attempt_id)


def ask_question(s: StepState) -> StepState:
    """노드 9 (ADLC 6.1-B): 현재 질문을 내보내고 답변을 기다린다(LangGraph interrupt).
    답변 제출 API가 Command(resume={question_id, text, source})로 다시 진행시킨다.
    재개 때 이 노드는 처음부터 다시 돌므로 interrupt 앞은 DB 읽기만 한다(부작용 없음).
    응시가 면접 단계가 아니거나 남은 질문이 없으면 끝(END)."""
    att = _load_attempt(s["attempt_id"])
    q = current_question(s["attempt_id"]) if att and att["status"] == "interview" else None
    if not q:
        return {"outcome": "ended", "q": None}
    got = interrupt({"question_id": q["id"], "seq": q["seq"], "time_limit": q["time_limit"],
                     "is_followup": q["parent_id"] is not None})
    if (got or {}).get("question_id") != q["id"]:
        raise StaleLoop(f"waiting {q['id']}, got {(got or {}).get('question_id')}")
    return {"att": att, "q": q, "text": got.get("text") or "", "source": got.get("source"),
            "proposal": None, "keyword_hits": None, "specificity": None, "followup_id": None}


def record_answer(s: StepState) -> StepState:
    db.run("UPDATE attempt_questions SET answer_text=%s, answer_status=%s, answer_source=%s, answered_at=now() WHERE id=%s",
           s["text"] or None, "answered" if s["text"] else "no_answer", s["source"], s["q"]["id"])
    return {}


def judge_detail(att, q, answer):
    """A3 꼬리질문 판단 — 전용 에이전트 `followup_agent.judge_detail`에 위임한다(2026-11-17 분리).
    반환 {'followup': (꼬리질문, 사유) | None, 'keyword_hits': list | None, 'specificity': int | None}.
    판단 순서·상수·프롬프트는 그 모듈에 있고, 은행 참고 조회(bank_reference·keyword_hits)는 이 모듈이 그대로 제공한다
    (평가 도구가 `question_graph.bank_reference`를 갈아끼워 쓰므로 그 경로를 유지한다)."""
    return FA.judge_detail(att, q, answer)


def judge(att, q, answer):
    """A3 노드 계약(ADLC 6.1-B): judge(att, q, answer) → (꼬리질문, 사유) | None."""
    return FA.judge(att, q, answer)


def judge_followup(s: StepState) -> StepState:
    d = judge_detail(s["att"], s["q"], s["text"]) if s["text"] else {"followup": None, "keyword_hits": None, "specificity": None}
    fu = d["followup"]
    q = s["q"]
    pair = {"question_id": q["id"], "seq": q["seq"], "is_followup": q["parent_id"] is not None, "answered": bool(s["text"]),
            "keyword_hits": d["keyword_hits"], "specificity": d["specificity"], "followup_reason": fu[1] if fu else None}
    return {"proposal": {"question": fu[0], "reason": fu[1]} if fu else None, "keyword_hits": d["keyword_hits"],
            "specificity": d["specificity"], "qa_pairs": (s.get("qa_pairs") or []) + [pair]}


def make_followup(s: StepState) -> StepState:
    """꼬리질문을 같은 seq 자리에 끼워 넣고 사용 수를 늘린다(질문당 1개·전체 4개는 judge가 확인, 여기서 DB로 한 번 더 확인)."""
    from .. import speech
    att, q, p = s["att"], s["q"], s["proposal"]
    if db.one("SELECT 1 AS x FROM attempt_questions WHERE parent_id=%s", q["id"]) or \
       (db.one("SELECT followups_used FROM attempts WHERE id=%s", att["id"]) or {}).get("followups_used", 0) >= config.MAX_FOLLOWUPS:
        return {"followup_id": None}
    row = db.one("""INSERT INTO attempt_questions (attempt_id, seq, parent_id, category, eval_items, text, gen_method, time_limit)
                    VALUES (%s,%s,%s,'followup',%s,%s,'llm',%s) RETURNING id""",
                 att["id"], q["seq"], q["id"], q["eval_items"], p["question"], config.FOLLOWUP_SECONDS)
    pre = {"followup_reason": p["reason"]}
    if s.get("keyword_hits") is not None:
        pre["keyword_hits"] = s["keyword_hits"]
    if s.get("specificity") is not None:
        pre["specificity"] = s["specificity"]
    db.run("UPDATE attempt_questions SET prescore=%s WHERE id=%s", db.J(pre), q["id"])
    db.run("UPDATE attempts SET followups_used=followups_used+1 WHERE id=%s", att["id"])
    speech.prewarm([p["question"]])
    return {"followup_id": row["id"] if row else None}


def next_question(s: StepState) -> StepState:
    """남은 질문이 없으면 코딩테스트로 넘기거나 응시를 끝낸다(상태 전이는 코드)."""
    from .. import coding, scheduler
    aid = s["att"]["id"]
    if current_question(aid):
        return {"outcome": "next"}
    if s["att"]["criteria_snapshot"]["coding_enabled"]:
        coding.assign_problems(aid)
        db.run(f"""UPDATE attempts SET status='coding', phase='coding', coding_deadline=now() + interval '{config.CODING_MINUTES} minutes'
                   WHERE id=%s""", aid)
        return {"outcome": "coding"}
    scheduler.finish_attempt(aid, "completed", "interview_done")
    return {"outcome": "finished"}


def _build_step():
    """체크포인터 없는 한 걸음 그래프(2026-11-09 방식). 루프가 실패했고 답변이 아직 저장되지 않았을 때의 대체 경로."""
    g = StateGraph(StepState)
    for name, fn in (("record_answer", record_answer), ("judge_followup", judge_followup),
                     ("make_followup", make_followup), ("next_question", next_question)):
        g.add_node(name, fn)
    g.add_edge(START, "record_answer")
    g.add_edge("record_answer", "judge_followup")
    g.add_conditional_edges("judge_followup", lambda s: "make" if s.get("proposal") else "next",
                            {"make": "make_followup", "next": "next_question"})
    g.add_edge("make_followup", "next_question")
    g.add_edge("next_question", END)
    return g.compile()


def _build_loop(checkpointer):
    """ADLC 6.1-B 면접 루프: ask_question(대기) → record_answer → judge_followup ─(꼬리질문)→ make_followup → ask_question
                                                                      └(없음)→ next_question ─(남음)→ ask_question / (끝)→ END."""
    g = StateGraph(StepState)
    for name, fn in (("ask_question", ask_question), ("record_answer", record_answer), ("judge_followup", judge_followup),
                     ("make_followup", make_followup), ("next_question", next_question)):
        g.add_node(name, fn)
    g.add_edge(START, "ask_question")
    g.add_conditional_edges("ask_question", lambda s: "end" if s.get("outcome") == "ended" else "record",
                            {"end": END, "record": "record_answer"})
    g.add_edge("record_answer", "judge_followup")
    g.add_conditional_edges("judge_followup", lambda s: "make" if s.get("proposal") else "next",
                            {"make": "make_followup", "next": "next_question"})
    g.add_edge("make_followup", "ask_question")
    g.add_conditional_edges("next_question", lambda s: "ask" if s.get("outcome") == "next" else "end",
                            {"ask": "ask_question", "end": END})
    return g.compile(checkpointer=checkpointer)


class StaleLoop(Exception):
    """체크포인트가 기다리는 질문과 DB의 현재 질문이 다름 → 체크포인트를 버리고 DB로 다시 만든다."""


# 임시 상태 보관 = 프로세스 메모리 체크포인터(thread_id = attempt-<id>). 기준은 계속 DB(attempt_questions·attempts)이고
# 체크포인트가 없거나(재시작) 어긋나면 DB에서 다시 만든다(장애 이어가기 RES-01과 같은 방식). 응시가 끝나면 지운다.
# uvicorn 워커 1개 기준. 워커를 늘리면 워커마다 따로 들고 있다가 어긋날 때 DB로 다시 만든다(정확성은 유지, 재사용률만 떨어짐).
CHECKPOINTER = InMemorySaver()
INTERVIEW_STEP = _build_step()
INTERVIEW_LOOP = _build_loop(CHECKPOINTER)
LOOP_IDLE_SECONDS = 3 * 3600     # 이만큼 안 쓴 체크포인트는 버린다(탭 닫기·취소로 END에 못 간 응시)
_locks_guard = threading.Lock()
_locks: dict = {}
_last_used: dict = {}


def _thread(aid):
    return f"attempt-{aid}"


def _lock(aid):
    with _locks_guard:
        return _locks.setdefault(aid, threading.Lock())


def discard(aid):
    """응시의 루프 체크포인트를 지운다(없어도 됨)."""
    try:
        CHECKPOINTER.delete_thread(_thread(aid))
    except Exception:
        pass
    with _locks_guard:
        _last_used.pop(aid, None)


def _prune(now):
    with _locks_guard:
        old = [a for a, t in _last_used.items() if now - t > LOOP_IDLE_SECONDS]
    for a in old:
        discard(a)
        with _locks_guard:
            _locks.pop(a, None)


def waiting_on(cfg):
    """체크포인트가 ask_question에서 기다리는 질문 ID(없으면 None)."""
    snap = INTERVIEW_LOOP.get_state(cfg)
    if snap.next != ("ask_question",):
        return None
    for t in snap.tasks:
        for it in (t.interrupts or ()):
            v = getattr(it, "value", None)
            if isinstance(v, dict) and "question_id" in v:
                return v["question_id"]
    return None


def _recover(att, q, text, source):
    """루프가 실패했을 때: 답변이 아직 저장 전이면 한 걸음 그래프로 처음부터, 저장됐으면 남은 상태 전이만 한다."""
    row = db.one("SELECT answer_status FROM attempt_questions WHERE id=%s", q["id"])
    if row and row["answer_status"] == "pending":
        return INTERVIEW_STEP.invoke({"att": att, "q": q, "text": text or "", "source": source},
                                     {"run_name": "interview_step", "tags": ["graph1", "interview", "fallback"],
                                      "metadata": {"attempt_id": att["id"]}})
    cur = _load_attempt(att["id"])
    if cur and cur["status"] == "interview" and not current_question(att["id"]):
        return next_question({"att": cur})
    return {"outcome": "next"}


def answer_step(att, q, text, source):
    """답변 1개 → 루프 한 걸음(routes_applicant._save_answer가 부름, 반환값은 쓰지 않음).
    체크포인트가 이 질문에서 기다리고 있으면 그대로 재개하고, 아니면 DB로 루프를 다시 만든 뒤 재개한다."""
    aid = att["id"]
    cfg = {"configurable": {"thread_id": _thread(aid)}, "run_name": "interview_loop",
           "tags": ["graph1", "interview"], "metadata": {"attempt_id": aid}}
    now = time.monotonic()
    _prune(now)
    with _lock(aid):
        with _locks_guard:
            _last_used[aid] = now
        try:
            if waiting_on(cfg) != q["id"]:
                discard(aid)
                INTERVIEW_LOOP.invoke({"attempt_id": aid, "qa_pairs": []}, cfg)      # DB로 다시 만들기 → ask_question에서 멈춤
                if waiting_on(cfg) != q["id"]:
                    raise StaleLoop(f"rebuilt loop not waiting on {q['id']}")
            out = INTERVIEW_LOOP.invoke(Command(resume={"question_id": q["id"], "text": text or "", "source": source}), cfg)
        except Exception:
            log.exception("interview loop failed (attempt %s) → fallback", aid)
            discard(aid)
            return _recover(att, q, text, source)
        if not INTERVIEW_LOOP.get_state(cfg).next:      # END(코딩 전환·응시 종료·면접 단계 아님)
            discard(aid)
        return out
