"""사내문서 RAG 검색 에이전트 A14 — ADLC v0.7 6.13.6.

기본 질문 8개 중 **기업 문항 1개**의 근거를 찾는다. 모델 판단은 없고 벡터 검색 + 규칙 재순위뿐이다.

질의 벡터는 그래프 1-A `retrieve`가 만든 것을 **재사용**한다 → 임베딩 추가 호출 0회.
(질문 은행 Redis와 사내문서 Chroma가 같은 로컬 qwen3-embedding:0.6b·1024차원이라 가능하다.
 2026-11-11 사용자 결정 — CLAUDE.md 참고)

**doc_type 화이트리스트를 하드 필터로 쓰지 않는다** (2026-11-11 사용자 지시):
노이즈 문서까지 후보에 넣고 유사도·재순위로 가리는 것이 RAG의 본래 동작이고,
미리 걸러내면 검색 품질을 측정할 수 없어 지표(M-4~6)·골든셋(G-05·G-06)이 의미를 잃는다.
doc_type은 재순위 **가산점**으로만 반영한다. 안전(disclosure·status)은 그대로 하드 필터다.
"""
import re

from .. import internal_store
from . import internal_qcat

K = 12                     # 1차 후보 조각 수
K_CAT = 28                 # 질문 카테고리(q_cat)로 거를 때의 1차 후보 수 — 앱에서 거르므로 넉넉히 가져온다(6.19.3 ㉯)
MIN_SIM = 0.55             # 하한 (실측 후 확정)
MAX_PER_DOC = 2            # 문서당 최대 조각
TOP_N = 4                  # 최종 조각 수
MAX_CHARS = 2000           # 컨텍스트 합계 상한
# 하드 필터 status (2026-11-13 사용자 결정: resolved 추가 — 해결된 장애 회고 공개판 등. open은 계속 제외)
ALLOWED_STATUS = ("active", "done", "resolved")

# 재순위 가산점 (하드 필터 아님)
PREFERRED_TYPES = {"jd": 0.04, "talent": 0.04, "project": 0.04,
                   "incident": 0.03, "open_challenge": 0.03, "tech_card": 0.02}
# 재순위 **감점** (2026-11-23 리드 승인 — '관리 문서 제외'가 아니라 '감점'). 위 가산점과 같은 구조이고 부호만 반대다.
# 왜: 기업 문항의 근거 조각이 회계·결재·예산·공지 같은 관리 문서에서 오면 제품·프로젝트 맥락이 없어 질문이
# 추상적으로 뭉개진다(F13 사용자 지적). 질문 쪽은 `question_graph.DEMOTED_TYPES`가 같은 일을 하고 있었고,
# **조각 쪽(A14 근거 검색)에는 그 장치가 없었다** → 같은 키·같은 폭으로 맞춘다.
# **통과 판정은 뒤집지 않는다**: 유사도 하한(MIN_SIM)은 `_ranked`에서 재순위 **전**에 걸리므로(:80) 감점은
# 순서(= `_assemble`의 TOP_N 선택)에만 영향한다. 폭도 가산점보다 작게(0.03/0.01) 둔다.
DEMOTED_TYPES = {
    "approval": 0.03, "approval_authority": 0.03, "settlement": 0.03, "closing": 0.03, "budget": 0.03,
    "costing": 0.03, "revenue": 0.03, "receivable": 0.03, "foreign_tx": 0.03, "notice": 0.03,
    "manpower_request": 0.03,
    "bid_process": 0.01, "bid_decision": 0.01, "pricing_model": 0.01, "proposal_guide": 0.01,
    "contract_guide": 0.01, "sales_plan": 0.01, "planning": 0.01, "asset_registry": 0.01,
    "decision_guide": 0.01, "discovery_guide": 0.01,
}

CODE_RE = re.compile(r"```.*?```", re.S)


def _rerank(hits, role_code=None, level_band=None, q_cat=None):
    """점수 = sim + dept 일치 + doc_type 우선순위 − doc_type 감점 + q_cat 일치 + ct_band 일치 − 같은 문서 중복."""
    seen = {}
    out = []
    for h in hits:
        m = h["metadata"]
        score = h["sim"]
        if role_code and m.get(f"dept_{role_code}"):
            score += 0.05
        score += PREFERRED_TYPES.get(m.get("doc_type", ""), 0.0)
        score -= DEMOTED_TYPES.get(m.get("doc_type", ""), 0.0)   # 관리 문서는 뒤로(2026-11-23, 통과 판정은 안 바뀐다)
        if q_cat and q_cat in internal_qcat.qcat_of(m):
            score += 0.05                     # 하드 필터 아님(6.13.6 ③) — 거르기는 _ranked가 한다
        if level_band and m.get("ct_band") == level_band:
            score += 0.02
        n = seen.get(m.get("doc_id"), 0)
        if n:
            score -= 0.06 * n
        seen[m["doc_id"]] = n + 1
        out.append({**h, "score": score})
    return sorted(out, key=lambda x: -x["score"])


def _forbidden(body, meta):
    """④ 금지 용어 1차 검사 — 공개판에 남아 있던 잔여 표기 대비. 걸리면 그 조각을 버린다."""
    terms = [t for t in (meta.get("forbid_terms") or "").split("|") if t]
    return any(t in body for t in terms)


