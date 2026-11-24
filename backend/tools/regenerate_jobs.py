"""공고 RAG 재생성 — CLI·자체 시험 (2026-11-12). 핵심 함수는 app.agents.job_posting_regen,
전체 단계 순서는 LangGraph app.agents.job_posting_graph(그래프 3).

실행(컨테이너 안, 이 파일을 먼저 복사):
    docker cp backend/tools/. headhunterAI-api:/srv/tools/
    docker exec -w /srv headhunterAI-api python -m tools.regenerate_jobs selftest            # 모델·DB 없이 시험
    docker exec -w /srv headhunterAI-api python -m tools.regenerate_jobs ingest [--raw DIR] [--dry-run]
    docker exec -w /srv headhunterAI-api python -m tools.regenerate_jobs regenerate --out /tmp/regen --k 8
    (보통은 그래프 한 번: python -m app.agents.job_posting_graph run --out /tmp/regen)
    docker cp headhunterAI-api:/tmp/regen/. documents/03_QA_doc/eval_runs/
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import job_posting_regen as R  # noqa: E402


def _fake_embed(texts):
    """문자 2-gram 해시 버킷 64차원 — 같은 문장은 코사인 1, 비슷한 문장은 높게."""
    out = []
    for t in texts:
        v = [0.0] * 64
        t = R._norm(t)
        for i in range(len(t) - 1):
            v[int(hashlib.md5(t[i:i + 2].encode()).hexdigest(), 16) % 64] += 1.0
        out.append(v)
    return out


COPIED = "Kubernetes 기반의 대규모 트래픽 서비스 운영 경험이 있으신 분을 찾습니다"


def _fake_chat(system, user, schema, **kw):
    if "sentence" in schema.get("properties", {}):
        return {"sentence": "컨테이너 오케스트레이션 환경에서 서비스를 안정적으로 다뤄 본 분"}
    return {"overview": "상담 플랫폼의 API를 설계하고 운영합니다. 팀과 함께 서비스 품질을 높입니다.",
            "duties": ["상담 예약 API 설계와 구현", "결제 연동 모듈 유지보수", COPIED, "장애 대응 절차 개선"],
            "requirements": ["Python 또는 Java 실무 경험", "관계형 DB 설계 이해", "REST API 설계 경험"],
            "preferred": ["메시지 큐 운영 경험", "테스트 자동화 경험"],
            "tech_stack": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker"]}


def _samples(raw):
    samples = [
        {"site": "saramin", "id": "1001", "role_code": "backend", "company": "가상A", "title": "백엔드 개발자",
         "url": "https://example.invalid/1001", "collected_at": "2026-11-12T00:00:00+09:00",
         "body_text": "## 주요 업무\n주문 서비스 백엔드 개발\n\n## 자격 요건\n" + COPIED + "\nJava 3년 이상 실무 경험이 있는 분"},
        {"source": "work24", "id": "2002", "role_code": "backend", "company": "가상B", "title": "서버 개발자",
         "url": "https://example.invalid/2002", "fetched_at": "2026-11-12T00:00:00+09:00",
         "text": "## 주요 업무\n정산 시스템 서버 개발과 배치 운영을 담당합니다\n\n## 우대 사항\nKafka 운영 경험을 우대합니다"},
        {"source": "x", "id": "bad", "role_code": "nope", "text": "직무 코드가 틀린 원문은 버린다"},
    ]
    for s in samples:
        (raw / f"{s.get('site') or s.get('source')}_{s['id']}.json").write_bytes(json.dumps(s, ensure_ascii=False).encode())


JOBS = [{"id": 5, "role_code": "backend", "title": "백엔드 개발자 (상담 플랫폼 API)", "company_intro": "가상 회사",
         "duties": "상담 API", "requirements": "", "company_name": "헤드헌터랩_서울", "role_name": "백엔드 개발자"},
        {"id": 6, "role_code": "data_sci", "title": "데이터 분석가", "company_intro": "가상 회사",
         "duties": "", "requirements": "", "company_name": "헤드헌터랩_서울", "role_name": "데이터 분석가"}]


def _check_result(res, md_path):
    assert res["status"] == "pass", res
    assert all(COPIED not in x for x in res["draft"]["duties"]), "복사 문장은 고쳐 써야 함"
    assert res["sentences"]["rewritten"] >= 1, res["sentences"]
    md = Path(md_path).read_text(encoding="utf-8")
    assert "Java 3년 이상 실무 경험이 있는 분" not in md, "원문 본문이 결과 파일에 들어가면 안 됨"
    assert {x["rec_id"] for x in res["references"]} <= {"1001", "2002"}


def selftest():
    """가짜 임베딩·가짜 LLM·임시 Chroma로 함수 단위 + 그래프 3 전체를 시험. 실제 Chroma·DB·모델은 건드리지 않는다."""
    R.EMBED, R.CHAT = _fake_embed, _fake_chat

    # 1) 함수 단위
    tmp = Path(tempfile.mkdtemp(prefix="regen_selftest_"))
    raw = tmp / "raw"
    raw.mkdir()
    _samples(raw)
    pdir = str(tmp / "chroma")
    assert len(R.load_raw(raw)) == 2, "정규화: 잘못된 직무 코드는 버려야 함"
    r1, r2 = R.ingest(raw, pdir), R.ingest(raw, pdir)
    assert r1["ingested"] == 2 and r2["ingested"] == 0, "같은 원문 두 번 → 두 번째는 변경 0건"
    res = R.regenerate(str(tmp / "out"), 4, "selftest", persist_dir=pdir, jobs=JOBS[:1])[0]
    _check_result(res, tmp / "out" / "selftest.md")
    print("[selftest] 함수 OK —", json.dumps(res["sentences"], ensure_ascii=False), flush=True)

    # 2) 그래프 3: 새 공고 → 그 직무(backend)만 재생성, 같은 원문 다시 → 변경 0건으로 바로 종료
    from app.agents import job_posting_graph as G
    tmp2 = Path(tempfile.mkdtemp(prefix="regen_graph_"))
    raw2 = tmp2 / "raw"
    raw2.mkdir()
    _samples(raw2)
    p2 = str(tmp2 / "chroma")
    st = G.run(str(tmp2 / "out"), raw_dir=str(raw2), k=4, name="g1", persist_dir=p2, jobs=JOBS)
    assert st["ingested"] == 2 and [t["id"] for t in st["targets"]] == [5], st.get("targets")
    _check_result(st["results"][0], tmp2 / "out" / "g1.md")
    st2 = G.run(str(tmp2 / "out"), raw_dir=str(raw2), k=4, name="g2", persist_dir=p2, jobs=JOBS)
    assert not st2.get("new_recs") and "results" not in st2 and not (tmp2 / "out" / "g2.json").exists(), "변경 0건이면 끝"
    st3 = G.run(str(tmp2 / "out"), raw_dir=str(raw2), k=4, name="g3", persist_dir=p2, jobs=JOBS, force=True)
    assert {t["id"] for t in st3["targets"]} == {5, 6}, "force면 전체 직무"
    assert {r["job_id"]: r["status"] for r in st3["results"]}[6] == "no_refs", "참고 조각 없는 직무는 no_refs"
    print("[selftest] 그래프 3 OK — 새 공고 직무만 재생성 · 재실행 변경 0건 종료 · force 전체 · 참고 없음 처리", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("ingest")
    a1.add_argument("--raw", default=None)
    a1.add_argument("--dry-run", action="store_true")
    a2 = sub.add_parser("regenerate")
    a2.add_argument("--out", required=True)
    a2.add_argument("--k", type=int, default=8)
    a2.add_argument("--name", default=f"regenerated_jobs_{dt.date.today().isoformat()}")
    sub.add_parser("selftest")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        return selftest()
    R.use_local_models()
    if a.cmd == "ingest":
        R.ingest(a.raw or R.RESOURCES_DIR, dry=a.dry_run)
    else:
        R.regenerate(a.out, a.k, a.name)


if __name__ == "__main__":
    main()
