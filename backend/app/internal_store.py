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
