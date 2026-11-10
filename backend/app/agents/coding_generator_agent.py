"""코딩 문제 생성 에이전트 A10 (LCEL, ADLC v0.4 6.11) — 자체 문제은행. 검증은 검증 에이전트 A11(`coding_verifier_agent.py`)을 Subagent로 부른다.

입력 = 출제 명세(topic · difficulty · learning_objective · target_complexity, `agents/coding_specs.py`).
**수집한 외부 문제(collected_problems)의 본문·제목·예시·해설·테스트는 생성 프롬프트·RAG에 넣지 않는다.**
(A11의 유사도 참고 지표만 수집 문제의 설명 벡터와 비교한다 — 생성 입력이 아님)

    ① 입력      출제 명세
    ② 생성      모델(CODING_GEN_MODEL, 기본 qwen3:8b) 1회: 제목·설명·입출력 규격·제약 조건·기준 풀이·해설·복잡도·단순 풀이·
                validator·예시/경계/무작위/최대 입력 생성 코드·틀린 풀이 2개
    ③ 검증 1    A11 execution_verifier (V1~V7, 실행기)
    ④ 예시 설명 모델 1회: 실행기가 계산한 예시 출력으로 설명 3개
    ⑤ 검증 2    A11 content_verifier (V8~V11)
    ⑥ 수정      ③~⑤ 실패 → 실패 이유와 이전 초안을 넘겨 **수정**(최대 3번). 그래도 실패 → 보류(on_hold)로 저장, 게시 안 함
    ⑦ 저장      generated_problems: origin='self_generated', quality_status(verified|on_hold), generation_log·verification_log 따로
    ⑧ 게시 게이트 review=auto(현재 프로젝트 기본) → 검증 통과분만 coding_problems로 게시 / manual → 검토 대기
                (외부 수집 문제의 license_status와 자체 문제의 quality_status는 다른 열·다른 표)

실행(컨테이너 안):
    python -m app.agents.coding_generator_agent generate --all            # coding_specs.SPECS 10개
    python -m app.agents.coding_generator_agent generate --spec 3 [--review manual] [--dry-run]
    python -m app.agents.coding_generator_agent approve --ids 3 [--reject] · stats
"""
import argparse
import hashlib
import json
import re
import sys
import time

from langchain_core.runnables import RunnableLambda

from .. import config, db, llm, prompts, vectorstore
from .coding_specs import SPECS, framework_specs
from .coding_verifier_agent import GEN_PREFIX, LEVEL_DESC, _event, content_verifier, execution_verifier

MAX_REPAIRS = 3
LICENSE = "self-generated (headhunterAI 코딩 문제 생성 에이전트, 외부 문제 미사용)"
S = lambda n: {"type": "string", "maxLength": n}   # noqa: E731
GEN_SCHEMA = {"type": "object", "properties": {
    "title": S(60), "statement": S(900), "io_spec": S(900),
    "constraints": {"type": "array", "items": S(160), "minItems": 2, "maxItems": 10},
    "func_name": S(40), "params": {"type": "array", "items": S(30), "minItems": 1, "maxItems": 3},
    "solution": S(2500), "solution_explanation": S(900), "time_complexity": S(40), "space_complexity": S(40),
    "brute": S(2000), "validator": S(1500), "gen_examples": S(900), "gen_edge": S(1500), "gen_random": S(1200), "gen_large": S(1200),
    "wrong_solutions": {"type": "array", "items": S(2000), "minItems": 2, "maxItems": 2}},
    "required": ["title", "statement", "io_spec", "constraints", "func_name", "params", "solution", "solution_explanation",
                 "time_complexity", "space_complexity", "brute", "validator", "gen_examples", "gen_edge", "gen_random", "gen_large",
                 "wrong_solutions"]}
EXPLAIN_SCHEMA = {"type": "object", "properties": {"explanations": {"type": "array", "items": S(500), "minItems": 3, "maxItems": 3}},
                  "required": ["explanations"]}
