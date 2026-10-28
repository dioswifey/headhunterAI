"""Thin PostgreSQL helpers (psycopg 3 + pool, dict rows)."""
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=10, kwargs={"row_factory": dict_row}, open=False)


@contextmanager
def tx():
    """One transaction; commits on success."""
    with pool.connection() as conn:
        with conn.transaction():
            yield conn


def all_(sql, *params):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def one(sql, *params):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()


def run(sql, *params):
    with pool.connection() as conn:
        cur = conn.execute(sql, params)
        try:
            return cur.fetchone()
        except Exception:
            return None


def audit(actor_id, action, target_type, target_id=None):
    run("INSERT INTO audit_logs (actor_id, action, target_type, target_id) VALUES (%s,%s,%s,%s)",
        actor_id, action, target_type, None if target_id is None else str(target_id))


J = Jsonb
