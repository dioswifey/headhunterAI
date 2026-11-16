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

K = 12                     # 1차 후보 조각 수
MIN_SIM = 0.55             # 하한 (실측 후 확정)
MAX_PER_DOC = 2            # 문서당 최대 조각
TOP_N = 4                  # 최종 조각 수
MAX_CHARS = 2000           # 컨텍스트 합계 상한
# 하드 필터 status (2026-11-13 사용자 결정: resolved 추가 — 해결된 장애 회고 공개판 등. open은 계속 제외)
ALLOWED_STATUS = ("active", "done", "resolved")

# 재순위 가산점 (하드 필터 아님)
PREFERRED_TYPES = {"jd": 0.04, "talent": 0.04, "project": 0.04,
                   "incident": 0.03, "open_challenge": 0.03, "tech_card": 0.02}

CODE_RE = re.compile(r"```.*?```", re.S)


def _rerank(hits, role_code=None, level_band=None):
    """점수 = sim + dept 일치 + doc_type 우선순위 + ct_band 일치 − 같은 문서 중복."""
    seen = {}
    out = []
    for h in hits:
        m = h["metadata"]
        score = h["sim"]
        if role_code and m.get(f"dept_{role_code}"):
            score += 0.05
        score += PREFERRED_TYPES.get(m.get("doc_type", ""), 0.0)
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


def _ranked(vector, role_code=None, level_band=None, k=K, min_sim=MIN_SIM, forbid_terms=None):
    """벡터 1개 → 안전 조건(하한·금지 용어)을 통과한 조각을 규칙 재순위한 목록. Chroma 오류는 그대로 올린다."""
    hits = internal_store.query_public(vector, k=k, where=_where())
    hits = [h for h in hits if h["sim"] >= min_sim]
    hits = [h for h in hits if not _forbidden(h["document"], h["metadata"])]
    if forbid_terms:
        from . import internal_forbid
        hits = [h for h in hits if not internal_forbid.find_terms(h["document"], forbid_terms)]
    return _rerank(hits, role_code, level_band)


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


def search(vector, role_code=None, level_band=None, k=K, min_sim=MIN_SIM, forbid_terms=None):
    """반환: {context, refs, hits, ranked}. 근거가 없으면 context="" (호출 쪽이 공고 정보만으로 생성).

    forbid_terms(2026-11-13, 6.13.6 ⑧): 전 문서 금지 용어 사전 `idoc:forbid`(`agents.internal_forbid.load_terms`).
    주면 조각 자신의 forbid_terms에 더해 이 용어가 본문에 있는 조각도 버린다. None이면 기존 동작(평가 도구 호환).
    ranked(2026-11-16): 솎기 전 재순위 목록의 조각 ID — 평가용. 기존 호출 쪽은 쓰지 않는다."""
    try:
        hits = _ranked(vector, role_code, level_band, k, min_sim, forbid_terms)
    except Exception:
        return {"context": "", "refs": [], "hits": [], "ranked": []}      # Chroma 실패 → 사내문서 없이 진행
    return _assemble(hits)


def search_multi(vectors, role_code=None, level_band=None, k=K, min_sim=MIN_SIM, forbid_terms=None, rrf_k=None):
    """Multi-Query + RRF (2026-11-16, ADLC v0.8 6.18). 벡터마다 `_ranked`(안전 조건 → 규칙 재순위)를 병렬로 돌리고,
    그 순위 목록들을 조각 ID 기준 RRF로 합친 뒤 기존과 같은 솎기·조립을 한다.
    반환: search()와 같은 모양 + mq{queries, ok, failed, per_query, fused}. 모든 목록이 실패하면 RuntimeError(호출 쪽이 단일 쿼리로 대체)."""
    from .. import config
    from . import rag_fusion
    res = rag_fusion.parallel(lambda v: _ranked(v, role_code, level_band, k, min_sim, forbid_terms), [(v,) for v in vectors])
    lists = [r for ok, r in res if ok]
    if not lists:
        raise RuntimeError("all queries failed")
    fused = rag_fusion.rrf(lists, key=lambda h: h["id"], k=rrf_k or config.RAG_RRF_K)
    out = _assemble(fused)
    out["mq"] = {"queries": len(vectors), "ok": len(lists), "failed": len(vectors) - len(lists),
                 "per_query": [len(x) for x in lists], "fused": len(fused)}
    return out
