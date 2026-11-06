"""SQL 문제 생성·검증 에이전트 A12 (2026-11-06, SQLD 유형 4지선다, ADLC v0.4 6.12).

입력 = 출제 명세(agents/sql_scope.plan: 공식 과목 비율 × 세부항목 비율(네이버 검색 API 통계 50% + 균등 50%)).
외부 문제 문장·블로그 원문은 생성 입력에 넣지 않는다(통계 숫자만).

    ① 생성      OpenAI(SQL_GEN_MODEL, 코딩 문제은행 생성 예외와 같은 규칙: 지원자 데이터 없음) 1회
    ② 검증      S1 형식 · S2 SQL 금지 문장 · S3 실행(격리 DB sqlbank, 항상 ROLLBACK) · S4 정답 계산·대조
                · S5 보기 서로 다름 · S6 로컬 Qwen 블라인드 풀이(정답 일치·모호함) · S7 중복
    ③ 수정      실패 이유를 넘겨 최대 2번 → 그래도 실패면 on_hold(게시 안 함)
    ④ 저장      sql_problems(status published | on_hold)

    python -m app.agents.sql_generator_agent generate --n 60 [--subject 1|2] [--first 5] [--dry-run]
    python -m app.agents.sql_generator_agent stats
"""
import argparse
import json
import re
import sys
import time

from .. import config, db, llm, prompts, sqlbank
from .sql_scope import OFFICIAL, plan

MAX_REPAIRS = 2
SUBJECT_NAME = {s["no"]: s["name"] for s in OFFICIAL["subjects"]}
EXEC_ITEMS = {"SELECT 문", "함수", "WHERE 절", "GROUP BY·HAVING 절", "ORDER BY 절", "조인", "표준 조인", "서브 쿼리", "집합 연산자",
              "그룹 함수", "윈도우 함수", "Top N 쿼리", "계층형 질의와 셀프 조인", "정규 표현식", "DML", "DDL"}
KIND_DESC = {"concept": "개념·이론 4지선다(보기는 문장)",
             "result": "표와 SQL을 보여 주고 '실행 결과로 옳은 것은?'(보기는 실행기가 만든 결과 표)",
             "sql_pick": "요구 결과를 말로 주고 보기 4개가 SQL(요구를 만족하는/만족하지 않는 SQL 고르기)"}
ANGLES = ["가장 기본이 되는 정의·동작", "NULL이 섞였을 때의 동작", "자주 헷갈리는 두 개념의 차이", "실행 순서·적용 시점", "예외적인 경계 상황",
          "실무 상황(주문·회원·사원 등)에 적용", "결과 행 수·값 계산", "다른 문법과의 동등성"]
S = lambda n: {"type": "string", "maxLength": n}   # noqa: E731
GEN_SCHEMA = {"type": "object", "properties": {
    "title": S(60), "stem": S(1500), "requirement": S(600), "setup_sql": S(4000), "query_sql": S(2000),
    "reference_sql": S(2000), "distractor_sqls": {"type": "array", "items": S(2000)}, "options_sql": {"type": "array", "items": S(1500)},
    "mode": S(10), "choices_text": {"type": "array", "items": S(400)}, "answer": {"type": "integer"}, "explanation": S(2500)},
    "required": ["title", "stem", "setup_sql", "query_sql", "choices_text", "answer", "explanation"]}
SOLVE_SCHEMA = {"type": "object", "properties": {"answer": {"type": "integer"}, "reason": S(400), "ambiguous": {"type": "boolean"},
                                                 "issues": {"type": "array", "items": S(200)}},
                "required": ["answer", "reason", "ambiguous", "issues"]}


def kind_for(sp):
    if sp["subject"] == 1 or sp["item"] not in EXEC_ITEMS:
        return "concept"
    return "result" if sp["variant"] % 2 == 0 else "sql_pick"


def split_sql(text):
    """세미콜론으로 문장 나누기(따옴표 안의 ; 무시)."""
    out, buf, q = [], [], False
    for ch in text or "":
        if ch == "'":
            q = not q
        if ch == ";" and not q:
            if "".join(buf).strip():
                out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def _model():
    return config.SQL_GEN_MODEL or config.OPENAI_MODEL


