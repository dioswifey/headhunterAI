"""Coding test: problem assignment, sandbox client, scoring (PRD 4.7 COD-01~07)."""
import json
import os
import time
import uuid

from . import config, db


def assign_problems(attempt_id):
    """COD-02: basic 1 + applied 1."""
    for level in ("basic", "applied"):
        p = db.one("SELECT id FROM coding_problems WHERE level=%s ORDER BY random() LIMIT 1", level)
        starter = db.one("SELECT starter FROM coding_problems WHERE id=%s", p["id"])["starter"]
        db.run("INSERT INTO coding_submissions (attempt_id, problem_id, code) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
               attempt_id, p["id"], starter)


def sandbox_run(code, func, tests, wait=20):
    if config.RUNNER_URL:  # Railway: 실행기 서비스를 HTTP로 부른다(네트워크 격리 없음 — CLAUDE.md 배포 절)
        import httpx
        try:
            r = httpx.post(config.RUNNER_URL + "/run", headers={"x-runner-token": config.RUNNER_TOKEN}, timeout=wait,
                           json={"code": code, "func": func, "tests": [{"args": t["args"], "expected": t["expected"]} for t in tests]})
            r.raise_for_status()
            return r.json()
        except Exception:
            return {"status": "error", "results": [], "stderr": "실행 환경 응답 없음"}
    job_id = uuid.uuid4().hex
    jobs, results = os.path.join(config.SANDBOX_DIR, "jobs"), os.path.join(config.SANDBOX_DIR, "results")
    os.makedirs(jobs, exist_ok=True)
    os.makedirs(results, exist_ok=True)
    tmp = os.path.join(jobs, job_id + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"code": code, "func": func, "tests": [{"args": t["args"], "expected": t["expected"]} for t in tests]}, f)
    os.replace(tmp, os.path.join(jobs, job_id + ".json"))
    out = os.path.join(results, job_id + ".json")
    deadline = time.time() + wait
    while time.time() < deadline:
        if os.path.exists(out):
            with open(out, encoding="utf-8") as f:
                res = json.load(f)
            os.remove(out)
            return res
        time.sleep(0.1)
    return {"status": "error", "results": [], "stderr": "실행 환경 응답 없음"}


def run_public(problem, code):
    """반복 실행: 공개 예제만 (정답·비공개 테스트 비노출)."""
    tests = [t for t in problem["tests"] if t.get("public")]
    res = sandbox_run(code, problem["func_name"], tests)
    view = []
    for t, r in zip(tests, res.get("results", [])):
        view.append({"input": t["args"], "expected": t["expected"], **r})
    return {"status": res["status"], "cases": view, "stderr": res.get("stderr", ""), "stdout": res.get("stdout", "")}


def grade(attempt_id):
    """COD-06: (public + hidden) pass rate × 100, problems weighted equally."""
    subs = db.all_("""SELECT s.*, p.tests, p.func_name FROM coding_submissions s
                      JOIN coding_problems p ON p.id = s.problem_id WHERE s.attempt_id=%s""", attempt_id)
    if not subs:
        return None
    rates = []
    for s in subs:
        tests = s["tests"]
        if not s["code"].strip():
            passed = 0
        else:
            res = sandbox_run(s["code"], s["func_name"], tests)
            passed = sum(1 for r in res.get("results", []) if r.get("ok"))
        db.run("UPDATE coding_submissions SET passed=%s, total=%s WHERE id=%s", passed, len(tests), s["id"])
        rates.append(passed / len(tests))
    return round(sum(rates) / len(rates) * 100, 2)
