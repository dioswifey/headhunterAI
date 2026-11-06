"""자체 문제은행 출제 목록 (2026-11-05) — 일반 CS 학습 목표만으로 만든 독립 명세.

수집한 외부 문제(collected_problems)의 본문·제목·예시·해설·테스트는 여기와 생성 프롬프트에 넣지 않는다.
항목 = topic · difficulty(basic|applied) · learning_objective · target_complexity.
"""
SPECS = [
    {"topic": "배열", "difficulty": "basic",
     "learning_objective": "배열을 한 번만 순회하면서 지금까지의 상태(최댓값·최솟값·누적값)를 갱신해 답을 구한다",
     "target_complexity": "시간 O(n), 추가 공간 O(1)"},
    {"topic": "문자열", "difficulty": "basic",
     "learning_objective": "문자열을 왼쪽부터 읽으며 연속 구간을 묶거나 규칙에 따라 변환해 새 문자열을 만든다",
     "target_complexity": "시간 O(n), 공간 O(n)"},
    {"topic": "해시", "difficulty": "basic",
     "learning_objective": "딕셔너리로 값의 등장 횟수나 마지막 위치를 기록해 이중 반복 없이 조건을 만족하는 쌍·원소를 찾는다",
     "target_complexity": "시간 O(n), 공간 O(n)"},
    {"topic": "스택", "difficulty": "basic",
     "learning_objective": "스택에 아직 처리되지 않은 원소를 쌓아 두고, 새 원소가 들어올 때 조건에 맞는 원소를 꺼내 처리한다",
     "target_complexity": "시간 O(n), 공간 O(n)"},
    {"topic": "큐", "difficulty": "basic",
     "learning_objective": "큐(collections.deque)로 먼저 들어온 작업부터 처리하는 과정을 시뮬레이션해 각 작업의 결과 순서나 시각을 구한다",
     "target_complexity": "시간 O(n), 공간 O(n)"},
    {"topic": "정렬", "difficulty": "basic",
     "learning_objective": "데이터를 기준에 맞게 정렬한 뒤 인접한 원소끼리만 비교해 답을 구한다(정렬 기준 설계 포함)",
     "target_complexity": "시간 O(n log n), 공간 O(n)"},
    {"topic": "이분 탐색", "difficulty": "applied",
     "learning_objective": "답의 범위를 정하고 '이 값으로 가능한가'를 판정하는 함수를 만들어 가능한 최솟값(또는 최댓값)을 이분 탐색으로 찾는다",
     "target_complexity": "시간 O(n log M) (M = 답의 범위), 공간 O(1)"},
    {"topic": "BFS", "difficulty": "applied",
     "learning_objective": "격자나 그래프에서 너비 우선 탐색으로 시작점부터 각 칸까지의 최소 이동 횟수를 구한다(도달 불가 처리 포함)",
     "target_complexity": "시간 O(V + E), 공간 O(V)"},
    {"topic": "DFS", "difficulty": "applied",
     "learning_objective": "깊이 우선 탐색(반복문 스택 또는 재귀)으로 서로 연결된 묶음을 찾아 묶음의 수나 크기를 구한다",
     "target_complexity": "시간 O(V + E), 공간 O(V)"},
    {"topic": "기초 DP", "difficulty": "applied",
     "learning_objective": "작은 문제의 답을 배열에 저장하고 점화식으로 큰 문제의 답을 만드는 1차원 동적 계획법을 설계한다",
     "target_complexity": "시간 O(n), 공간 O(n)"},
]


# ---------------- 프로그래머스 framework 기반 명세 (2026-11-05 사용자 지시) ----------------
# 수집한 프로그래머스 566문항의 **유형 통계(개수·분포)만** 쓴다(questionbank/coding_taxonomy/programmers_taxonomy.json).
# 레벨(Lv.0~1 = junior, Lv.2+ = senior) · 그 레벨의 개념 분포 · 입출력 구조 분포에서 뽑아 명세를 만들고,
# AI는 이 틀 안에서만 문제를 쓴다. 프로그래머스 문제의 문장·예시·테스트는 쓰지 않는다(권한 확인 전).
import json as _json
import random as _random

