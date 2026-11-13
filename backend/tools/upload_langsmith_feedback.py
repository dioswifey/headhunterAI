"""로컬 판정 점수를 LangSmith feedback으로 올린다 (PRD v0.13 6.3.9 · AC-96, HANDOFF 23-3 ⑩, 2026-11-13).

LangSmith 온라인 평가기(외부 LLM 판정)는 쓰지 않는다. 이 스크립트는 **이미 계산된 숫자만** 올린다:
  - eval_rag_questions report 결과 JSON  (--kind rag)      → M-31 · M-32 · M-33 · M-36 + 집계 M-31~37
  - eval_reports report 결과 JSON        (--kind report)   → M-38 · M-39 · M-40 · M-41 · M-42 + 집계 M-38~44
  - eval_followup report 결과 JSON       (--kind followup) → EV-55 · EV-56 · M-50 + 집계

feedback key = 지표 ID(예: M-33), score = 숫자, comment = 판정 이유 요약(짧게 자름).
항목에 LangSmith run ID(`langsmith_run_id` 또는 `run_id`)가 있으면 그 run에 붙이고, 없으면 실험용 프로젝트
`headhunter-eval-<kind>-<파일 이름>`을 만들어 항목마다 run 1개(+ 집계 run `metrics-summary`)를 만든 뒤 붙인다.
run · feedback ID는 (프로젝트, 항목) 기준 uuid5라서 같은 파일을 두 번 올리면 같은 ID가 된다 — 프로젝트가 이미 있으면 멈춘다.

안전장치
  - 기본은 --dry-run(무엇을 올릴지 출력만, 네트워크·langsmith import 없음). 실제 전송은 --apply.
  - --apply는 환경 변수 DEV_MODE=1(명시적으로) + LANGSMITH_API_KEY가 있을 때만. 키는 환경 변수에서만 읽고 출력하지 않는다.
  - 가상 데이터 전용: rag 결과는 모든 세트의 이력서 이름이 '가상'으로 시작해야 한다. 지원자 답변 원문은 올리지 않는다.
  - 질문 문장(가상 생성물)은 --include-text 를 줄 때만 run 출력에 넣는다.
  - 제품 코드를 부르지 않는다(app.* import 없음).

    cd backend
    python -m tools.upload_langsmith_feedback ../documents/03_QA_doc/eval_runs/rag_questions_2026-11-12.json --kind rag
    python -m tools.upload_langsmith_feedback ../documents/03_QA_doc/eval_runs/reports_2026-11-12.json --kind report --show 5
    DEV_MODE=1 LANGSMITH_API_KEY=... python -m tools.upload_langsmith_feedback <json> --kind rag --apply
"""
import argparse
import datetime
import json
import os
import sys
import uuid

NS = uuid.UUID("6f1d2c3a-9b7e-4c55-8a10-4e1f0b6a7d21")   # 이 스크립트 전용 uuid5 이름공간
COMMENT_MAX = 300


def _clip(s, n=COMMENT_MAX):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _fb(key, score, comment="", **extra):
    return {"key": key, "score": score, "comment": _clip(comment), **({"extra": extra} if extra else {})}


def _item(name, feedback, run_id=None, inputs=None, outputs=None):
    return {"name": name, "run_id": run_id, "inputs": inputs or {}, "outputs": outputs or {}, "feedback": [f for f in feedback if f["score"] is not None]}


def _rid(it):
    return it.get("langsmith_run_id") or it.get("run_id")


# ---------------- 집계 값 꺼내기 ----------------

def _agg_value(m):
    """지표 dict에서 대표 값 하나. M-31은 redis 평균, M-36은 관련도 평균."""
    if not isinstance(m, dict):
        return None
    for sub in ("redis", "relevance"):
        if isinstance(m.get(sub), dict) and _num(m[sub].get("mean")):
            return m[sub]["mean"]
    for k in ("value", "mean", "p"):
        if _num(m.get(k)):
            return m[k]
    return None