def _gen(x):
    sp = x["spec"]
    system = prompts.load("generate/sql_problem_generator", subject_no=sp["subject"], subject_name=SUBJECT_NAME[sp["subject"]],
                          area=sp["area"], item=sp["item"], kind=x["kind"], kind_desc=KIND_DESC[x["kind"]], variant=sp["variant"] + 1,
                          angle=ANGLES[sp["variant"] % len(ANGLES)],
                          feedback=("\n[이전 초안의 실패 이유 — 반드시 고칠 것]\n" + x["feedback"]) if x.get("feedback") else "")
    user = "출제 명세대로 문제 1개를 JSON으로."
    if x.get("draft"):
        user += "\n[이전 초안]\n" + json.dumps(x["draft"], ensure_ascii=False)[:9000]
    try:
        out, usage = llm.openai_chat_json(system, user, GEN_SCHEMA, timeout=600, max_tokens=20000, model=_model())
    except llm.LLMError as e:
        if e.usage:
            x["usage"].append({**e.usage, "failed": True})
        raise
    x["usage"].append(usage)
    return out


# ---------------- 검증 ----------------
def _choice_key(c):
    return json.dumps(c, ensure_ascii=False, sort_keys=True)


def verify(x):
    """초안 → (ok, 이유 목록, 게시용 dict)."""
    d, kind, log = x["draft"], x["kind"], x["log"]
    errs = []
    title, stem, expl = (d.get("title") or "").strip(), (d.get("stem") or "").strip(), (d.get("explanation") or "").strip()
    if not re.search(r"[가-힣]", title) or len(title) > 40:
        errs.append("S1 제목은 한국어 40자 이하")
    if len(stem) < 15 or len(expl) < 60:
        errs.append("S1 문제 문장 또는 해설이 너무 짧음")
    if re.search(r"출제 명세|kind|변형|세부항목", stem + title):
        errs.append("S1 문제 문장에 출제 지시어가 들어 있음")
    if kind != "concept" and (re.search(r"(?i)\bSELECT\b|\bINSERT\b|\bCREATE\b", stem) or stem.count("|") >= 2):
        errs.append("S1 stem에 표 데이터나 SQL이 들어 있음 — stem에는 질문 문장만(표·SQL은 화면이 따로 보여 줌)")
    if kind == "result":
        miss = [m for m in ("[정답]", "[오답1]", "[오답2]", "[오답3]") if m not in expl]
        if miss:
            errs.append(f"S1 result 해설에 표시 {miss}가 없음 — 번호 대신 [정답]·[오답1~3]으로")
    pub = {"title": title, "stem": stem, "explanation": expl, "setup_sql": "", "query_sql": "", "tables": {}, "hidden": {}}
    if kind == "concept":
        ch = [c.strip() for c in d.get("choices_text") or []]
        if len(ch) != 4 or len(set(ch)) != 4 or not all(ch):
            errs.append("S1 보기는 서로 다른 문장 4개")
        if d.get("answer") not in (1, 2, 3, 4):
            errs.append("S1 answer는 1~4")
        pub["choices"], pub["answer"] = [{"type": "text", "text": c} for c in ch], d.get("answer")
    else:
        setup = d.get("setup_sql") or ""
        for s in [setup, d.get("query_sql"), d.get("reference_sql"), *(d.get("distractor_sqls") or []), *(d.get("options_sql") or [])]:
            bad = sqlbank.check(s or "")
            if bad:
                errs.append("S2 " + bad)
        if errs:
            return False, errs, pub
        base = sqlbank.run(setup, [], snapshot=True)
        if base["error"]:
            return False, [f"S3 setup_sql 실행 오류: {base['error']}"], pub
        if not base["tables"] or any(len(t["rows"]) > 10 for t in base["tables"].values()):
            errs.append("S3 표가 없거나 표 하나가 10행 초과")
        pub["setup_sql"], pub["tables"] = setup, base["tables"]
        if kind == "result":
            steps = split_sql(d.get("query_sql"))
            if not steps or not re.match(r"(?is)^\s*(WITH|SELECT)\b", steps[-1]):
                return False, errs + ["S3 query_sql의 마지막 문장은 SELECT"], pub
            right = sqlbank.run(setup, steps)
            if right["error"] or not right["result"]:
                return False, errs + [f"S3 query_sql 실행 오류: {right['error'] or '결과 없음'}"], pub
            r = right["result"]
            if len(r["rows"]) > 1 and not re.search(r"(?is)ORDER\s+BY", steps[-1]):
                errs.append("S4 여러 행 결과인데 ORDER BY가 없음(행 순서가 정해지지 않음)")
            if len(r["rows"]) > 8 or len(r["columns"]) > 5:
                errs.append("S4 결과가 8행·5열을 넘음")
            wrong = []
            for i, ds in enumerate((d.get("distractor_sqls") or [])[:3]):
                w = sqlbank.run(setup, split_sql(ds))
                if w["error"] or not w["result"]:
                    errs.append(f"S4 오답 SQL {i + 1} 실행 오류: {w['error'] or '결과 없음'}")
                    continue
                wrong.append(w["result"])
            if len(d.get("distractor_sqls") or []) < 3:
                errs.append("S4 오답 SQL 3개 필요")
            tables = [r] + wrong
            keys = [_choice_key(t) for t in tables]
            if len(set(keys)) != len(keys):
                errs.append("S5 오답 SQL 결과가 정답 또는 서로와 같음 — 결과가 달라지는 실수로 바꿀 것")
            # 정답 위치는 제목 해시로 고정 배치(재현 가능)
            pos = (sum(map(ord, title)) % 4)
            order = wrong[:pos] + [r] + wrong[pos:]
            pub["choices"] = [{"type": "table", **t} for t in order[:4]]
            pub["answer"] = pos + 1
            nums = "①②③④"
            where = {"[정답]": pos}
            for k in range(3):
                where[f"[오답{k + 1}]"] = k if k < pos else k + 1
            for mark, idx in where.items():                    # 실행기가 정한 위치로 해설 번호를 바꾼다
                expl = expl.replace(mark, nums[idx])
            pub["explanation"] = expl
            pub["query_sql"] = d.get("query_sql").strip()
            pub["hidden"] = {"distractor_sqls": d.get("distractor_sqls")}
        else:  # sql_pick
            opts, mode = d.get("options_sql") or [], (d.get("mode") or "").strip()
            if len(opts) != 4 or mode not in ("match", "mismatch"):
                return False, errs + ["S1 options_sql 4개와 mode(match|mismatch) 필요"], pub
            ref = sqlbank.run(setup, split_sql(d.get("reference_sql")))
            if ref["error"] or not ref["result"]:
                return False, errs + [f"S3 reference_sql 실행 오류: {ref['error'] or '결과 없음'}"], pub
            ordered = bool(re.search(r"(?is)ORDER\s+BY", d.get("reference_sql") or ""))
            hits = []
            for o in opts:
                ro = sqlbank.run(setup, split_sql(o))
                hits.append(bool(ro["result"]) and not ro["error"] and sqlbank.same(ref["result"], ro["result"], ordered=ordered))
            target = [i + 1 for i, h in enumerate(hits) if h == (mode == "match")]
            log.append({"step": "S4_options", "matches": hits, "mode": mode})
            if len(target) != 1:
                errs.append(f"S4 실행 결과 기준으로 {'만족하는' if mode == 'match' else '만족하지 않는'} 보기가 {len(target)}개(정확히 1개여야 함). "
                            f"보기별 기준 결과 일치 여부: {hits}")
            elif target[0] != d.get("answer"):
                errs.append(f"S4 실행 결과로 계산한 정답은 {target[0]}번인데 answer는 {d.get('answer')}")
            if len({o.strip() for o in opts}) != 4:
                errs.append("S5 보기 SQL이 서로 같음")
            pub["choices"] = [{"type": "sql", "sql": o.strip()} for o in opts]
            pub["answer"] = target[0] if len(target) == 1 else d.get("answer")
            pub["hidden"] = {"reference_sql": d.get("reference_sql"), "mode": mode, "requirement": d.get("requirement")}
    if errs:
        return False, errs, pub
    # S6 로컬 Qwen 블라인드 풀이: 개념 문제는 정답 일치까지, 실행형은 모호함만(정답은 실행기가 이미 계산)
    view = render_text(pub)
    try:
        r = llm.chat_json(prompts.load("evaluate/sql_problem_solve"), view + "\n/no_think", SOLVE_SCHEMA, timeout=600, temperature=0.0,
                          model=config.CODING_GEN_MODEL, max_tokens=600, num_ctx=8192)
        log.append({"step": "S6_solve", "answer": r["answer"], "ambiguous": r["ambiguous"], "issues": r.get("issues")})
        if kind == "concept" and r["answer"] != pub["answer"]:
            errs.append(f"S6 로컬 검토 모델이 {r['answer']}번을 정답으로 고름(선언된 정답 {pub['answer']}번). 검토 근거: {r['reason']} "
                        "— 정답이 하나로 분명한지, 오답이 정말 틀렸는지 다시 확인할 것")
        if r["ambiguous"]:
            errs.append("S6 모호함: " + "; ".join(r.get("issues") or []))
    except llm.LLMError as e:
        errs.append(f"S6 로컬 검토 모델 호출 실패: {e}")
    # S7 중복: 같은 세부항목의 게시 문제와 '내용'(문장·표 SQL·문제 SQL·보기) 3글자 Jaccard > 0.5
    #   (문제 문장만 비교하면 "실행 결과로 옳은 것은?" 같은 정형 문장 때문에 늘 비슷하게 나옴 — 2026-11-06)
    mine = _content(pub)
    for o in db.all_("SELECT * FROM sql_problems WHERE item=%s AND status='published'", x["spec"]["item"]):
        sim = _tri(_content(o), mine)
        if sim > 0.5:
            errs.append(f"S7 기존 문제 {o['id']}번과 내용이 너무 비슷함(겹침 {sim:.2f}) — 표·데이터·SQL·묻는 개념을 바꿀 것")
            break
    return not errs, errs, pub