CODE_KEYS = ("solution", "brute", "validator", "gen_examples", "gen_edge", "gen_random", "gen_large")


def _system(sp, feedback=""):
    return prompts.load("generate/coding_problem_generator", topic=sp["topic"], difficulty=sp["difficulty"],
                        level_desc=LEVEL_DESC[sp["difficulty"]], learning_objective=sp["learning_objective"],
                        target_complexity=sp["target_complexity"], structure=sp.get("structure") or "자유(문제에 맞게)",
                        feedback=feedback)


def _call(x, system, user, schema, max_tokens, timeout=1200):
    """생성·수정·예시 설명 = 문제은행 '생성' 호출. CODING_GEN_PROVIDER=openai면 OpenAI API(사용자 결정 2026-11-05),
    아니면 로컬 Qwen. 검증의 AI 검토(V10)는 이 함수를 쓰지 않는다(로컬 Qwen 고정)."""
    if config.CODING_GEN_PROVIDER == "openai":
        try:
            # 추론 계열(gpt-5.x)은 생각 토큰도 출력 한도에 들어가므로 넉넉히
            out, usage = llm.openai_chat_json(system, user, schema, timeout=600, max_tokens=max(max_tokens, 25000), model=_gen_model(x))
        except llm.LLMError as e:                # 실패한 호출의 토큰도 비용에 넣는다
            if e.usage:
                x.setdefault("usage", []).append({**e.usage, "attempt": x["attempt"], "failed": True})
            raise
        x.setdefault("usage", []).append({**usage, "attempt": x["attempt"]})
        return out
    return llm.chat_json(system, user + "\n/no_think", schema, timeout=timeout, temperature=0.4, model=config.CODING_GEN_MODEL,
                         max_tokens=max_tokens, num_ctx=8192)


def _gen_model(x=None):
    """이 명세에 쓸 생성 모델: 명세의 model(레벨별 분리) > OPENAI_MODEL. 로컬이면 CODING_GEN_MODEL."""
    if config.CODING_GEN_PROVIDER != "openai":
        return config.CODING_GEN_MODEL
    return ((x or {}).get("spec") or {}).get("model") or config.OPENAI_MODEL


def _cost(usage):
    pin = sum(u["prompt_tokens"] for u in usage)
    pout = sum(u["completion_tokens"] for u in usage)
    priced = config.OPENAI_PRICE_IN_PER_M or config.OPENAI_PRICE_OUT_PER_M
    usd = round(pin / 1e6 * config.OPENAI_PRICE_IN_PER_M + pout / 1e6 * config.OPENAI_PRICE_OUT_PER_M, 6) if priced else None
    return {"calls": len(usage), "prompt_tokens": pin, "completion_tokens": pout, "usd": usd,
            "price_note": None if usd is not None else "OPENAI_PRICE_IN_PER_M·OUT_PER_M 미설정 — 토큰 수만 기록"}


# ---------------- ② 생성 / ⑥ 수정 ----------------
def s_generate(x):
    sp, t0 = x["spec"], time.time()
    try:
        if x["attempt"] == 1:
            d = _call(x, _system(sp), "출제 명세에 맞는 새 문제를 설계해 JSON으로 출력한다. 코드는 짧고 간결하게.", GEN_SCHEMA, 4500)
            kind = "generate"
        else:
            prev = {k: x["draft"].get(k) for k in GEN_SCHEMA["properties"]}
            fb = f"\n[이전 초안 검증 실패 — 고칠 것]\n- {x['feedback'][-1]}\n"
            d = _call(x, _system(sp, fb), "아래 이전 초안에서 실패 이유를 고친 **전체** JSON을 다시 출력한다. 실패 이유와 관련된 칸은 반드시 바꾸고, 나머지는 유지한다.\n[이전 초안]\n"
                      + json.dumps(prev, ensure_ascii=False)[:12000], GEN_SCHEMA, 4500)
            kind = "repair"
    except llm.LLMError as e:
        x["draft"] = x.get("draft") or {}
        return _event(x, "generate" if x["attempt"] == 1 else "repair", False, reason=f"모델 호출 실패: {e}",
                      sec=round(time.time() - t0))
    x["draft"] = d
    return _event(x, kind, True, sec=round(time.time() - t0), model=_gen_model(x))


