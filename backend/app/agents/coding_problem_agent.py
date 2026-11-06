"""코딩 문제 수집 에이전트 (LCEL) — 프로그래머스 / LeetCode → 가공 → PostgreSQL `collected_problems`.

AI(모델) 호출이 없다. 분기는 모두 코드 규칙 Router라 빠르다(문항당 시간 = 사이트 요청 간격).

    plan ─(Router ① 사이트: programmers·leetcode = 웹 페이지 / experthire = 공식 REST API)─▶ 사이트 어댑터(`agents/collectors/*.py`, 파일 하나 = 사이트 하나, 자동 등록)
      │                        목록: 어댑터 list_ids 또는 --ids
      ▼  문항마다
    robots_gate   robots.txt에 막힌 주소면 skip(robots)
    fetch         사이트별 요청 간격(기본 2초)·시간 제한. 403·429·503 = 차단 → 그 사이트는 이번 실행에서 멈춤(우회 안 함)
    parse         어댑터가 원문 필드를 뽑음(설명·난이도·언어·예제·시작 코드)
    Router ② 종류  python_func(함수형 Python, 실행기로 채점 가능) / sql / other
    normalize     난이도 → basic|applied, 예제 표 → tests[{args, expected, public}], 함수 이름·매개변수
    Router ③ 품질  ready(바로 게시 후보) / needs_review(예제 없음·값 해석 실패·열 수 불일치·클래스형 시작 코드)
    upsert        (source, source_id)로 저장. 내용 해시가 같으면 unchanged, 다르면 changed
                  저장 상태는 항상 license_status='unverified' → 지원자 화면(coding_problems)에는 안 나감

    마지막 조치(게시 게이트, 1개): 재사용 권한을 확인한 문제만 `approve` → `publish`가
      license_status='approved' · quality='ready' · kind='python_func'인 문제만 `coding_problems`로 복사(출처 표기)

실행(컨테이너 안):
    python -m app.agents.coding_problem_agent collect --site programmers --limit 20 [--dry-run]
    python -m app.agents.coding_problem_agent collect --site leetcode --ids two-sum valid-parentheses
    python -m app.agents.coding_problem_agent collect --site experthire --limit 20   (.env EH_SECRET_KEY 필요)
    python -m app.agents.coding_problem_agent stats
    python -m app.agents.coding_problem_agent approve --ids 3 5   (또는 --reject)
    python -m app.agents.coding_problem_agent publish [--dry-run]
"""
import argparse
import ast
import hashlib
import json
import re
import sys
import time
import urllib.robotparser
from urllib.parse import urlsplit

import httpx
from langchain_core.runnables import RunnableBranch, RunnableLambda

from .. import db
from . import collectors

USER_AGENT = "Mozilla/5.0 (compatible; headhunterAI-collector/0.1)"
MIN_INTERVAL = 2.0          # 같은 사이트 요청 간격(초)
BLOCK_STATUS = {401, 403, 429, 503}


# ---------------- 요청 (robots.txt · 간격 · 차단 감지) ----------------
class Blocked(Exception):
    pass


