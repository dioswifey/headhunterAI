"""사내문서 벡터 저장소 (Chroma) — ADLC v0.7 2.1 도구 `internal_store`.

Redis(질문 은행)와 역할이 다르다. 여기는 `internal_documents/`의 사내문서 조각을 담고,
기본 질문 8개 중 **기업 문항 1개**의 근거로만 쓴다.

컬렉션을 둘로 **물리 분리**한다(ADLC 6.13.7 ① 1차 관문):
  - internal_docs_public      disclosure=public 문서만. 지원자에게 가는 질문 생성 경로가 유일하게 열 수 있다
  - internal_docs_restricted  그 밖(internal·confidential·abstract). 질문 생성 경로에서 열지 않는다

검색 함수가 컬렉션 이름을 인자로 받지 않는 이유: 호출하는 쪽이 실수로 restricted를 지정할 수 없게.

임베딩 함수는 **지정하지 않는다**. Chroma 기본 임베딩 함수는 외부 모델을 내려받으므로 쓰지 않고,
벡터는 항상 `llm.embed`(로컬 qwen3-embedding:0.6b)가 만든 것만 `embeddings=`로 직접 넣는다.
(ADLC 2.3 외부 임베딩 API·모델 금지 / CLAUDE.md 2026-11-11 사용자 결정)
"""
import os

PUBLIC = "internal_docs_public"
RESTRICTED = "internal_docs_restricted"

# questionbank/vectorDB/chroma — 컨테이너에서는 /questionbank/vectorDB로 마운트돼 있다.
_DEFAULT = "/questionbank/vectorDB" if os.path.isdir("/questionbank/vectorDB") else \
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 "questionbank", "vectorDB")
PERSIST_DIR = os.path.join(os.getenv("VECTORDB_DIR", _DEFAULT), "chroma")

_client = None


def client():
    global _client
    if _client is None:
        import chromadb
        os.makedirs(PERSIST_DIR, exist_ok=True)
        _client = chromadb.PersistentClient(path=PERSIST_DIR)
    return _client


def collection(name):
    """없으면 만든다. 거리 = cosine → 유사도 = 1 - distance (vectorstore.knn과 같은 환산)."""
    return client().get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


def upsert(name, ids, documents, metadatas, embeddings):
    collection(name).upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)


def delete_doc(name, doc_id):
    """문서 단위 통째 삭제. 섹션이 늘거나 줄면 조각 단위 비교로는 맞출 수 없어서 문서째 교체한다."""
    collection(name).delete(where={"doc_id": doc_id})


def doc_hashes(name):
    """{doc_id: doc_content_hash} — 증분 동기화 판단용."""
    out = {}
    got = collection(name).get(include=["metadatas"])
    for m in got.get("metadatas") or []:
        if m and m.get("doc_id"):
            out[m["doc_id"]] = m.get("doc_content_hash")
    return out


def count(name):
    return collection(name).count()


def chunks(name, documents=False):
    """감사·정리용 전체 목록 — [{id, metadata[, document]}]. 벡터는 읽지 않는다."""
    got = collection(name).get(include=["metadatas", "documents"] if documents else ["metadatas"])
    out = []
    for i, cid in enumerate(got.get("ids") or []):
        rec = {"id": cid, "metadata": (got.get("metadatas") or [])[i] or {}}
        if documents:
            rec["document"] = (got.get("documents") or [])[i] or ""
        out.append(rec)
    return out


def query_public(vector, k=12, where=None):
    """지원자 질문 생성 경로가 쓰는 유일한 검색. 컬렉션은 PUBLIC로 고정이다(인자로 받지 않는다).

    반환: [{id, document, metadata, sim}] — sim = 1 - distance.
    """
    r = collection(PUBLIC).query(query_embeddings=[vector], n_results=k, where=where or None,
                                 include=["documents", "metadatas", "distances"])
    out = []
    for i, cid in enumerate(r["ids"][0]):
        out.append({"id": cid, "document": r["documents"][0][i], "metadata": r["metadatas"][0][i],
                    "sim": 1.0 - r["distances"][0][i]})
    return out


# ---------------- 사내문서에서 만든 '질문' 컬렉션 (A17 · A18, ADLC v0.8 6.19.4) ----------------
# 조각(설명문)과 질문(한 문장)은 성질이 달라 **컬렉션을 따로** 둔다. 카테고리도 컬렉션으로 물리 분리한다
# — Chroma `where` 조건에는 카테고리를 넣지 않는다(2026-11-17 사용자 지시 · QO-02·QO-14 실측: where 870 ms vs 19 ms).
# 질문은 public 조각에서만 만든다(A17) → 메타에 disclosure=public을 남기고 검색 뒤 앱에서 다시 확인한다(B-09).
QUESTION_BY_CAT = {"personality": "internal_questions_personality",
                   "job": "internal_questions_job",
                   "company": "internal_questions_company"}


def question_collection(q_cat):
    name = QUESTION_BY_CAT.get(q_cat)
    if not name:
        raise ValueError(f"알 수 없는 질문 카테고리: {q_cat}")
    return collection(name)


def upsert_questions(q_cat, ids, documents, metadatas, embeddings):
    question_collection(q_cat).upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)


def delete_questions_of_chunk(q_cat, chunk_id):
    """조각 1개에서 나온 질문 전부 삭제(그 조각이 바뀌거나 사라졌을 때)."""
    question_collection(q_cat).delete(where={"chunk_id": chunk_id})


def delete_questions_of_doc(q_cat, doc_id):
    question_collection(q_cat).delete(where={"doc_id": doc_id})


def question_chunk_hashes(q_cat):
    """{chunk_id: chunk_hash} — 증분 재생성 판단용(조각 본문이 바뀌면 그 조각의 질문을 다시 만든다)."""
    out = {}
    got = question_collection(q_cat).get(include=["metadatas"])
    for m in got.get("metadatas") or []:
        if m and m.get("chunk_id"):
            out[m["chunk_id"]] = m.get("chunk_hash")
    return out


def questions_all(q_cat, documents=True):
    """감사·내보내기용 전체 목록 — [{id, metadata[, document]}]. 벡터는 읽지 않는다."""
    got = question_collection(q_cat).get(include=["metadatas", "documents"] if documents else ["metadatas"])
    out = []
    for i, qid in enumerate(got.get("ids") or []):
        rec = {"id": qid, "metadata": (got.get("metadatas") or [])[i] or {}}
        if documents:
            rec["document"] = (got.get("documents") or [])[i] or ""
        out.append(rec)
    return out


def count_questions(q_cat):
    return question_collection(q_cat).count()


def query_questions(vector, q_cat, k=12, where=None):
    """질문 컬렉션 KNN. 컬렉션 자체가 카테고리라 `where`에 카테고리를 넣지 않는다(기본 where=None).

    반환: [{id, document(질문 문장), metadata, sim}] — sim = 1 - distance (`query_public`과 같은 환산).
    """
    r = question_collection(q_cat).query(query_embeddings=[vector], n_results=k, where=where or None,
                                         include=["documents", "metadatas", "distances"])
    out = []
    for i, qid in enumerate(r["ids"][0]):
        out.append({"id": qid, "document": r["documents"][0][i], "metadata": r["metadatas"][0][i],
                    "sim": 1.0 - r["distances"][0][i]})
    return out