def _strip_code(body, meta):
    """has_code 조각은 코드 원문을 떼고 설명 문단만 쓴다."""
    return CODE_RE.sub("[코드 생략]", body) if meta.get("has_code") else body


def _where():
    return {"$and": [{"disclosure": {"$eq": "public"}},
                     {"status": {"$in": list(ALLOWED_STATUS)}}]}


def _ranked(vector, role_code=None, level_band=None, k=K, min_sim=MIN_SIM, forbid_terms=None, q_cat=None):
    """벡터 1개 → 안전 조건(하한·금지 용어)을 통과한 조각을 규칙 재순위한 목록. Chroma 오류는 그대로 올린다.

    q_cat(2026-11-17, 6.19.3): 질문 카테고리로 거른다. **Chroma `where`에 넣지 않고 앱에서 거른다** —
    `where`는 870 ms vs 19 ms로 비싸고 실제로 거르는 조각도 없었다(QO-02·QO-14). 대신 후보 수를 K_CAT로 늘린다.
    """
    hits = internal_store.query_public(vector, k=max(k, K_CAT) if q_cat else k, where=_where())
    hits = [h for h in hits if h["sim"] >= min_sim]
    if q_cat:
        hits = [h for h in hits if internal_qcat.matches(h["metadata"], q_cat)]
    hits = [h for h in hits if not _forbidden(h["document"], h["metadata"])]
    if forbid_terms:
        from . import internal_forbid
        hits = [h for h in hits if not internal_forbid.find_terms(h["document"], forbid_terms)]
    return _rerank(hits, role_code, level_band, q_cat)


def _assemble(hits):
    """순위대로 솎기(문서당 2조각 · 상위 4조각 · 2,000자) → 컨텍스트 조립."""
    picked, per_doc, total = [], {}, 0
    for h in hits:
        m = h["metadata"]
        did = m.get("doc_id")
        if per_doc.get(did, 0) >= MAX_PER_DOC:
            continue
        body = _strip_code(h["document"], m)
        if total + len(body) > MAX_CHARS:
            continue
        per_doc[did] = per_doc.get(did, 0) + 1
        total += len(body)
        picked.append({**h, "body": body})
        if len(picked) >= TOP_N:
            break

    blocks, refs = [], []
    for h in picked:
        m = h["metadata"]
        ref = f"{m['doc_id']}#{m.get('sec', '')}"
        refs.append(ref)
        blocks.append(f"[근거 {ref} · {m.get('doc_title', '')} > {m.get('sec_title', '')}]\n{h['body']}")
    return {"context": "\n\n".join(blocks), "refs": refs, "hits": picked, "ranked": [h["id"] for h in hits]}


def search(vector, role_code=None, level_band=None, k=K, min_sim=MIN_SIM, forbid_terms=None, q_cat=None):
    """반환: {context, refs, hits, ranked}. 근거가 없으면 context="" (호출 쪽이 공고 정보만으로 생성).

    forbid_terms(2026-11-13, 6.13.6 ⑧): 전 문서 금지 용어 사전 `idoc:forbid`(`agents.internal_forbid.load_terms`).
    주면 조각 자신의 forbid_terms에 더해 이 용어가 본문에 있는 조각도 버린다. None이면 기존 동작(평가 도구 호환).
    ranked(2026-11-16): 솎기 전 재순위 목록의 조각 ID — 평가용. 기존 호출 쪽은 쓰지 않는다.
    q_cat(2026-11-17): 질문 카테고리(personality·job·company)로 조각을 좁힌다. None이면 기존 동작."""
    try:
        hits = _ranked(vector, role_code, level_band, k, min_sim, forbid_terms, q_cat)
    except Exception:
        return {"context": "", "refs": [], "hits": [], "ranked": []}      # Chroma 실패 → 사내문서 없이 진행
    return _assemble(hits)


def search_multi(vectors, role_code=None, level_band=None, k=K, min_sim=MIN_SIM, forbid_terms=None, rrf_k=None,
                 q_cat=None):
    """Multi-Query + RRF (2026-11-16, ADLC v0.8 6.18). 벡터마다 `_ranked`(안전 조건 → 규칙 재순위)를 병렬로 돌리고,
    그 순위 목록들을 조각 ID 기준 RRF로 합친 뒤 기존과 같은 솎기·조립을 한다.
    반환: search()와 같은 모양 + mq{queries, ok, failed, per_query, fused}. 모든 목록이 실패하면 RuntimeError(호출 쪽이 단일 쿼리로 대체)."""
    from .. import config
    from . import rag_fusion
    res = rag_fusion.parallel(lambda v: _ranked(v, role_code, level_band, k, min_sim, forbid_terms, q_cat),
                              [(v,) for v in vectors])
    lists = [r for ok, r in res if ok]
    if not lists:
        raise RuntimeError("all queries failed")
    fused = rag_fusion.rrf(lists, key=lambda h: h["id"], k=rrf_k or config.RAG_RRF_K)
    out = _assemble(fused)
    out["mq"] = {"queries": len(vectors), "ok": len(lists), "failed": len(vectors) - len(lists),
                 "per_query": [len(x) for x in lists], "fused": len(fused)}
    return out
