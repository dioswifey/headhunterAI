"""코딩 문제 유형 통계 (collected_problems → 유형·개념·입출력 구조 개수만).

수집한 문제의 문장·제목·예제는 결과에 넣지 않는다. 결과는 **개수 통계만**(JSON) — 새 문제를 처음부터 만들 때
'어떤 개념을 어느 비율로, 어떤 입출력 구조로' 낼지 참고하는 용도(ADLC v0.4 6.10).

    ① 문제집 이름(partTitle, 목록 API) → 출처 묶음(기초·연습문제·유형별 문제집·기출/대회)
    ② 개념: 유형별 문제집이면 이름 그대로(규칙), 아니면 로컬 모델이 고정 목록에서 주 개념 1개 + 보조 0~1개 (`llm.chat_json`)
    ③ 입출력 구조: 예제 값의 타입으로 시그니처('(int, list[int]) → int')와 큰 묶음('정수·1차원 배열 → 정수')
    ④ 집계: 개념별·난이도별·출처별·구조별 개수, 서로 다른 구조 수

실행(컨테이너 안, 문항당 모델 1회 — 약 500문항 30~50분):
    python -m app.agents.coding_taxonomy --source programmers > taxonomy.json
"""
import argparse
import collections
import json
import os
import sys

from .. import db, llm
from .coding_problem_agent import Fetcher

CONCEPTS = ["기초 문법(조건·반복)", "구현·시뮬레이션", "문자열", "수학·정수론", "배열·리스트 처리", "해시", "스택·큐", "힙",
            "정렬", "완전탐색", "백트래킹", "탐욕법", "동적계획법", "DFS·BFS", "그래프·최단 경로", "트리",
            "이분탐색", "투 포인터·슬라이딩 윈도우", "누적합", "비트 연산", "유니온 파인드·자료구조 설계", "기하·좌표"]
PART_CONCEPT = {"해시": "해시", "스택/큐": "스택·큐", "힙(Heap)": "힙", "정렬": "정렬", "완전탐색": "완전탐색",
                "탐욕법(Greedy)": "탐욕법", "동적계획법(Dynamic Programming)": "동적계획법",
                "깊이/너비 우선 탐색(DFS/BFS)": "DFS·BFS", "이분탐색": "이분탐색", "그래프": "그래프·최단 경로"}
SYSTEM = ("너는 코딩테스트 문제 분류기다. 문제 설명을 읽고 풀이에 필요한 핵심 알고리즘 개념을 아래 목록에서만 고른다.\n"
          "primary = 가장 중요한 개념 1개, secondary = 두 번째로 중요한 개념(없으면 빈 문자열). 목록에 없는 말을 쓰지 않는다.\n"
          "단순히 if·for만 쓰면 되는 문제는 '기초 문법(조건·반복)'이다.\n목록: " + ", ".join(CONCEPTS))
SCHEMA = {"type": "object", "properties": {"primary": {"type": "string", "enum": CONCEPTS},
                                           "secondary": {"type": "string", "enum": CONCEPTS + [""]}},
          "required": ["primary", "secondary"]}
CACHE = "/tmp/coding_taxonomy_cache.json"


def group_of(part):
    if part in ("코딩 기초 트레이닝", "코딩테스트 입문"):
        return "기초"
    if part == "연습문제":
        return "연습문제"
    if part in PART_CONCEPT:
        return "유형별 문제집"
    return "기출·대회"


def type_of(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, dict):
        return "dict"
    if isinstance(v, list):
        inner = [type_of(x) for x in v]
        known = [t for t in inner if "?" not in t]
        return f"list[{collections.Counter(known or inner).most_common(1)[0][0] if inner else '?'}]"
    return "?"


def _merge(types):
    known = [t for t in types if "?" not in t]
    return collections.Counter(known or types).most_common(1)[0][0]


def signature(examples, params):
    if not examples:
        return f"({', '.join(['?'] * len(params or []))}) → ?", "예제 없음"
    n = len(examples[0]["args"])
    ins = [_merge([type_of(e["args"][i]) for e in examples if len(e["args"]) > i]) for i in range(n)]
    out = _merge([type_of(e["expected"]) for e in examples])
    return f"({', '.join(ins)}) → {out}", f"{' · '.join(sorted({family(t) for t in ins})) or '입력 없음'} → {family(out)}"