# ---------------- ④ 예시 설명 ----------------
def s_explain(x):
    if x.get("fail"):
        return x
    d = x["draft"]
    ex = [c for c in x["cases"] if c["kind"] == "ex"]
    user = (f"[제목] {d['title']}\n[설명]\n{d['statement']}\n[입출력 규격]\n{d['io_spec']}\n\n"
            + "\n".join(f"예시 {i + 1}: 입력 {json.dumps(c['args'], ensure_ascii=False)} → 출력 {json.dumps(c['expected'], ensure_ascii=False)}"
                        for i, c in enumerate(ex)))
    t0 = time.time()
    try:
        r = _call(x, prompts.load("generate/coding_problem_examples"), user, EXPLAIN_SCHEMA, 1500, timeout=600)
    except llm.LLMError as e:
        return _event(x, "explain", False, reason=f"예시 설명 모델 호출 실패: {e}")
    x["examples"] = [{"input": c["args"], "expected": c["expected"], "explanation": (e or "").strip()}
                     for c, e in zip(ex, r.get("explanations") or [])]
    return _event(x, "explain", True, sec=round(time.time() - t0))


attempt_chain = (RunnableLambda(s_generate).with_config(run_name="2_generate_or_repair")
                 | execution_verifier
                 | RunnableLambda(s_explain).with_config(run_name="4_explain_examples")
                 | content_verifier).with_config(run_name="coding_problem_attempt")


# ---------------- ⑦ 저장 · ⑧ 게시 게이트 ----------------
def _starter(func, params):
    return f"def {func}({', '.join(params)}):\n    answer = None\n    # 여기에 코드를 작성하세요\n    return answer\n"


def _display(d, examples):
    parts = [d["statement"].strip(), "[입력·출력]\n" + d["io_spec"].strip(), "[제한 사항]\n" + "\n".join("- " + re.sub(r"^\s*(\d+[.)]|[-•])\s*", "", c) for c in d["constraints"])]
    if examples:
        parts.append("[예시 설명]\n" + "\n".join(f"예시 {i + 1}: {e['explanation']}" for i, e in enumerate(examples)))
    return "\n\n".join(parts)