def _summary(metrics, src):
    """metrics: {id: dict} 또는 [dict(id=...)] → 집계 run 1개."""
    if isinstance(metrics, list):
        metrics = {m.get("id"): m for m in metrics if m.get("id")}
    fbs = []
    for mid, m in metrics.items():
        v = _agg_value(m)
        if v is None:
            continue
        ci = m.get("ci95") or m.get("ci") or (m.get("redis") or {}).get("ci95") or m.get("wilson95")
        note = f"목표 {m.get('target', '-')} · 판정 {'충족' if m.get('pass') else ('미달' if m.get('pass') is False else '-')}"
        if ci:
            note += f" · 95% CI {ci}"
        if m.get("n") is not None:
            note += f" · n={m['n']}"
        fbs.append(_fb(mid, v, note, aggregate=True, source=src))
    return _item("metrics-summary", fbs, inputs={"kind": "aggregate", "source": src})


# ---------------- kind별 매핑 ----------------

def map_rag(d, src, include_text):
    sets = d.get("sets") or []
    bad = [s.get("resume") for s in sets if not str(s.get("resume") or "").startswith("가상")]
    items = []
    for q in d.get("questions") or []:
        j = q.get("judge") or {}
        fb = [_fb("M-33", q.get("rubric_mean"), j.get("reason", "")),
              _fb("M-32", (1 if q["grounded_final"] else 0) if q.get("grounded_final") is not None else None,
                  "근거 없음: " + ", ".join(map(str, j.get("ungrounded_facts") or [])) if q.get("grounded_final") is False else "근거 있음")]
        out = {"gen_method": q.get("gen_method"), "fallback": q.get("fallback"), "passed": q.get("passed"),
               "fail_reasons": q.get("fail_reasons")}
        if include_text:
            out["text"] = q.get("text")
        items.append(_item(q["qid"], fb, _rid(q),
                           {"qid": q["qid"], "set_id": q.get("set_id"), "role_code": q.get("role_code"), "category": q.get("category")}, out))
    for key, r in (d.get("retrieval") or {}).items():
        items.append(_item(key, [_fb("M-31", r.get("relevance"), r.get("reason", ""))], _rid(r),
                           {"set_id": r.get("set_id"), "category": r.get("key"), "source": r.get("source")},
                           {"sim": r.get("sim")}))
    for i, r in enumerate(d.get("internal") or [], 1):
        hits = r.get("hits") or []
        rel = [h["relevance"] for h in hits if _num(h.get("relevance"))]
        leaks = sum(1 for h in hits if h.get("leak"))
        why = next((h.get("reason") for h in hits if h.get("reason")), "")
        items.append(_item(f"internal-{i:02d}", [_fb("M-36", round(sum(rel) / len(rel), 4) if rel else None,
                                                      f"누출 {leaks}건 · 조각 {len(hits)}개 · {why}", leaks=leaks)], _rid(r),
                           {"role": r.get("role"), "kind": r.get("kind"), "adversarial": r.get("adversarial")},
                           {"n_hits": r.get("n_hits"), "leaks": leaks}))
    items.append(_summary(d.get("metrics") or {}, src))
    meta = d.get("meta") or {}
    return items, {"judge_model": meta.get("judge_model"), "gen_model": meta.get("gen_model"), "date": meta.get("date"),
                   "prompt_version": meta.get("prompt_version")}, bad


def map_report(d, src, include_text):
    items = []
    for r in d.get("rows") or []:
        j = r.get("judge") or {}
        reasons = j.get("reasons") or {}
        cons = r.get("consistency") or []
        quotes = r.get("a4_quotes") or []
        forb = r.get("forbidden") or []
        tmpl = None if r.get("coach_template") is None and r.get("summary_template") is None else \
            (1 if (r.get("coach_template") or r.get("summary_template")) else 0)
        fb = [
            _fb("M-39", r.get("judge_mean"), " / ".join(f"{k}: {v}" for k, v in reasons.items())[:COMMENT_MAX]),
            _fb("M-40", round(sum(1 for c in cons if c.get("ok")) / len(cons), 4) if cons else None,
                f"점수-서술 일치 {sum(1 for c in cons if c.get('ok'))}/{len(cons)}문장(코드 검사)"),
            _fb("M-38", round(sum(1 for c in quotes if c.get("ok")) / len(quotes), 4) if quotes else None,
                f"인용 부분 문자열 {sum(1 for c in quotes if c.get('ok'))}/{len(quotes)}"),
            _fb("M-41", len(forb) if r.get("ok") else None, "; ".join(f"{f.get('type')} '{f.get('text')}'" for f in forb) or "0건"),
            _fb("M-42", tmpl, f"코치 템플릿 {r.get('coach_template')} · 총평 템플릿 {r.get('summary_template')}"),
        ]
        items.append(_item(r["id"], fb, _rid(r), {"id": r["id"], "role": r.get("role"), "level": r.get("level")},
                           {"ok": r.get("ok"), "interview_score": r.get("interview_score"), "item_scores": r.get("item_scores")}))
    items.append(_summary(d.get("metrics") or [], src))
    return items, {"judge_model": d.get("judge_model"), "gen_model": d.get("pipeline_model"), "date": d.get("date"),
                   "judge_prompt": d.get("judge_prompt")}, []


