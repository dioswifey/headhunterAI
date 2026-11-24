"""문제·질문 은행 배포용 파일 (2026-11-09 사용자 지시: 로컬 DB·Redis에만 있던 은행을 git에 올려 어디서든 다시 채우기).

파일 = backend/db/seed/ (api 이미지에 /srv/db/seed로 들어감, .railwayignore 대상 아님)
  - bank_coding_problems.sql   코딩 문제은행(coding_problems)
  - bank_sql_problems.sql      SQL 문제은행(sql_problems)
  - bank_question_bank.sql     DB 질문 은행(question_bank, Redis 실패 때 대체 경로)
  - questionbank_redis.jsonl.gz  Redis 질문 은행(qb:*) — 한·영 번역 + 임베딩 벡터까지. 적재할 때 번역·임베딩 모델이 필요 없다

SQL 파일은 'INSERT … SELECT FROM jsonb_to_recordset(…) WHERE NOT EXISTS(자연 키)' 한 문장씩이라
몇 번 실행해도 중복이 생기지 않고, 이미 있는 행은 건드리지 않는다(id는 받는 DB가 새로 매김).
Redis도 없는 문서만 넣는다(있는 문서는 그대로 → 로컬에서 새로 번역한 내용을 덮어쓰지 않음).

    docker exec headhunterAI-api python -m app.bank_seed export --out /tmp/seed   # 로컬 DB·Redis → 파일
    docker cp headhunterAI-api:/tmp/seed/. backend/db/seed/
    docker exec headhunterAI-api python -m app.bank_seed apply                    # 파일 → DB·Redis (기동 때 자동)
    docker exec headhunterAI-api python -m app.bank_seed status
"""
import argparse
import base64
import gzip
import json
import os
import sys
from pathlib import Path

from . import config, db

SEED_DIR = Path(os.getenv("BANK_SEED_DIR", str(Path(config.SCHEMA_PATH).parent / "seed")))
REDIS_FILE = "questionbank_redis.jsonl.gz"
DOLLAR = "$bank$"

# 테이블 → (내보낼 컬럼, 자연 키). id·created_at·사용 횟수는 받는 DB가 새로 둔다
TABLES = {
    "coding_problems": (None, ("func_name", "title")),
    "sql_problems": (None, ("subject", "title", "stem")),
    "question_bank": (("category", "subcategory", "text", "source", "dup_group"), ("category", "text")),
}
SKIP_COLS = {"id", "created_at", "use_count"}


def _columns(table):
    rows = db.all_("""SELECT column_name, data_type, udt_name FROM information_schema.columns
                      WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""", table)
    def typ(r):
        if r["data_type"] == "ARRAY":
            return r["udt_name"].lstrip("_") + "[]"
        return r["udt_name"] if r["data_type"] == "USER-DEFINED" else r["data_type"]
    return [(r["column_name"], typ(r)) for r in rows]


def _sql_for(table, cols, key, rows):
    """jsonb_to_recordset으로 한 번에 넣는 SQL. 값은 JSON 한 덩어리($bank$ 인용)라 이스케이프 문제가 없다."""
    payload = json.dumps(rows, ensure_ascii=False, default=str)
    if DOLLAR in payload:
        raise ValueError(f"{table}: 데이터에 {DOLLAR}가 들어 있어 인용할 수 없음")
    names = ", ".join(c for c, _ in cols)
    typed = ", ".join(f"{c} {t}" for c, t in cols)
    cond = " AND ".join(f"t.{k} IS NOT DISTINCT FROM x.{k}" for k in key)
    return (f"-- headhunterAI 은행 배포 파일: {table} {len(rows)}행 (app.bank_seed export로 생성, 직접 고치지 않음)\n"
            f"INSERT INTO {table} ({names})\nSELECT {names} FROM jsonb_to_recordset({DOLLAR}{payload}{DOLLAR}::jsonb) AS x({typed})\n"
            f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {cond});\n")


