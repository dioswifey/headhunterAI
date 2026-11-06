"""SQLD 출제 범위 · 출제 비율 · 최근 유형 통계 (2026-11-06, SQL 문제은행).

① 공식 출제 범위(SQLD, 2024 개편 기준): 과목 2개 · 주요항목 · 세부항목 · 과목별 문항 수
   - 1과목 데이터 모델링의 이해 10문항(20%) · 2과목 SQL 기본과 활용 40문항(80%), 객관식 4지선다 50문항 · 90분
   - 합격: 총점 60점 이상, 과목별 40% 미만 과락
   - 공식 사이트(dataq.or.kr)는 robots.txt로 자동 수집을 막아 자동으로 읽지 않았다. 아래 값은 공개된 출제 기준을 사람이 옮긴 것 →
     사용자가 공식 안내와 한 번 대조할 것(OFFICIAL["verified_by_human"])
② 최근 유형 통계: 네이버 **검색 API**(공식, 키 필요)로 2026-01-01 이후 블로그 글의 제목·요약만 읽어 세부항목 키워드가 나온 횟수만 센다.
   원문·문제 문장은 저장하지 않는다. 네이버 블로그·카페·검색 페이지를 직접 긁는 것은 robots.txt가 막고 있어 하지 않는다.
   카페 검색 API는 글 날짜를 주지 않으므로 2026년 회차(60회~)를 이름에 넣은 검색어로만 센다.
③ 출제 명세: 과목 비율(공식) × 세부항목 비율(통계 50% + 균등 50%, 통계가 없으면 균등)

    python -m app.agents.sql_scope collect-stats [--since 20260101]   # 네이버 검색 API
    python -m app.agents.sql_scope show [--n 60]                       # 범위·통계·출제 명세 배분
"""
import argparse
import html
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone

import httpx

from .. import config

DIR = os.path.join(os.getenv("QUESTIONBANK_DIR", "/questionbank"), "sql_taxonomy")
STATS_PATH = os.path.join(DIR, "sqld_stats.json")

OFFICIAL = {
    "exam": "SQL 개발자(SQLD)", "basis": "2024 개편 출제 기준", "format": "객관식 4지선다 50문항 · 90분",
    "pass_rule": "총점 60점 이상 · 과목별 40% 미만 과락", "source": "한국데이터산업진흥원 데이터자격검정(dataq.or.kr) 공개 안내",
    "verified_by_human": False,
    "subjects": [
        {"no": 1, "name": "데이터 모델링의 이해", "questions": 10, "areas": [
            {"area": "데이터 모델링의 이해", "items": ["데이터 모델의 이해", "엔터티", "속성", "관계", "식별자"]},
            {"area": "데이터 모델과 SQL", "items": ["정규화", "관계와 조인의 이해", "모델이 표현하는 트랜잭션의 이해", "NULL 속성의 이해",
                                               "본질식별자 vs 인조식별자"]}]},
        {"no": 2, "name": "SQL 기본과 활용", "questions": 40, "areas": [
            {"area": "SQL 기본", "items": ["관계형 데이터베이스 개요", "SELECT 문", "함수", "WHERE 절", "GROUP BY·HAVING 절", "ORDER BY 절",
                                         "조인", "표준 조인"]},
            {"area": "SQL 활용", "items": ["서브 쿼리", "집합 연산자", "그룹 함수", "윈도우 함수", "Top N 쿼리", "계층형 질의와 셀프 조인",
                                         "PIVOT 절과 UNPIVOT 절", "정규 표현식"]},
            {"area": "관리 구문", "items": ["DML", "TCL", "DDL", "DCL"]}]},
    ],
}

