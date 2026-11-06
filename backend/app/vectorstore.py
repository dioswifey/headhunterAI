"""질문 은행 벡터 저장소 — 저장 계층만 (Redis 8 Query Engine, HNSW · COSINE, v0.7).

적재·동기화 흐름(LCEL)은 `agents/questionbank_agents.py`, 출제 체인은 `agents/question_chain.py`.
여기서는 인덱스 생성 · 문서 쓰기/지우기 · 저장된 해시 조회 · KNN 검색만 한다.

- 키: `qb:{doc_id}` (doc_id 예: `ai_ax:ai-engineer:03`, `common:cm-p001`), 인덱스 `qb_idx`(접두사 `qb:`)
- `content_hash`: 원본 행(번역 제외)의 SHA-256. 동기화 에이전트가 이 값으로 추가·변경·삭제를 판단한다
- `lang`: 원본 언어(en|ko). `*_ko`·`*_en` 두 벌을 모두 둔다(원본 언어 쪽은 원문 그대로, 다른 쪽은 번역 에이전트 결과)
- `translated`: 1 = 반대 언어 번역 완료, 0 = 번역 실패 → 다음 동기화 때 다시 시도
"""
import numpy as np
import redis
from redis.commands.search.field import NumericField, TagField, TextField, VectorField
from redis.commands.search.indexDefinition import IndexDefinition, IndexType
from redis.commands.search.query import Query

from . import config

INDEX = "qb_idx"
PREFIX = "qb:"
SCHEMA_VERSION = "3"          # 필드를 바꾸면 올린다 → ensure_index가 인덱스를 다시 만든다(문서는 유지 후 재적재)
TEXT_FIELDS = ("question", "question_ko", "question_en", "intent", "intent_ko", "intent_en", "model_answer",
               "red_flags", "follow_up", "follow_up_ko", "follow_up_en", "sub_role_name", "source", "source_file")
TAG_FIELDS = ("role_code", "sub_role", "category", "competency", "difficulty", "lang", "translated", "dup_group")


def client():
    return redis.Redis.from_url(config.REDIS_URL, decode_responses=False)


def vec(v):
    return np.asarray(v, dtype=np.float32).tobytes()


def ensure_index(r=None):
    """인덱스가 없거나 스키마 버전이 다르면 만든다. 반환: 'exists' | 'created' | 'recreated'"""
    r = r or client()
    current = r.get(f"{PREFIX}__schema_version")
    try:
        r.ft(INDEX).info()
        if current and current.decode() == SCHEMA_VERSION:
            return "exists"
        r.ft(INDEX).dropindex(delete_documents=True)   # 옛 스키마 문서는 지우고 다시 적재
        state = "recreated"
    except redis.ResponseError:
        state = "created"
    fields = [TagField(f) for f in TAG_FIELDS] + [NumericField("updated_at")] + [TextField(f) for f in TEXT_FIELDS]
    fields.append(VectorField("embedding", "HNSW", {"TYPE": "FLOAT32", "DIM": config.EMBED_DIM, "DISTANCE_METRIC": "COSINE"}))
    r.ft(INDEX).create_index(fields, definition=IndexDefinition(prefix=[PREFIX], index_type=IndexType.HASH))
    r.set(f"{PREFIX}__schema_version", SCHEMA_VERSION)
    return state


def stored_hashes(r=None, role_codes=None):
    """{doc_id: (content_hash, translated)} — 동기화 비교용. role_codes가 있으면 그 직무만."""
    r = r or client()
    out = {}
    for key in r.scan_iter(match=f"{PREFIX}*", count=500):
        k = key.decode()
        if k.startswith(f"{PREFIX}__"):
            continue
        h, t, role = r.hmget(key, "content_hash", "translated", "role_code")
        if role_codes and (role or b"").decode() not in role_codes:
            continue
        out[k[len(PREFIX):]] = ((h or b"").decode(), (t or b"0").decode())
    return out


def upsert(records, r=None):
    """records: [{doc_id, fields(dict, embedding 제외), embedding(list[float])}]"""
    r = r or client()
    pipe = r.pipeline(transaction=False)
    for rec in records:
        mapping = {k: ("" if v is None else v) for k, v in rec["fields"].items()}
        mapping["embedding"] = vec(rec["embedding"])
        key = f"{PREFIX}{rec['doc_id']}"
        pipe.delete(key)                    # 빠진 필드가 남지 않게 통째로 교체
        pipe.hset(key, mapping=mapping)
    pipe.execute()
    return len(records)


def delete(doc_ids, r=None):
    if not doc_ids:
        return 0
    r = r or client()
    return r.delete(*[f"{PREFIX}{d}" for d in doc_ids])


def knn(vector, k=5, role_codes=None, categories=None, translated_only=False, r=None):
    r = r or client()
    flt = []
    if role_codes:
        flt.append("@role_code:{%s}" % "|".join(role_codes))
    if categories:
        flt.append("@category:{%s}" % "|".join(categories))
    if translated_only:
        flt.append("@translated:{1}")
    base = " ".join(flt) or "*"
    fields = ("role_code", "sub_role", "category", "competency", "difficulty", "lang", "question", "question_ko",
              "question_en", "intent", "intent_ko", "intent_en", "follow_up_ko", "follow_up_en", "dup_group", "dist")
    q = (Query(f"({base})=>[KNN {int(k)} @embedding $v AS dist]").sort_by("dist")
         .return_fields(*fields).dialect(2).paging(0, int(k)))
    res = r.ft(INDEX).search(q, query_params={"v": vec(vector)})
    hits = []
    for d in res.docs:
        h = {f: getattr(d, f, "") for f in fields}
        h["doc_id"] = d.id[len(PREFIX):]
        h["sim"] = round(1 - float(h.pop("dist") or 1), 4)
        hits.append(h)
    return hits


def stats(r=None):
    r = r or client()
    info = r.ft(INDEX).info()
    by_role = {}
    for doc_id, (_, t) in stored_hashes(r).items():
        role = doc_id.split(":", 1)[0]
        d = by_role.setdefault(role, {"docs": 0, "translated": 0})
        d["docs"] += 1
        d["translated"] += t == "1"
    return {"index": INDEX, "num_docs": int(info["num_docs"]), "by_role": by_role}
