"""SQL 문제 실행 검증기 (2026-11-06, SQL 문제은행).

생성 모델이 만든 SQL을 **격리 DB `sqlbank`(역할 `sqlbank_exec`, 앱 DB 접속 불가)**에서 실행한다.
- 한 번 실행 = 연결 1개 · 트랜잭션 1개 · statement_timeout 3초 · 끝나면 항상 ROLLBACK(아무것도 남지 않음)
- 금지 문장(트랜잭션 제어·권한·파일·시스템 카탈로그·함수 생성 등)은 실행 전에 거른다(역할 권한이 2차 방어)
- 결과는 {"columns": [...], "rows": [[...], ...]} — 값은 화면 표시용 문자열로 바꾼다(NULL은 None)
면접·채점·지원자 코드에는 쓰지 않는다(문제은행 생성·검증 전용).
"""
import datetime
import decimal
import re

import psycopg
from psycopg import sql as pgsql

from . import config

DENY = re.compile(r"\b(COMMIT|ROLLBACK|BEGIN|START\s+TRANSACTION|SAVEPOINT|RELEASE|GRANT|REVOKE|COPY|DO|LISTEN|NOTIFY|VACUUM|ANALYZE|"
                  r"CLUSTER|REINDEX|SECURITY|SET\s+ROLE|SET\s+SESSION|RESET|LOAD|PREPARE|EXECUTE|DEALLOCATE|DISCARD|"
                  r"CREATE\s+(OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE|TRIGGER|EXTENSION|ROLE|USER|DATABASE|SCHEMA|RULE|LANGUAGE)|"
                  r"ALTER\s+(SYSTEM|ROLE|USER|DATABASE|SCHEMA)|DROP\s+(SCHEMA|DATABASE|ROLE|USER|FUNCTION)|"
                  r"PG_\w+|DBLINK|LO_\w+|CURRENT_SETTING|SET_CONFIG|INFORMATION_SCHEMA)\b", re.I)


class SqlError(Exception):
    pass


def _strip_comments(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def check(sql):
    m = DENY.search(re.sub(r"'(?:[^']|'')*'", "''", _strip_comments(sql or "")))
    return f"허용하지 않는 문장/이름: {m.group(0)}" if m else ""


def _cell(v):
    if v is None:
        return None
    if isinstance(v, decimal.Decimal):
        v = v.normalize()
        return format(v, "f") if v == v.to_integral() else str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(round(v, 6))
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()[:19].replace("T", " ")
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return str(v)


def run(setup_sql, steps, snapshot=False, timeout_ms=3000):
    """setup_sql 실행 → steps(SQL 문자열 목록)를 차례로 실행. 마지막 단계의 결과를 돌려준다.
    반환: {"result": {"columns","rows"}|None, "tables": {이름: {"columns","rows"}} (snapshot=True면 setup 직후 표), "error": str}"""
    for s in [setup_sql, *steps]:
        bad = check(s)
        if bad:
            return {"result": None, "tables": {}, "error": bad}
    out = {"result": None, "tables": {}, "error": ""}
    try:
        with psycopg.connect(config.SQLBANK_URL, autocommit=False, connect_timeout=5) as conn:
            cur = conn.cursor()
            try:
                cur.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
                cur.execute("SET LOCAL search_path = work")
                if setup_sql.strip():
                    cur.execute(setup_sql)
                if snapshot:
                    cur.execute("SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                                "WHERE n.nspname='work' AND c.relkind='r' ORDER BY c.oid")   # 만든 순서
                    for (name,) in cur.fetchall():
                        cur.execute(pgsql.SQL("SELECT * FROM {} ORDER BY 1").format(pgsql.Identifier(name)))
                        out["tables"][name.upper()] = {"columns": [d.name.upper() for d in cur.description],
                                                       "rows": [[_cell(v) for v in r] for r in cur.fetchall()]}
                for s in steps:
                    cur.execute(s)
                    if cur.description:
                        out["result"] = {"columns": [d.name.upper() for d in cur.description],
                                         "rows": [[_cell(v) for v in r] for r in cur.fetchmany(200)]}
            finally:
                conn.rollback()
    except psycopg.Error as e:
        out["error"] = (getattr(e.diag, "message_primary", None) or str(e)).strip()[:300]
    except Exception as e:  # 연결 실패 등
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


def same(a, b, ordered=True):
    """두 결과가 같은가. ordered=False면 행 순서를 무시한다(ORDER BY 없는 질의)."""
    if not a or not b or len(a["columns"]) != len(b["columns"]):
        return False
    ra, rb = a["rows"], b["rows"]
    if not ordered:
        key = lambda r: [("" if v is None else v) for v in r]  # noqa: E731
        ra, rb = sorted(ra, key=key), sorted(rb, key=key)
    return ra == rb