def _content(p):
    ch = " ".join(c.get("text") or c.get("sql") or json.dumps(c.get("rows"), ensure_ascii=False) for c in p.get("choices") or [])
    return " ".join([p.get("stem") or "", p.get("setup_sql") or "", p.get("query_sql") or "", ch])


def _tri(a, b):
    g = lambda s: {s[i:i + 3] for i in range(len(s) - 2)}  # noqa: E731
    A, B = g(re.sub(r"\s+", "", a.upper())), g(re.sub(r"\s+", "", b.upper()))
    return len(A & B) / max(1, len(A | B))


def _tbl_text(name, t):
    return f"[{name}]\n" + " | ".join(t["columns"]) + "\n" + "\n".join(" | ".join("NULL" if v is None else v for v in r) for r in t["rows"])


def render_text(p):
    parts = [p["stem"]]
    for n, t in (p.get("tables") or {}).items():
        parts.append(_tbl_text(n, t))
    if p.get("query_sql"):
        parts.append("[SQL]\n" + p["query_sql"])
    for i, c in enumerate(p.get("choices") or []):
        body = c.get("text") or c.get("sql") or _tbl_text("결과", c)
        parts.append(f"{'①②③④'[i]} {body}")
    return "\n\n".join(parts)


def run_spec(sp, dry_run=False):
    t0 = time.time()
    x = {"spec": sp, "kind": kind_for(sp), "usage": [], "log": [], "feedback": "", "draft": None}
    reasons, pub, ok = [], None, False
    for attempt in range(1, MAX_REPAIRS + 2):
        try:
            x["draft"] = _gen(x)
        except llm.LLMError as e:
            reasons.append(f"[시도 {attempt}] 생성 실패: {e}")
            x["log"].append({"attempt": attempt, "step": "generate", "ok": False, "reason": str(e)[:300]})
            continue
        ok, errs, pub = verify(x)
        x["log"].append({"attempt": attempt, "step": "verify", "ok": ok, "reasons": errs})
        print(f"[진행] {sp['subject']}과목 {sp['item']} · 시도 {attempt} · {'OK' if ok else 'FAIL ' + '; '.join(errs)[:200]}", file=sys.stderr, flush=True)
        if ok:
            break
        reasons += [f"[시도 {attempt}] {e}" for e in errs]
        x["feedback"] = "\n".join("- " + e for e in errs)
    usage = {"calls": len(x["usage"]), "prompt_tokens": sum(u["prompt_tokens"] for u in x["usage"]),
             "completion_tokens": sum(u["completion_tokens"] for u in x["usage"]), "model": _model()}
    out = {"spec": f"{sp['subject']}과목 / {sp['item']} / {x['kind']}", "verified": ok, "attempts": len([l for l in x["log"] if l.get("attempt")]),
           "elapsed_sec": round(time.time() - t0), "usage": usage, "fail_reasons": [] if ok else reasons[-4:]}
    if dry_run or not pub or not pub.get("choices") or pub.get("answer") not in (1, 2, 3, 4):
        out["stored"] = False
        return out
    row = db.one("""INSERT INTO sql_problems (quality_status, status, subject, area, item, kind, title, stem, setup_sql, query_sql, tables,
                    choices, answer, explanation, hidden, generation_model, prompt_version, generation_log, verification_log, usage, elapsed_sec)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                 "verified" if ok else "on_hold", "published" if ok else "on_hold", sp["subject"], sp["area"], sp["item"], x["kind"],
                 pub["title"] or "(제목 없음)", pub["stem"], pub["setup_sql"], pub["query_sql"], json.dumps(pub["tables"], ensure_ascii=False),
                 json.dumps(pub["choices"], ensure_ascii=False), pub["answer"], pub["explanation"], json.dumps(pub["hidden"], ensure_ascii=False),
                 _model(), config.VERSIONS["prompt"], json.dumps([{"usage": x["usage"]}], ensure_ascii=False),
                 json.dumps(x["log"], ensure_ascii=False), json.dumps(usage), out["elapsed_sec"])
    out["id"], out["stored"] = row["id"], True
    return out


def stats():
    return db.all_("SELECT subject, kind, status, count(*) FROM sql_problems GROUP BY 1,2,3 ORDER BY 1,2,3")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sql_generator_agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--n", type=int, default=60)
    g.add_argument("--subject", type=int, choices=[1, 2])
    g.add_argument("--first", type=int, default=0, help="명세 중 앞에서 몇 개만(시험 실행)")
    g.add_argument("--pick", default="", help="쉼표로 명세 번호 고르기")
    g.add_argument("--dry-run", action="store_true")
    sub.add_parser("stats")
    a = ap.parse_args(argv)
    db.pool.open()
    if a.cmd == "stats":
        for r in stats():
            print(r)
        return
    specs = plan(a.n)
    if a.subject:
        specs = [s for s in specs if s["subject"] == a.subject]
    if a.pick:
        specs = [specs[int(i)] for i in a.pick.split(",")]
    if a.first:
        specs = specs[: a.first]
    t0, res = time.time(), []
    for sp in specs:
        r = run_spec(sp, a.dry_run)
        res.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
    print(json.dumps({"summary": True, "model": _model(), "total": len(res), "verified": sum(r["verified"] for r in res),
                      "by_subject": {n: f"{sum(r['verified'] for r in res if r['spec'].startswith(str(n)))}/"
                                        f"{sum(1 for r in res if r['spec'].startswith(str(n)))}" for n in (1, 2)},
                      "total_sec": round(time.time() - t0), "prompt_tokens": sum(r["usage"]["prompt_tokens"] for r in res),
                      "completion_tokens": sum(r["usage"]["completion_tokens"] for r in res)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.exit(main())
