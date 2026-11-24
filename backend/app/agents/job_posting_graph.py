"""그래프 3 — 새 공고 RAG 재생성 (LangGraph, 2026-11-12 사용자 지시). 개발 도구, 제품 실행 경로 아님.

새 원문 공고가 `job_posting_resources/`에 들어오면 이 그래프 한 번으로 전체 단계를 돈다.

  (0단계, 그래프 밖·호스트) backend/tools/collect_jobs.py --site saramin|wanted  → job_posting_resources/<site>/*.json
      Scrapling은 호스트 가상환경에만 있고 Chroma·LangGraph는 api 컨테이너에만 있어서 수집은 그래프 밖에 둔다.

  START → scan ─(새·바뀐 공고 없음, force 아님)→ END
               └→ ingest → select_targets ─(대상 없음)→ report → END
                                           └→ regenerate → report → END

- scan            원문 정규화(공통 형식) + Chroma `job_postings_ref`의 저장 해시와 비교 → 새·바뀐 공고
- ingest          바뀐 공고만 조각 → llm.embed(qwen3-embedding:0.6b) → 문서째 교체
- select_targets  새 공고가 속한 직무의 시드 공고(헤드헌터랩_서울·경기·인천 게시 공고). force면 전체 직무
- regenerate      시드 공고마다 같은 직무 참고 조각 k개 검색 → llm.chat_json 초안 → 문장·전체 유사성 검사
- report          documents/03_QA_doc/eval_runs/<name>.json·.md (원문 본문 없이 출처 id·URL·유사도만)

규칙: 모델 호출은 llm.chat_json·llm.embed만(외부 API 없음). DB에는 쓰지 않는다(공고는 기업이 확정, PRD JOB-08~10).
사내문서 컬렉션은 열지 않는다. 체크포인터 없음(같은 원문으로 다시 돌리면 scan이 변경 0건 → 바로 끝).

실행(컨테이너 안):
    docker exec -w /srv headhunterAI-api python -m app.agents.job_posting_graph run --out /tmp/regen
    ... run --force        # 새 공고가 없어도 전체 재생성
    ... run --dry-run      # scan만 하고 무엇이 바뀌었는지 출력
"""
import argparse
import datetime as dt
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from . import job_posting_regen as R


class JobRegenState(TypedDict, total=False):
    raw_dir: str
    persist_dir: str | None
    out_dir: str
    name: str
    k: int
    force: bool
    dry_run: bool
    jobs: list | None          # 시험용: 대상 공고를 직접 넘기면 DB를 읽지 않는다
    total: int                 # 원문 공고 수
    new_recs: list             # 새·바뀐 공고(key·role_code·title만, 본문은 State에 두지 않음)
    new_roles: list
    ingested: int
    targets: list
    results: list
    report: dict


def _coll(s):
    return R._coll(s.get("persist_dir"))


def scan(s: JobRegenState) -> dict:
    R.use_local_models()
    recs = R.load_raw(s["raw_dir"])
    todo, n_have = R.changed(_coll(s), recs)
    print(f"[scan] 원문 {len(recs)}개 · 새·바뀐 {len(todo)}개 · 저장돼 있던 공고 {n_have}개", flush=True)
    return {"total": len(recs), "new_recs": [{"key": r["key"], "role_code": r["role_code"], "title": r["title"]} for r in todo],
            "new_roles": sorted({r["role_code"] for r in todo})}


def ingest(s: JobRegenState) -> dict:
    coll = _coll(s)
    keys = {r["key"] for r in s.get("new_recs") or []}
    done = 0
    for r in R.load_raw(s["raw_dir"]):
        if r["key"] in keys and R.upsert_one(coll, r):
            done += 1
            if done % 10 == 0:
                print(f"[ingest] {done}/{len(keys)}", flush=True)
    print(f"[ingest] {done}/{len(keys)}개 적재 · 컬렉션 조각 {coll.count()}개", flush=True)
    return {"ingested": done}


def select_targets(s: JobRegenState) -> dict:
    roles = None if s.get("force") else (s.get("new_roles") or [])
    if s.get("jobs") is not None:
        jobs = [j for j in s["jobs"] if roles is None or j["role_code"] in roles]
    else:
        jobs = R.seed_jobs(roles) if roles is None or roles else []
    print(f"[targets] 재생성 대상 시드 공고 {len(jobs)}개 (직무 {roles if roles is not None else '전체'})", flush=True)
    return {"targets": jobs}


def regenerate(s: JobRegenState) -> dict:
    coll = _coll(s)
    out = []
    for j in s["targets"]:
        r = R.regenerate_one(coll, j, s.get("k", 8))
        print(f"[regen] #{j['id']} {j['company_name']} {j['title']} → {r['status']}", flush=True)
        out.append(r)
    return {"results": out}


def report(s: JobRegenState) -> dict:
    from .. import config
    info = {"llm_model": config.LLM_MODEL, "embed_model": config.EMBED_MODEL, "k": s.get("k", 8),
            "collection_chunks": _coll(s).count(), "raw_total": s.get("total", 0),
            "new_or_changed": len(s.get("new_recs") or []), "ingested": s.get("ingested", 0),
            "force": bool(s.get("force"))}
    results = s.get("results") or []
    R.write_report(results, s["out_dir"], s["name"], info)
    summary = {st: sum(1 for r in results if r["status"] == st) for st in {r["status"] for r in results}}
    return {"report": {**info, "status_counts": summary}}


# ---------- 엣지 ----------
def _after_scan(s: JobRegenState) -> str:
    if s.get("dry_run"):
        return "end"
    return "ingest" if (s.get("new_recs") or s.get("force")) else "end"


def _after_targets(s: JobRegenState) -> str:
    return "regenerate" if s.get("targets") else "report"


def build():
    g = StateGraph(JobRegenState)
    for name, fn in (("scan", scan), ("ingest", ingest), ("select_targets", select_targets),
                     ("regenerate", regenerate), ("report", report)):
        g.add_node(name, fn)
    g.add_edge(START, "scan")
    g.add_conditional_edges("scan", _after_scan, {"end": END, "ingest": "ingest"})
    g.add_edge("ingest", "select_targets")
    g.add_conditional_edges("select_targets", _after_targets, {"regenerate": "regenerate", "report": "report"})
    g.add_edge("regenerate", "report")
    g.add_edge("report", END)
    return g.compile()


GRAPH = build()


def run(out_dir, raw_dir=None, k=8, force=False, dry_run=False, name=None, persist_dir=None, jobs=None) -> dict:
    state = {"raw_dir": raw_dir or R.RESOURCES_DIR, "out_dir": out_dir, "k": k, "force": force, "dry_run": dry_run,
             "name": name or f"regenerated_jobs_{dt.date.today().isoformat()}", "persist_dir": persist_dir, "jobs": jobs}
    return GRAPH.invoke(state, {"run_name": "job_posting_graph", "tags": ["graph3"]})


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("run")
    a.add_argument("--out", required=True)
    a.add_argument("--raw", default=None)
    a.add_argument("--k", type=int, default=8)
    a.add_argument("--name", default=None)
    a.add_argument("--force", action="store_true")
    a.add_argument("--dry-run", action="store_true")
    x = ap.parse_args(argv)
    st = run(x.out, x.raw, x.k, x.force, x.dry_run, x.name)
    print("[graph3]", {k: st.get(k) for k in ("total", "ingested", "report") if k in st}, flush=True)


if __name__ == "__main__":
    main()