class Fetcher:
    def __init__(self, interval=MIN_INTERVAL):
        self.interval = interval
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True)
        self.robots, self.last, self.blocked = {}, {}, {}

    def allowed(self, url):
        host = urlsplit(url).netloc
        if host not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.client.get(f"https://{host}/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except httpx.HTTPError:
                rp.parse([])
            self.robots[host] = rp
        return self.robots[host].can_fetch(USER_AGENT, url)

    def _get(self, url, headers=None):
        host = urlsplit(url).netloc
        if host in self.blocked:
            raise Blocked(self.blocked[host])
        wait = self.interval - (time.time() - self.last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        for attempt in range(2):
            try:
                r = self.client.get(url, headers=headers)
                break
            except httpx.HTTPError:
                if attempt:
                    raise
                time.sleep(self.interval)
        self.last[host] = time.time()
        if r.status_code in BLOCK_STATUS:
            self.blocked[host] = f"HTTP {r.status_code}"
            raise Blocked(self.blocked[host])
        r.raise_for_status()
        return r

    def get_text(self, url):
        return self._get(url).text

    def get_json(self, url, headers=None):
        if not self.allowed(url):
            raise Blocked("robots.txt")
        return self._get(url, headers=headers).json()


# ---------------- 가공 ----------------
def _literal(cell):
    """예제 표의 셀 문자열 → 파이썬 값. JSON(true/false/null) 먼저, 그다음 파이썬 리터럴."""
    s = (cell or "").strip()
    for parse in (json.loads, ast.literal_eval):
        try:
            return True, parse(s)
        except Exception:
            continue
    return False, s


def _signature(starter):
    m = re.search(r"^\s*def\s+(\w+)\s*\(([^)]*)\)", starter or "", re.M)
    if not m:
        return None, []
    params = [p.split(":")[0].split("=")[0].strip() for p in m.group(2).split(",") if p.strip()]
    return m.group(1), [p for p in params if p != "self"]


def _level(n):
    if n is None:
        return None
    return "basic" if n <= 1 else "applied"      # 프로그래머스 Lv.0~1·LeetCode Easy = basic, 그 위 = applied


# ---------------- 단계 ----------------
def _parse(x):
    site = collectors.get(x["site"])
    url = site.problem_url(x["source_id"], x["kind"])
    base = {"site": x["site"], "source_id": x["source_id"], "kind_hint": x["kind"], "url": url, "fetcher": x["fetcher"]}
    if not x["fetcher"].allowed(url):
        return {**base, "skip": "robots"}
    try:
        # API형 어댑터(ExpertHire)는 자기 fetch로 JSON을 받고, 웹 페이지형은 HTML을 받는다
        html = site.fetch(x["fetcher"], url) if hasattr(site, "fetch") else x["fetcher"].get_text(url)
    except Blocked as e:
        return {**base, "skip": f"blocked ({e})"}
    except httpx.HTTPError as e:
        return {**base, "skip": f"fetch error ({type(e).__name__})"}
    return {**base, "raw": site.parse(html, x["source_id"])}


def _kind(x):
    raw = x["raw"]
    langs = set(raw.get("languages") or [])
    if x["kind_hint"] == "sql" or (langs and langs <= {"mysql", "oracle"}):
        return "sql"
    if "python3" in langs or re.search(r"^\s*def\s", raw.get("starter") or "", re.M):
        return "python_func"
    return "other"


def _normalize_python(x):
    raw, issues = x["raw"], []
    starter = raw.get("starter") or ""
    if re.search(r"^\s*class\s+\w+", starter, re.M):
        issues.append("클래스형 시작 코드(실행기는 함수 하나를 부름)")
    func, params = _signature(starter)
    if not func:
        issues.append("시작 코드에서 함수를 찾지 못함")
    header, rows = raw.get("examples_header") or [], raw.get("examples_rows") or []
    tests = []
    if not rows:
        issues.append("입출력 예 표 없음")
    elif params and len(header) != len(params) + 1:
        issues.append(f"예제 열 수({len(header)}) ≠ 매개변수 수+1({len(params) + 1})")
    else:
        for r in rows:
            vals = [_literal(c) for c in r]
            if not all(ok for ok, _ in vals) or len(vals) < 2:
                issues.append(f"예제 값 해석 실패: {r}")
                continue
            tests.append({"args": [v for _, v in vals[:-1]], "expected": vals[-1][1], "public": True})
    return {**x, "kind": "python_func", "func_name": func, "params": params, "tests": tests, "issues": issues}


def _normalize_other(kind):
    def run(x):
        base = ["SQL 문제: 실행기가 아직 SQL을 채점하지 못함"] if kind == "sql" else ["지원하지 않는 형식"]
        return {**x, "kind": kind, "func_name": None, "params": [], "tests": [],
                "issues": base + list(x["raw"].get("issues") or [])}
    return run


def _finish(x):
    raw = x["raw"]
    if not raw.get("title"):
        x["issues"].append("제목 없음")
    if len(raw.get("statement") or "") < 20:
        x["issues"].append("문제 설명이 비었거나 너무 짧음")
    x["quality"] = "ready" if not x["issues"] and x["kind"] == "python_func" else "needs_review"
    x["level"] = _level(raw.get("level_num"))
    rec = {"source": x["site"], "source_id": x["source_id"], "source_url": raw.get("origin_url") or x["url"], "title": raw.get("title") or "",
           "statement": raw.get("statement") or "", "difficulty": raw.get("difficulty") or "", "level": x["level"],
           "category": raw.get("category") or "", "kind": x["kind"], "languages": raw.get("languages") or [],
           "func_name": x["func_name"], "params": x["params"], "starter": raw.get("starter") or "",
           "examples": x["tests"], "quality": x["quality"], "issues": x["issues"]}
    rec["content_hash"] = hashlib.sha256(json.dumps({k: v for k, v in rec.items() if k not in ("quality", "issues")},
                                                    ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"skip": None, "rec": rec}


COLS = ("source", "source_id", "source_url", "title", "statement", "difficulty", "level", "category", "kind", "languages",
        "func_name", "params", "starter", "examples", "quality", "issues", "content_hash")


def _upsert(rec, dry_run):
    old = db.one("SELECT id, content_hash FROM collected_problems WHERE source=%s AND source_id=%s",
                 rec["source"], rec["source_id"])
    state = "new" if not old else ("unchanged" if old["content_hash"] == rec["content_hash"] else "changed")
    if dry_run or state == "unchanged":
        return state, (old or {}).get("id")
    vals = [db.J(rec[c]) if c == "examples" else rec[c] for c in COLS]
    row = db.run(f"""INSERT INTO collected_problems ({", ".join(COLS)}) VALUES ({", ".join(["%s"] * len(COLS))})
                     ON CONFLICT (source, source_id) DO UPDATE SET
                     {", ".join(f"{c}=EXCLUDED.{c}" for c in COLS[2:])}, updated_at=now()
                     RETURNING id""", *vals)
    return state, row["id"]


# Router ② 종류 → 가공 경로
route_kind = RunnableBranch(
    (lambda x: x["kind"] == "python_func", RunnableLambda(_normalize_python).with_config(run_name="normalize_python")),
    (lambda x: x["kind"] == "sql", RunnableLambda(_normalize_other("sql")).with_config(run_name="normalize_sql")),
    RunnableLambda(_normalize_other("other")).with_config(run_name="normalize_other"),
).with_config(run_name="router_kind")

# 문항 하나: robots·요청·파싱 → (건너뜀 | 종류 Router → 가공 → 품질 Router)
process_one = (
    RunnableLambda(_parse).with_config(run_name="fetch_parse")
    | RunnableBranch(
        (lambda x: x.get("skip"), RunnableLambda(lambda x: {"skip": x["skip"], "source_id": x["source_id"]})),
        RunnableLambda(lambda x: {**x, "kind": _kind(x)}).with_config(run_name="classify")
        | route_kind
        | RunnableLambda(_finish).with_config(run_name="router_quality"),
    ).with_config(run_name="router_skip")
).with_config(run_name="process_problem")


def _plan(x):
    """Router ① 사이트 → 어댑터, 문항 목록."""
    site = collectors.get(x["site"])
    fetcher = Fetcher(interval=x.get("interval", MIN_INTERVAL))
    kinds = tuple(x.get("kinds") or ("python_func",))
    try:
        ids = [(i, kinds[0]) for i in x["ids"]] if x.get("ids") else site.list_ids(fetcher, x.get("limit", 20), kinds)
        plan_error = None if ids else "목록 없음(이 사이트는 --ids로 문항을 직접 지정)"
    except Blocked as e:
        ids, plan_error = [], f"목록 차단 ({e})"
    except RuntimeError as e:                          # 예: API 키 없음
        ids, plan_error = [], str(e)
    return {**x, "fetcher": fetcher, "items": ids, "plan_error": plan_error}


def _run(x):
    out = {"agent": "coding_problem_collect", "site": x["site"], "dry_run": bool(x.get("dry_run")),
           "planned": len(x["items"]), "plan_error": x["plan_error"],
           "new": [], "changed": [], "unchanged": 0, "ready": 0, "needs_review": [], "skipped": []}
    for sid, kind in x["items"]:
        res = process_one.invoke({"site": x["site"], "source_id": sid, "kind": kind, "fetcher": x["fetcher"]})
        if res.get("skip"):
            out["skipped"].append({"source_id": sid, "reason": res["skip"]})
            if res["skip"].startswith("blocked"):          # 차단되면 그 사이트는 멈춘다(우회 안 함)
                out["skipped"].append({"source_id": "*", "reason": "사이트 차단 → 나머지 문항 중단"})
                break
            continue
        rec = res["rec"]
        state, pid = _upsert(rec, x.get("dry_run"))
        if state == "unchanged":
            out["unchanged"] += 1
        else:
            out[state].append(f"{sid}:{rec['title']}")
        if rec["quality"] == "ready":
            out["ready"] += 1
        else:
            out["needs_review"].append({"source_id": sid, "title": rec["title"], "issues": rec["issues"]})
    return out


collect_agent = (RunnableLambda(_plan).with_config(run_name="router_site")
                 | RunnableLambda(_run).with_config(run_name="collect_loop")).with_config(run_name="coding_problem_agent")


# ---------------- 마지막 조치: 재사용 권한 게이트 → 게시 ----------------
def approve(ids, status="approved"):
    rows = db.all_("UPDATE collected_problems SET license_status=%s, updated_at=now() WHERE id = ANY(%s) RETURNING id",
                   status, list(ids))
    return {"updated": [r["id"] for r in rows], "license_status": status}


def publish(dry_run=False):
    rows = db.all_("""SELECT * FROM collected_problems
                      WHERE license_status='approved' AND quality='ready' AND kind='python_func'
                        AND level IS NOT NULL AND published_problem_id IS NULL ORDER BY id""")
    done = []
    for r in rows:
        stmt = f"{r['statement']}\n\n출처: {r['source_url']}"
        if not dry_run:
            p = db.run("""INSERT INTO coding_problems (level, title, statement, starter, func_name, tests)
                          VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                       r["level"], r["title"], stmt, r["starter"], r["func_name"], db.J(r["examples"]))
            db.run("UPDATE collected_problems SET published_problem_id=%s, updated_at=now() WHERE id=%s", p["id"], r["id"])
        done.append({"id": r["id"], "title": r["title"], "level": r["level"]})
    return {"published": done, "dry_run": dry_run}


def stats():
    rows = db.all_("""SELECT source, kind, quality, license_status, count(*) AS n,
                             count(published_problem_id) AS published
                      FROM collected_problems GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""")
    return [dict(r) for r in rows]


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--site", required=True, help=f"사이트: {sorted(collectors.sites())}")
    c.add_argument("--limit", type=int, default=20)
    c.add_argument("--ids", nargs="*")
    c.add_argument("--kinds", nargs="*", default=["python_func"], choices=["python_func", "sql"])
    c.add_argument("--interval", type=float, default=MIN_INTERVAL)
    c.add_argument("--dry-run", action="store_true")
    a2 = sub.add_parser("approve")
    a2.add_argument("--ids", nargs="+", type=int, required=True)
    a2.add_argument("--reject", action="store_true")
    pb = sub.add_parser("publish")
    pb.add_argument("--dry-run", action="store_true")
    sub.add_parser("stats")
    a = p.parse_args(argv)
    db.pool.open()                     # CLI 실행: 앱 시작 때 여는 연결 풀을 직접 연다(QA-13)
    if a.cmd == "collect":
        out = collect_agent.invoke({"site": a.site, "limit": a.limit, "ids": a.ids, "kinds": a.kinds,
                                    "interval": max(a.interval, 1.0), "dry_run": a.dry_run})
    elif a.cmd == "approve":
        out = approve(a.ids, "rejected" if a.reject else "approved")
    elif a.cmd == "publish":
        out = publish(a.dry_run)
    else:
        out = stats()
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    sys.exit(main())