def map_followup(d, src, include_text):
    judged = d.get("judged") or {}
    items = []
    for r in d.get("rows") or []:
        j = judged.get(r["id"]) or {}
        sc = j.get("scores") if j.get("ok") else None
        fb = [
            _fb("EV-55", (1 if r.get("proposed") == r.get("label") else 0) if r.get("label") is not None else None,
                f"라벨 {r.get('label')} · 판단 {r.get('proposed')} · 종류 {r.get('kind')}"),
            _fb("EV-56", round(sum(sc.values()) / len(sc), 4) if sc else None, j.get("reason", "")),
            _fb("M-50", r.get("llm_sec") if _num(r.get("llm_sec")) else None, "A3 판단 지연(초, 개발 PC 참고값)"),
        ]
        out = {"proposed": r.get("proposed"), "label": r.get("label")}
        if include_text:
            out["followup"] = r.get("followup")
        items.append(_item(r["id"], fb, _rid(r), {"id": r["id"], "kind": r.get("kind")}, out))
    items.append(_summary(d.get("metrics") or {}, src))
    return items, {"judge_model": d.get("judge_model"), "gen_model": d.get("llm_model"), "date": d.get("date")}, []


MAPPERS = {"rag": map_rag, "report": map_report, "followup": map_followup}
# --kind가 파일과 맞는지: 꼭 있어야 하는 최상위 키 / 첫 행에 있어야 하는 키
SHAPE = {"rag": (("questions", "retrieval", "metrics"), "questions", "rubric_mean"),
         "report": (("rows", "metrics", "judge_model"), "rows", "judge_mean"),
         "followup": (("rows", "judged", "metrics"), "rows", "label")}


def _check_shape(d, kind):
    top, rows_key, row_field = SHAPE[kind]
    miss = [k for k in top if k not in d]
    rows = d.get(rows_key) or []
    if miss or not rows or row_field not in rows[0]:
        raise SystemExit(f"--kind {kind} 형식이 아니다(없는 키: {miss or row_field}) — eval_* report 결과 JSON과 --kind를 확인")


# ---------------- 실행 ----------------

def gate():
    """--apply 허용 여부. (ok, 이유) — 키 값은 절대 돌려주지 않는다."""
    why = []
    if os.environ.get("DEV_MODE") != "1":
        why.append("DEV_MODE=1 이 환경 변수에 명시돼 있지 않음")
    if not os.environ.get("LANGSMITH_API_KEY"):
        why.append("LANGSMITH_API_KEY 없음")
    ep = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    if not ep.startswith("https://"):
        why.append("LANGSMITH_ENDPOINT가 https가 아님")
    return not why, why


def plan(path, kind, include_text):
    d = json.load(open(path, encoding="utf-8"))
    src = os.path.splitext(os.path.basename(path))[0]
    _check_shape(d, kind)
    items, meta, bad = MAPPERS[kind](d, src, include_text)
    if not items or (len(items) == 1 and items[0]["name"] == "metrics-summary" and not items[0]["feedback"]):
        raise SystemExit(f"'{kind}' 형식으로 읽은 항목이 없다 — --kind가 맞는지 확인")
    project = f"headhunter-eval-{kind}-{src}"[:120]
    return items, meta, bad, project