# 세부항목별 통계 키워드(제목·요약에 나오면 1회). 대소문자 무시
KEYWORDS = {
    "데이터 모델의 이해": ["데이터 모델링", "데이터모델링", "개념적 모델", "논리적 모델", "물리적 모델", "3층 스키마", "ERD"],
    "엔터티": ["엔터티", "엔티티"], "속성": ["속성", "도메인"], "관계": ["관계차수", "카디널리티", "관계 표기", "식별관계", "비식별관계"],
    "식별자": ["식별자", "주식별자", "후보식별자"], "정규화": ["정규화", "반정규화", "정규형", "함수적 종속"],
    "관계와 조인의 이해": ["관계와 조인"], "모델이 표현하는 트랜잭션의 이해": ["트랜잭션 모델", "모델이 표현하는 트랜잭션"],
    "NULL 속성의 이해": ["NULL"], "본질식별자 vs 인조식별자": ["본질식별자", "인조식별자"],
    "관계형 데이터베이스 개요": ["관계형 데이터베이스", "RDBMS"], "SELECT 문": ["SELECT", "DISTINCT", "ALIAS"],
    "함수": ["단일행 함수", "NVL", "DECODE", "COALESCE", "NULLIF", "SUBSTR", "TO_CHAR", "CASE"], "WHERE 절": ["WHERE", "LIKE", "BETWEEN"],
    "GROUP BY·HAVING 절": ["GROUP BY", "HAVING", "집계함수", "집계 함수"], "ORDER BY 절": ["ORDER BY", "정렬"],
    "조인": ["조인", "JOIN", "EQUI", "NON EQUI"], "표준 조인": ["ANSI", "NATURAL JOIN", "OUTER JOIN", "CROSS JOIN", "USING"],
    "서브 쿼리": ["서브쿼리", "서브 쿼리", "스칼라", "인라인 뷰", "EXISTS"], "집합 연산자": ["UNION", "INTERSECT", "MINUS", "EXCEPT", "집합 연산"],
    "그룹 함수": ["ROLLUP", "CUBE", "GROUPING SETS", "GROUPING"], "윈도우 함수": ["윈도우 함수", "윈도우함수", "RANK", "ROW_NUMBER", "LAG", "LEAD", "OVER"],
    "Top N 쿼리": ["TOP N", "TOP-N", "ROWNUM", "FETCH FIRST"], "계층형 질의와 셀프 조인": ["계층형", "CONNECT BY", "셀프 조인", "셀프조인"],
    "PIVOT 절과 UNPIVOT 절": ["PIVOT", "UNPIVOT"], "정규 표현식": ["정규 표현식", "정규표현식", "REGEXP"],
    "DML": ["DML", "INSERT", "UPDATE", "DELETE", "MERGE"], "TCL": ["TCL", "COMMIT", "ROLLBACK", "SAVEPOINT"],
    "DDL": ["DDL", "CREATE TABLE", "ALTER TABLE", "TRUNCATE", "제약조건", "제약 조건"], "DCL": ["DCL", "GRANT", "REVOKE"],
}
QUERIES_BLOG = ["SQLD 기출", "SQLD 후기", "SQLD 시험 후기", "SQLD 복원", "SQLD 합격", "SQLD 문제", "SQLD 오답", "SQLD 정리",
                "SQLD 60회", "SQLD 61회", "SQLD 62회", "SQLD 63회"]
QUERIES_CAFE = ["SQLD 60회", "SQLD 61회", "SQLD 62회", "SQLD 63회"]     # 2026년 회차(카페 API는 날짜가 없음)


def items(subject=None):
    return [(s["no"], a["area"], it) for s in OFFICIAL["subjects"] if subject in (None, s["no"]) for a in s["areas"] for it in a["items"]]


def _clean(t):
    return html.unescape(re.sub(r"<[^>]+>", "", t or ""))


