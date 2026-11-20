"""Coding test: problem assignment, sandbox client, scoring (PRD 4.7 COD-01~07)."""
import json
import os
import time
import uuid

from . import config, db


def assign_problems(attempt_id):
    """COD-02: basic 2 + applied 1 (2026-11-10 2문항→3문항, CLAUDE.md 참고)."""
    chosen = [-1]
    for level in ("basic", "basic", "applied"):
        p = db.one("SELECT id, starter FROM coding_problems WHERE level=%s AND id != ALL(%s) ORDER BY random() LIMIT 1",
                    level, chosen)
        chosen.append(p["id"])
        db.run("INSERT INTO coding_submissions (attempt_id, problem_id, code) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
               attempt_id, p["id"], p["starter"])


def _payload(tests, hide_hidden):
    """실행기로 보낼 케이스. hide_hidden=True(지원자 화면·채점)이면 public 표시를 같이 보내 실행기가 비공개 케이스의
    반환값·출력·오류 문구를 버리게 한다. 내부 검증 에이전트는 False(전부 공개 취급)."""
    out = []
    for t in tests:
        c = {"args": t["args"], "expected": t["expected"]}
        if hide_hidden:
            c["public"] = bool(t.get("public"))
        out.append(c)
    return out


def sandbox_run(code, func, tests, wait=20, hide_hidden=False):
    if config.RUNNER_URL:  # Railway: 실행기 서비스를 HTTP로 부른다(네트워크 격리 없음 — CLAUDE.md 배포 절)
        import httpx
        try:
            r = httpx.post(config.RUNNER_URL + "/run", headers={"x-runner-token": config.RUNNER_TOKEN}, timeout=wait,
                           json={"code": code, "func": func, "tests": _payload(tests, hide_hidden)})
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
        json.dump({"code": code, "func": func, "tests": _payload(tests, hide_hidden)}, f)
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
    res = sandbox_run(code, problem["func_name"], tests, hide_hidden=True)
    view = []
    for t, r in zip(tests, res.get("results", [])):
        view.append({"input": t["args"], "expected": t["expected"], **r})
    return {"status": res["status"], "cases": view, "stderr": res.get("stderr", ""), "stdout": res.get("stdout", "")}


def problem_points(passed, total):
    """문제 1개의 점수(2026-11-20 사용자 지시): 통과율 70% 이상 = 1점(만점), 50% 이상 = 0.5점, 그 미만 = 0점.
    완벽히 다 맞는 지원자는 드물어서, 핵심 로직이 맞으면 만점·절반쯤 맞으면 반점을 준다."""
    if not total:
        return 0.0
    rate = passed / total
    if rate >= config.CODING_FULL_RATE:
        return 1.0
    if rate >= config.CODING_HALF_RATE:
        return 0.5
    return 0.0


def points_summary(attempt_id):
    """채점된 제출 기준 문제별 점수 합계. {'points': 1.5, 'max': 3, 'passed': n, 'total': m} 또는 None."""
    subs = db.all_("SELECT passed, total FROM coding_submissions WHERE attempt_id=%s", attempt_id)
    if not subs:
        return None
    return {"points": sum(problem_points(s["passed"] or 0, s["total"] or 0) for s in subs), "max": len(subs),
            "passed": sum(s["passed"] or 0 for s in subs), "total": sum(s["total"] or 0 for s in subs)}


def grade(attempt_id):
    """COD-06 (2026-11-20 변경): 문제별 부분 점수(problem_points) 합계 / 문제 수 × 100.
    예: 3문제 중 1문제 만점(70%+)·1문제 반점(50%+)·1문제 0점 → 1.5/3 = 50.0"""
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
            res = sandbox_run(s["code"], s["func_name"], tests, hide_hidden=True)
            passed = sum(1 for r in res.get("results", []) if r.get("ok"))
        db.run("UPDATE coding_submissions SET passed=%s, total=%s WHERE id=%s", passed, len(tests), s["id"])
        rates.append(problem_points(passed, len(tests)))
    return round(sum(rates) / len(rates) * 100, 2)