def publish_one(gid):
    g = db.one("SELECT * FROM generated_problems WHERE id=%s", gid)
    if not g or g["published_problem_id"] or g["quality_status"] != "verified":
        return None
    p = db.run("""INSERT INTO coding_problems (level, title, statement, starter, func_name, tests)
                  VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
               g["level"], g["title"], g["public_statement"], g["starter"], g["func_name"], db.J(g["tests"]))
    db.run("UPDATE generated_problems SET published_problem_id=%s, status='approved', updated_at=now() WHERE id=%s", p["id"], gid)
    return p["id"]


def _split_logs(history):
    gen = [h for h in history if h["stage"] in ("spec", "generate", "repair", "explain")]
    ver = [h for h in history if h["stage"] not in ("spec", "generate", "repair", "explain")]
    return gen, ver


def store(x, verified):
    d, sp = x.get("draft") or {}, x["spec"]
    func, params = d.get("func_name") or "", d.get("params") or []
    tests = []
    if verified:
        tests = [{"args": c["args"], "expected": c["expected"], "public": c["kind"] == "ex"} for c in x["cases"]]
    status = ("approved" if sp["review"] == "auto" else "pending_review") if verified else "on_hold"
    gen_log, ver_log = _split_logs(x["history"])
    last = {h["stage"]: {k: v for k, v in h.items() if k not in ("t", "attempt", "stage")} for h in ver_log if h["ok"]}
    summary = {"verified": verified, "attempts": x["attempt"], "elapsed_sec": x.get("elapsed_sec"),
               "framework": x["spec"].get("framework"), "structure": x["spec"].get("structure"), "generation_provider": config.CODING_GEN_PROVIDER,
               "generation_model": _gen_model(x), "cost": _cost(x.get("usage") or []), "repairs": x["attempt"] - 1, "passed_stages": sorted(last),
               "fail": None if verified else x.get("fail"), "review": x.get("review"),
               "similarity_note": "유사도는 참고 지표이며 독창성·법적 적합성을 확정하지 않음",
               "sandbox": "실행기: 시간 10초·메모리 256MB·권한 없는 사용자, 로컬 네트워크 차단(docker-compose network_mode none)"}
    row = db.run("""INSERT INTO generated_problems
        (concept, level, spec, title, statement, public_statement, func_name, params, starter, solution, brute, gen_large, tests, checks,
         status, review_mode, history, license, references_used, model, prompt_version, content_hash,
         origin, quality_status, learning_objective, target_complexity, io_spec, constraints, examples, solution_explanation,
         time_complexity, space_complexity, validator, gen_examples, gen_edge, gen_random, wrong_solutions,
         generation_log, verification_log, verification_summary)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                'self_generated',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                 sp["topic"], sp["difficulty"], db.J(sp), d.get("title") or "(제목 없음)", d.get("statement") or "",
                 _display(d, x.get("examples")) if verified else "", func, params, _starter(func, params) if func else "",
                 d.get("solution") or "", d.get("brute") or "", d.get("gen_large") or "", db.J(tests), db.J(summary),
                 status, sp["review"], db.J(x["history"]), LICENSE, [], _gen_model(x), config.VERSIONS["prompt"],
                 hashlib.sha256(((d.get("title") or "") + (d.get("statement") or "")).encode("utf-8")).hexdigest(),
                 "verified" if verified else "on_hold", sp["learning_objective"], sp["target_complexity"], d.get("io_spec") or "",
                 d.get("constraints") or [], db.J(x.get("examples") or []), d.get("solution_explanation") or "",
                 d.get("time_complexity") or "", d.get("space_complexity") or "", d.get("validator") or "", d.get("gen_examples") or "",
                 d.get("gen_edge") or "", d.get("gen_random") or "", db.J(d.get("wrong_solutions") or []),
                 db.J(gen_log), db.J(ver_log), db.J(summary))
    gid = row["id"]
    pid = None
    if verified:
        vectorstore.client().hset(f"{GEN_PREFIX}{gid}", mapping={"kind": "gen", "embedding": vectorstore.vec(x["embedding"])})
        pid = publish_one(gid) if status == "approved" else None
    return {"id": gid, "status": status, "quality_status": "verified" if verified else "on_hold", "published_problem_id": pid}


def run_spec(spec, dry_run=False):
    t0 = time.time()
    x = {"spec": spec, "history": [{"stage": "spec", "attempt": 0, "ok": True, "t": int(time.time()), **spec}],
         "feedback": [], "attempt": 0, "draft": {}}
    for attempt in range(1, MAX_REPAIRS + 2):          # 처음 1번 + 수정 최대 3번
        x.update(attempt=attempt, fail=None)
        x = attempt_chain.invoke(x)
        if not x.get("fail"):
            break
        x["feedback"].append(f"[{x['fail']['stage']}] {x['fail'].get('reason', '')}")
    verified = not x.get("fail")
    x["elapsed_sec"] = round(time.time() - t0, 1)
    out = {"spec": f"{spec['topic']} / {'Junior' if spec['difficulty'] == 'basic' else 'Senior'} / {spec.get('structure', '-')}",
           "elapsed_sec": x["elapsed_sec"], "attempts": x["attempt"], "verified": verified,
           "model": _gen_model(x), "cost": _cost(x.get("usage") or []),
           "title": (x.get("draft") or {}).get("title"), "fail_reasons": x["feedback"]}
    if dry_run:
        return out
    return {**out, **store(x, verified)}