def export_sql(out):
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    for table, (want, key) in TABLES.items():
        cols = [(c, t) for c, t in _columns(table) if c not in SKIP_COLS and (want is None or c in want)]
        if table == "sql_problems":       # 공개된 문제만(보류·거절은 로컬 기록)
            rows = db.all_(f"SELECT {', '.join(c for c, _ in cols)} FROM {table} WHERE status='published' ORDER BY id")
        else:
            rows = db.all_(f"SELECT {', '.join(c for c, _ in cols)} FROM {table} ORDER BY id")
        (out / f"bank_{table}.sql").write_bytes(_sql_for(table, cols, key, rows).encode("utf-8"))
        report[table] = len(rows)
    return report


def export_redis(out):
    from . import vectorstore
    r = vectorstore.client()
    n = 0
    with gzip.open(out / REDIS_FILE, "wb") as f:
        f.write((json.dumps({"_meta": {"schema_version": vectorstore.SCHEMA_VERSION, "embed_model": config.EMBED_MODEL,
                                       "embed_dim": config.EMBED_DIM}}, ensure_ascii=False) + "\n").encode("utf-8"))
        for key in sorted(r.scan_iter(match=f"{vectorstore.PREFIX}*", count=500)):
            k = key.decode()
            if k.startswith(f"{vectorstore.PREFIX}__") or r.type(key) != b"hash":
                continue
            h = r.hgetall(key)
            doc = {"doc_id": k[len(vectorstore.PREFIX):], "embedding": base64.b64encode(h.pop(b"embedding", b"")).decode()}
            doc["fields"] = {a.decode(): b.decode("utf-8", "replace") for a, b in h.items()}
            f.write((json.dumps(doc, ensure_ascii=False) + "\n").encode("utf-8"))
            n += 1
    return n


def apply_sql(seed_dir=SEED_DIR):
    """seed 폴더의 bank_*.sql을 실행. 반환 {파일: 새로 넣은 행 수}"""
    report = {}
    for p in sorted(Path(seed_dir).glob("bank_*.sql")):
        with db.tx() as conn:
            report[p.name] = conn.execute(p.read_text(encoding="utf-8")).rowcount
    return report


def apply_redis(seed_dir=SEED_DIR):
    """Redis에 없는 질문 은행 문서만 넣는다. 임베딩 모델·번역 모델 없이 동작. 반환 넣은 문서 수"""
    from . import vectorstore
    path = Path(seed_dir) / REDIS_FILE
    if not path.exists():
        return 0
    r = vectorstore.client()
    vectorstore.ensure_index(r)
    pipe, n = r.pipeline(transaction=False), 0
    with gzip.open(path, "rb") as f:
        for line in f:
            doc = json.loads(line)
            if "_meta" in doc:
                meta = doc["_meta"]
                if int(meta.get("embed_dim", 0)) != config.EMBED_DIM:
                    raise ValueError(f"임베딩 차원이 다름: 파일 {meta.get('embed_dim')} ≠ 설정 {config.EMBED_DIM}")
                continue
            key = f"{vectorstore.PREFIX}{doc['doc_id']}"
            if r.exists(key):
                continue
            mapping = dict(doc["fields"])
            mapping["embedding"] = base64.b64decode(doc["embedding"])
            pipe.hset(key, mapping=mapping)
            n += 1
    pipe.execute()
    return n


def apply_all(seed_dir=SEED_DIR):
    """기동 때 호출(main.lifespan). 실패해도 서비스는 뜬다 — 결과·오류를 dict로 돌려준다."""
    out = {}
    try:
        out["sql"] = apply_sql(seed_dir)
    except Exception as e:
        out["sql_error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        out["redis"] = apply_redis(seed_dir)
    except Exception as e:
        out["redis_error"] = f"{type(e).__name__}: {e}"[:300]
    return out


def status():
    out = {t: db.one(f"SELECT count(*) n FROM {t}")["n"] for t in TABLES}
    try:
        from . import vectorstore
        out["redis"] = vectorstore.stats()
    except Exception as e:
        out["redis_error"] = type(e).__name__
    out["seed_files"] = sorted(p.name for p in SEED_DIR.glob("*")) if SEED_DIR.exists() else []
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["export", "apply", "status"])
    p.add_argument("--out", default=str(SEED_DIR))
    a = p.parse_args(argv)
    db.pool.open(wait=True)
    if a.cmd == "export":
        out = Path(a.out)
        res = export_sql(out)
        res["redis_docs"] = export_redis(out)
    elif a.cmd == "apply":
        res = apply_all(Path(a.out))
    else:
        res = status()
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    sys.exit(main())
