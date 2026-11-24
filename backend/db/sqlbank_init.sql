-- SQL 문제 실행 검증용 격리 DB (2026-11-06, SQL 문제은행 — ADLC v0.4 6.12)
-- 앱 DB(headhunter)와 다른 DB `sqlbank`, 권한이 거의 없는 역할 `sqlbank_exec`.
-- 생성 모델이 만든 SQL은 이 DB의 `work` 스키마에서, 트랜잭션 안에서만 실행하고 항상 ROLLBACK 한다(sqlbank.py).
-- 적용(최초 1회, 수퍼유저):
--   docker exec -i headhunterAI-db psql -U headhunter -d headhunter < backend/db/sqlbank_init.sql
--   (이 파일은 \connect를 쓰므로 psql로 실행)
SELECT 'CREATE DATABASE sqlbank' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'sqlbank')\gexec
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sqlbank_exec') THEN
    CREATE ROLE sqlbank_exec LOGIN PASSWORD 'sqlbank_exec' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 4;
  END IF;
END $$;
-- 앱 DB에는 접속하지 못하게
REVOKE CONNECT ON DATABASE headhunter FROM PUBLIC;
GRANT CONNECT ON DATABASE sqlbank TO sqlbank_exec;

\connect sqlbank
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS work;
GRANT USAGE, CREATE ON SCHEMA work TO sqlbank_exec;
ALTER ROLE sqlbank_exec IN DATABASE sqlbank SET search_path = work;
ALTER ROLE sqlbank_exec SET statement_timeout = '3s';
-- SQLD(Oracle 문법)에서 자주 쓰는 함수 일부를 흉내 낸다. 나머지 Oracle 전용 문법은 생성 프롬프트에서 금지
CREATE OR REPLACE FUNCTION work.nvl(a anyelement, b anyelement) RETURNS anyelement LANGUAGE sql IMMUTABLE AS 'SELECT COALESCE(a, b)';
CREATE OR REPLACE FUNCTION work.nvl2(a anyelement, b anycompatible, c anycompatible) RETURNS anycompatible LANGUAGE sql IMMUTABLE AS 'SELECT CASE WHEN a IS NOT NULL THEN b ELSE c END';
GRANT EXECUTE ON FUNCTION work.nvl(anyelement, anyelement), work.nvl2(anyelement, anycompatible, anycompatible) TO sqlbank_exec;