generator_agent = RunnableLambda(lambda items: [run_spec(i["spec"], i.get("dry_run")) for i in items]).with_config(
    run_name="coding_generator_agent")


def approve(ids, reject=False):
    out = []
    for gid in ids:
        if reject:
            db.run("UPDATE generated_problems SET status='rejected', updated_at=now() WHERE id=%s", gid)
            out.append({"id": gid, "status": "rejected"})
        else:
            out.append({"id": gid, "published_problem_id": publish_one(gid)})
    return out


def stats():
    return [dict(r) for r in db.all_("""SELECT concept, level, quality_status, status, count(*) AS n, count(published_problem_id) AS published
                                        FROM generated_problems GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""")]


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--all", action="store_true")
    g.add_argument("--spec", type=int, nargs="*", help="coding_specs.SPECS 번호(0부터)")
    g.add_argument("--first", type=int, default=0, help="SPECS 앞에서부터 n개(예: --first 5)")
    g.add_argument("--framework", type=int, default=0, help="프로그래머스 유형 통계(개수만) 기반 명세 n개(레벨·개념·입출력 구조)")
    g.add_argument("--seed", type=int, default=None)
    g.add_argument("--model-junior", default="", help="Junior 명세에 쓸 OpenAI 모델(비우면 OPENAI_MODEL)")
    g.add_argument("--model-senior", default="", help="Senior 명세에 쓸 OpenAI 모델")
    g.add_argument("--review", choices=["auto", "manual"], default="auto")
    g.add_argument("--dry-run", action="store_true")
    ap = sub.add_parser("approve")
    ap.add_argument("--ids", nargs="+", type=int, required=True)
    ap.add_argument("--reject", action="store_true")
    sub.add_parser("stats")
    a = p.parse_args(argv)
    db.pool.open()
    if a.cmd == "generate":
        if a.framework:
            specs = framework_specs(a.framework, seed=a.seed)
            for sp in specs:                               # 레벨별 모델 분리(A/B 결과: Junior 6.1-sol, Senior 5.5)
                m = a.model_junior if sp["difficulty"] == "basic" else a.model_senior
                if m:
                    sp["model"] = m
        else:
            idx = range(len(SPECS)) if a.all else (range(min(a.first, len(SPECS))) if a.first else (a.spec or []))
            specs = [SPECS[i] for i in idx]
        if not specs:
            raise SystemExit("--all · --first n · --spec 번호 중 하나가 필요하다")
        if config.CODING_GEN_PROVIDER == "openai" and not config.OPENAI_API_KEY and not a.dry_run:
            raise SystemExit("OPENAI_API_KEY가 없다 — 저장소 루트 .env에 넣고 docker compose up -d api (또는 CODING_GEN_PROVIDER=ollama)")
        out = []
        t_all = time.time()
        for sp in specs:                                # 한 문제씩 끝날 때마다 결과를 출력(긴 작업 진행 확인용)
            r = generator_agent.invoke([{"spec": {**sp, "review": a.review}, "dry_run": a.dry_run}])[0]
            print(json.dumps(r, ensure_ascii=False, default=str), flush=True)
            out.append(r)
        ok = [r for r in out if r["verified"]]
        print(json.dumps({"summary": True, "model": sorted({r["model"] for r in out}), "total": len(out), "verified": len(ok),
                          "published": sum(1 for r in out if r.get("published_problem_id")),
                          "total_sec": round(time.time() - t_all, 1),
                          "avg_sec_per_problem": round((time.time() - t_all) / max(1, len(out)), 1),
                          "prompt_tokens": sum(r["cost"]["prompt_tokens"] for r in out),
                          "completion_tokens": sum(r["cost"]["completion_tokens"] for r in out)}, ensure_ascii=False), flush=True)
        return
    out = approve(a.ids, a.reject) if a.cmd == "approve" else stats()
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    sys.exit(main())