TAXONOMY = "/questionbank/coding_taxonomy/programmers_taxonomy.json"
JUNIOR_LV, SENIOR_LV = ("Lv.0", "Lv.1"), ("Lv.2", "Lv.3", "Lv.4", "Lv.5")
CONCEPT_INFO = {   # 개념 → (학습 목표, 목표 복잡도). 일반 CS 설명으로 직접 씀
    "기초 문법(조건·반복)": ("조건문과 반복문으로 규칙에 따라 값을 세거나 바꾼다", "시간 O(n), 공간 O(1)"),
    "문자열": ("문자열을 순회·분리·결합해 규칙에 맞는 결과를 만든다", "시간 O(n), 공간 O(n)"),
    "배열·리스트 처리": ("배열을 순회하며 상태를 갱신하거나 새 배열을 만든다", "시간 O(n), 공간 O(n)"),
    "해시": ("딕셔너리로 등장 횟수·위치를 기록해 이중 반복 없이 답을 찾는다", "시간 O(n), 공간 O(n)"),
    "스택·큐": ("스택 또는 큐로 처리 순서를 관리한다", "시간 O(n), 공간 O(n)"),
    "정렬": ("정렬 기준을 설계하고 정렬 결과를 이용해 답을 구한다", "시간 O(n log n), 공간 O(n)"),
    "완전탐색": ("가능한 경우를 빠짐없이 나열해 조건을 만족하는 답을 찾는다(입력이 작음)", "입력 크기 안에서 모든 경우 확인"),
    "탐욕법": ("매 단계 가장 좋은 선택이 전체 최적이 되는 근거를 세워 답을 구한다", "시간 O(n log n), 공간 O(n)"),
    "동적계획법": ("작은 문제의 답을 저장하고 점화식으로 큰 문제의 답을 만든다", "시간 O(n) 또는 O(n·m), 공간 O(n)"),
    "DFS·BFS": ("격자·그래프를 깊이/너비 우선으로 탐색해 연결 묶음이나 최단 거리를 구한다", "시간 O(V + E), 공간 O(V)"),
    "그래프·최단 경로": ("그래프를 만들고 최단 경로를 구한다", "시간 O(E log V), 공간 O(V + E)"),
    "트리": ("부모·자식 관계를 이용해 트리를 순회하고 값을 모은다", "시간 O(n), 공간 O(n)"),
    "이분탐색": ("답의 범위를 정하고 판정 함수로 가능한 최솟값·최댓값을 이분 탐색한다", "시간 O(n log M), 공간 O(1)"),
    "수학·정수론": ("나머지·약수·최대공약수 같은 수의 성질로 계산을 줄인다", "시간 O(√n) 또는 O(n), 공간 O(1)"),
    "힙": ("힙(heapq)으로 가장 작은·큰 값을 반복해서 꺼내며 처리한다", "시간 O(n log n), 공간 O(n)"),
    "기하·좌표": ("좌표와 거리·방향을 계산해 조건을 판정한다", "시간 O(n), 공간 O(1)"),
    "백트래킹": ("조건에 맞지 않는 경우를 일찍 잘라 내며 경우를 탐색한다(입력이 작음)", "입력 크기 안에서 가지치기 탐색"),
}


def _fits(concept, family):
    if concept in ("DFS·BFS", "그래프·최단 경로", "트리"):
        return "2차원 배열" in family
    if concept == "문자열":
        return "문자열" in family
    if concept in ("기하·좌표",):
        return "배열" in family
    return True


def framework_specs(n=10, path=TAXONOMY, seed=None, junior=None):
    st = _json.load(open(path, encoding="utf-8"))
    rnd = _random.Random(seed)
    diff = st["difficulty"]
    jr = sum(diff.get(k, 0) for k in JUNIOR_LV)
    total = sum(diff.values()) or 1
    n_jr = round(n * jr / total) if junior is None else junior
    fams = {k: v for k, v in st["structure_families"].items() if k != "예제 없음" and v >= 10}   # 자주 나온 구조만(10회 이상)
    out, used = [], {}
    cap = max(2, -(-n // 5))                       # 같은 개념 최대 횟수: 10문제면 2, 60문제면 12
    for i in range(n):
        level = "basic" if i < n_jr else "applied"
        lvs = JUNIOR_LV if level == "basic" else SENIOR_LV
        cw = {c: sum(d.get(k, 0) for k in lvs) for c, d in st["concept_x_difficulty"].items() if c in CONCEPT_INFO}
        cw = {c: w for c, w in cw.items() if w > 0 and used.get(c, 0) < cap}    # 같은 개념 쏠림 방지(다양성)
        concept = rnd.choices(list(cw), weights=list(cw.values()))[0]
        used[concept] = used.get(concept, 0) + 1
        fw = {f: w for f, w in fams.items() if _fits(concept, f)} or fams
        family = rnd.choices(list(fw), weights=list(fw.values()))[0]
        obj, cx = CONCEPT_INFO[concept]
        out.append({"topic": concept, "difficulty": level, "learning_objective": obj, "target_complexity": cx,
                    "structure": family,
                    "framework": {"source": "programmers_taxonomy(개수만)", "level": f"{'Junior' if level == 'basic' else 'Senior'} "
                                  f"= 프로그래머스 {'·'.join(lvs)}", "concept_weight": cw[concept], "structure_weight": fw[family]}})
    return out