def apply(items, meta, project, kind, src):
    from langsmith import Client          # 지연 import — dry-run은 langsmith 없이도 돈다
    client = Client(api_key=os.environ["LANGSMITH_API_KEY"],
                    api_url=os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"))
    need_runs = [it for it in items if not it["run_id"]]
    if need_runs:
        if client.has_project(project):
            raise SystemExit(f"프로젝트 '{project}'가 이미 있다 — 같은 결과를 두 번 올리지 않는다(LangSmith에서 지우거나 파일 이름을 바꿔라)")
        client.create_project(project, description=f"로컬 판정 점수 업로드({kind}, {src}). 온라인 평가기 미사용 — PRD 6.3.9",
                              metadata={"kind": kind, "source_file": src, "synthetic": True, **{k: v for k, v in meta.items() if v}})
        now = datetime.datetime.now(datetime.timezone.utc)
        for it in need_runs:
            it["run_id"] = str(uuid.uuid5(NS, f"{project}/{it['name']}"))
            client.create_run(id=it["run_id"], project_name=project, name=it["name"], run_type="chain",
                              inputs=it["inputs"], outputs=it["outputs"], start_time=now, end_time=now,
                              extra={"metadata": {"synthetic": True, "source_file": src, "kind": kind}},
                              tags=["local-judge", kind])
        client.flush()                    # run이 먼저 들어가야 feedback이 붙는다
    sent = 0
    for it in items:
        for f in it["feedback"]:
            client.create_feedback(it["run_id"], f["key"], score=f["score"], comment=f["comment"] or None,
                                   feedback_id=uuid.uuid5(NS, f"{project}/{it['name']}/{f['key']}"),
                                   source_info={"origin": "local", "judge_model": meta.get("judge_model"), "source_file": src,
                                                **(f.get("extra") or {})})
            sent += 1
    client.flush()
    return sent


def main(argv=None):
    p = argparse.ArgumentParser(description="로컬 판정 점수 → LangSmith feedback (기본 dry-run)")
    p.add_argument("json", help="평가 결과 JSON (eval_* report 산출물)")
    p.add_argument("--kind", required=True, choices=sorted(MAPPERS))
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="apply", action="store_false", default=False, help="올릴 내용만 출력(기본)")
    g.add_argument("--apply", dest="apply", action="store_true", help="실제로 LangSmith에 보낸다")
    p.add_argument("--show", type=int, default=3, help="dry-run에서 예시로 보여 줄 항목 수")
    p.add_argument("--include-text", action="store_true", help="가상 질문·꼬리질문 문장을 run 출력에 넣는다")
    p.add_argument("--plan-out", help="업로드 계획 전체를 JSON으로 저장(선택)")
    a = p.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    items, meta, bad, project = plan(a.json, a.kind, a.include_text)
    src = os.path.splitext(os.path.basename(a.json))[0]
    by_key = {}
    for it in items:
        for f in it["feedback"]:
            by_key.setdefault(f["key"], []).append(f["score"])
    n_fb = sum(len(v) for v in by_key.values())
    n_new = sum(1 for it in items if not it["run_id"])

    print(f"[{'APPLY' if a.apply else 'DRY-RUN'}] {a.kind} · {src}")
    print(f"  프로젝트: {project} (새 run {n_new}개 · 기존 run ID 사용 {len(items) - n_new}개)")
    print(f"  판정 모델: {meta.get('judge_model')} · 날짜: {meta.get('date')}")
    print(f"  feedback 합계 {n_fb}건")
    for k in sorted(by_key):
        v = [x for x in by_key[k] if _num(x)]
        print(f"    {k:6s} n={len(by_key[k]):3d}  평균 {round(sum(v) / len(v), 4) if v else '-'}  최소 {min(v) if v else '-'}  최대 {max(v) if v else '-'}")
    for it in items[: a.show] + [x for x in items if x["name"] == "metrics-summary"]:
        print(f"  - {it['name']}  inputs={json.dumps(it['inputs'], ensure_ascii=False)}")
        for f in it["feedback"]:
            print(f"      {f['key']} = {f['score']}  # {f['comment'][:120]}")
    if a.plan_out:
        json.dump({"project": project, "meta": meta, "items": items}, open(a.plan_out, "w", encoding="utf-8", newline="\n"),
                  ensure_ascii=False, indent=1)
        print(f"  계획 저장: {a.plan_out}")

    ok, why = gate()
    if bad:
        ok, why = False, why + [f"가상 데이터가 아닌 이력서 이름 {len(bad)}개: {bad[:3]}"]
    if not a.apply:
        print(f"  --apply 가능 여부: {'가능' if ok else '불가 — ' + '; '.join(why)}")
        print("  (dry-run: 아무것도 보내지 않음)")
        return 0
    if not ok:
        print("거부: " + "; ".join(why), file=sys.stderr)
        return 2
    sent = apply(items, meta, project, a.kind, src)
    print(f"  보냄: feedback {sent}건 → 프로젝트 {project}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