def _search(kind, query, start):
    r = httpx.get(f"https://openapi.naver.com/v1/search/{kind}.json", timeout=15,
                  params={"query": query, "display": 100, "start": start, "sort": "date"},
                  headers={"X-Naver-Client-Id": config.NAVER_CLIENT_ID, "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET})
    if r.status_code != 200:
        raise RuntimeError(f"naver {kind} {r.status_code}: {r.text[:150]}")
    return r.json().get("items") or []


def collect_stats(since="20260101", pages=5):
    if not (config.NAVER_CLIENT_ID and config.NAVER_CLIENT_SECRET):
        raise SystemExit("NAVER_CLIENT_ID·NAVER_CLIENT_SECRET 없음 — developers.naver.com에서 애플리케이션 등록(검색 API) 후 .env에 넣고 "
                         "docker compose up -d api")
    seen, docs = set(), []
    for kind, queries in (("blog", QUERIES_BLOG), ("cafearticle", QUERIES_CAFE)):
        for q in queries:
            for p in range(pages):
                its = _search(kind, q, 1 + p * 100)
                time.sleep(0.15)                                  # 초당 10회 이하
                stop = False
                for it in its:
                    link = it.get("link")
                    if kind == "blog":
                        if (it.get("postdate") or "0") < since:
                            stop = True                           # 날짜순이므로 이후는 모두 더 오래됨
                            continue
                    if link in seen:
                        continue
                    seen.add(link)
                    docs.append((kind, _clean(it.get("title")) + " " + _clean(it.get("description"))))
                if stop or len(its) < 100:
                    break
    counts = {it: 0 for _, _, it in items()}
    for _, text in docs:
        up = text.upper()
        for it, kws in KEYWORDS.items():
            if any(k.upper() in up for k in kws):
                counts[it] += 1
    out = {"collected_at": datetime.now(timezone.utc).isoformat()[:19] + "Z", "since": since,
           "source": "네이버 검색 API(블로그: 날짜 필터 / 카페: 2026 회차 검색어) — 제목·요약의 키워드 등장 수만, 원문 미저장",
           "documents": {"blog": sum(1 for k, _ in docs if k == "blog"), "cafearticle": sum(1 for k, _ in docs if k == "cafearticle")},
           "counts": counts}
    os.makedirs(DIR, exist_ok=True)
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def load_stats():
    try:
        with open(STATS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def weights(subject):
    """세부항목 비율: 통계 50% + 균등 50% (통계 없으면 균등)."""
    its = [it for _, _, it in items(subject)]
    st = (load_stats() or {}).get("counts") or {}
    tot = sum(st.get(i, 0) for i in its)
    eq = 1 / len(its)
    return {i: (0.5 * st.get(i, 0) / tot + 0.5 * eq) if tot else eq for i in its}


def allocate(n, w):
    """최대 나머지 방식으로 n개를 비율대로 나눈다."""
    raw = {k: n * v for k, v in w.items()}
    out = {k: math.floor(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: n - sum(out.values())]:
        out[k] += 1
    return out


def plan(n=60):
    """과목 비율(공식 10:40) × 세부항목 비율로 출제 명세 n개."""
    tq = sum(s["questions"] for s in OFFICIAL["subjects"])
    per_subject = allocate(n, {s["no"]: s["questions"] / tq for s in OFFICIAL["subjects"]})
    area_of = {it: (no, area) for no, area, it in items()}
    specs = []
    for no, k in per_subject.items():
        for it, c in allocate(k, weights(no)).items():
            for j in range(c):
                specs.append({"subject": no, "area": area_of[it][1], "item": it, "variant": j})
    return specs


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sql_scope")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect-stats")
    c.add_argument("--since", default="20260101")
    s = sub.add_parser("show")
    s.add_argument("--n", type=int, default=60)
    a = ap.parse_args(argv)
    if a.cmd == "collect-stats":
        out = collect_stats(a.since)
        print(json.dumps({k: out[k] for k in ("since", "documents")}, ensure_ascii=False))
        for it, v in sorted(out["counts"].items(), key=lambda kv: -kv[1]):
            print(f"  {v:5d}  {it}")
    else:
        st = load_stats()
        print("통계:", "없음(균등 배분)" if not st else f"{st['collected_at']} · 문서 {st['documents']}")
        from collections import Counter
        cnt = Counter((sp["subject"], sp["item"]) for sp in plan(a.n))
        for (no, it), v in sorted(cnt.items()):
            print(f"  {no}과목  {v:2d}  {it}")
        print("합계", sum(cnt.values()))


if __name__ == "__main__":
    sys.exit(main())