def family(t):
    if t in ("int", "float"):
        return "숫자"
    if t == "bool":
        return "참/거짓"
    if t == "str":
        return "문자열"
    if t == "list[str]":
        return "문자열 배열"
    if t.startswith("list[list"):
        return "2차원 배열"
    if t.startswith("list["):
        return "1차원 배열"
    return "기타"


def part_titles(source):
    if source != "programmers":
        return {}
    f, out, page = Fetcher(interval=1.0), {}, 1
    while True:
        d = f.get_json("https://school.programmers.co.kr/api/v2/school/challenges/"
                       f"?page={page}&perPage=20&order=recent&languages[]=python3")
        for r in d.get("result") or []:
            out[str(r["id"])] = r.get("partTitle") or ""
        if page >= int(d.get("totalPages") or 1):
            return out
        page += 1


def classify(rows, parts):
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    labels, failed = {}, 0
    for i, r in enumerate(rows):
        key = f"{r['source']}:{r['source_id']}:{r['content_hash'][:12]}"
        part = parts.get(r["source_id"], "")
        if part in PART_CONCEPT:
            labels[key] = {"primary": PART_CONCEPT[part], "secondary": "", "by": "rule"}
            continue
        if key in cache:
            labels[key] = cache[key]
            continue
        try:
            out = llm.chat_json(SYSTEM, (r["statement"] or "")[:1800], SCHEMA, timeout=60, temperature=0.0)
            labels[key] = {"primary": out["primary"], "secondary": out.get("secondary") or "", "by": "llm"}
        except llm.LLMError:
            labels[key] = {"primary": "미분류", "secondary": "", "by": "failed"}
            failed += 1
        cache[key] = labels[key]
        if i % 20 == 0:
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"[{i + 1}/{len(rows)}]", file=sys.stderr, flush=True)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return labels, failed


def build(source):
    rows = db.all_("SELECT source, source_id, difficulty, level, kind, quality, statement, examples, params, content_hash "
                   "FROM collected_problems WHERE source=%s ORDER BY id", source)
    parts = part_titles(source)
    labels, failed = classify(rows, parts)
    C = collections.Counter
    concept, concept2, by_group, by_diff, sig, fam = C(), C(), C(), C(), C(), C()
    concept_diff = collections.defaultdict(C)
    group_concept = collections.defaultdict(C)
    for r in rows:
        lab = labels[f"{r['source']}:{r['source_id']}:{r['content_hash'][:12]}"]
        g = group_of(parts.get(r["source_id"], ""))
        s, f = signature(r["examples"], r["params"])
        concept[lab["primary"]] += 1
        if lab["secondary"] and lab["secondary"] != lab["primary"]:
            concept2[lab["secondary"]] += 1
        by_group[g] += 1
        by_diff[r["difficulty"]] += 1
        concept_diff[lab["primary"]][r["difficulty"]] += 1
        group_concept[g][lab["primary"]] += 1
        sig[s] += 1
        fam[f] += 1
    return {
        "source": source, "total": len(rows), "note": "개수 통계만. 문제 문장·제목·예제는 넣지 않음",
        "classified_by": dict(C(v["by"] for v in labels.values())), "classify_failed": failed,
        "concepts_primary": dict(concept.most_common()), "concepts_secondary": dict(concept2.most_common()),
        "concept_x_difficulty": {k: dict(sorted(v.items())) for k, v in concept_diff.items()},
        "groups": dict(by_group.most_common()), "group_x_concept": {k: dict(v.most_common()) for k, v in group_concept.items()},
        "difficulty": dict(sorted(by_diff.items())),
        "structure_families": dict(fam.most_common()), "structure_family_count": len(fam),
        "signatures_top30": dict(sig.most_common(30)), "signature_count": len(sig),
        "problem_collections": len(set(parts.values())),
    }


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--source", default="programmers")
    a = p.parse_args(argv)
    db.pool.open()
    print(json.dumps(build(a.source), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
