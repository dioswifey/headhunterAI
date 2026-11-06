"""코딩 문제 검증 에이전트 A11 (LCEL, ADLC v0.4 6.11) — 생성 에이전트(A10)가 만든 초안을 검사해 통과/실패와 이유만 돌려준다.

문제를 고치지 않는다. 모델이 쓴 코드는 api 컨테이너에서 실행하지 않고 **실행기(runner)**에서만 돈다
(로컬: 네트워크 차단 컨테이너, 시간 10초·메모리 256MB·권한 없는 사용자. Railway 실행기는 네트워크 격리 없음 — CLAUDE.md).

검증 1 — 실행 (`execution_verifier`)
    V1 정적 검사     필요한 함수 정의·금지 호출(input·print·open·os·sys·subprocess·socket·eval·exec 등)
    V2 입력 생성     gen_examples(3) · gen_edge(4~8) · gen_random(seed 1~30)을 실행기에서 만들고, 모두 validate()=True여야 함
    V3 기대 출력     기준 풀이로 계산(모델이 쓴 값이 아님). 오류·tuple/set 반환 → 실패
    V4 단순 풀이 대조 작은 입력 전부에서 brute == solution
    V5 자명성 검사   상수·첫 인자·길이·합·정렬 같은 자명한 풀이가 전부 통과하면 실패, 서로 다른 출력 3개 미만이면 실패
    V6 틀린 풀이     모델이 낸 틀린 풀이 2개가 각각 1개 이상 실패해야 함 + 자동 변이(부등호·±1 등) 사망률 50% 이상
    V7 최대 입력     gen_large 2개가 validate()=True이고 기준 풀이가 실행기 제한 시간 안에 끝남
검증 2 — 내용 (`content_verifier`, 예시 설명을 쓴 뒤)
    V8 예시 설명     설명 3개가 계산된 출력 값을 그대로 담고 있어야 함
    V9 규칙 검사     한국어 제목·지시문 노출·제약 조건(빈 입력·중복 명시)·백틱 짝
    V10 AI 검토      모호함·난이도·주제 적합(검토 모델) — 소형 모델이라 V1~V9를 대신하지 않는다
    V11 유사도(참고) 이미 만든 문제 ≥ 0.92 → 중복으로 거절, 수집한 문제 ≥ 0.90 → 거절. **유사도는 참고 지표일 뿐 독창성·법적 적합성을 확정하지 않는다**
앞 단계가 실패하면 뒤 단계는 건너뛴다. 결과: x["fail"](없으면 통과)·x["tests"]·x["verification"]
"""
import ast
import json
import re
import sys
import time

from langchain_core.runnables import RunnableLambda
from redis.commands.search.field import TagField, VectorField
from redis.commands.search.indexDefinition import IndexDefinition, IndexType
from redis.commands.search.query import Query

from .. import coding, config, db, llm, prompts, vectorstore

N_RANDOM = 30
MIN_RANDOM = 20
MIN_EDGE = 4
MUTANT_KILL_MIN = 0.5
SIM_DUP = 0.92
SIM_SOURCE = 0.90
LEVEL_DESC = {"basic": "Junior(초급) — 프로그래머스 Lv.0~1 수준, 개념 하나를 바로 적용, 20분 안팎",
              "applied": "Senior — 프로그래머스 Lv.2 이상 수준, 개념을 조합하거나 효율을 고려, 40분 안팎"}
REVIEW_SCHEMA = {"type": "object", "properties": {
    "ambiguous": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string", "maxLength": 200}, "maxItems": 6},
    "difficulty_fit": {"type": "integer"}, "concept_fit": {"type": "integer"}},
    "required": ["ambiguous", "issues", "difficulty_fit", "concept_fit"]}
FORBIDDEN = re.compile(r"\binput\s*\(|\bprint\s*\(|\bopen\s*\(|\beval\s*\(|\bexec\s*\(|__import__|"
                       r"^\s*(from|import)\s+(os|sys|subprocess|socket|shutil|pathlib|requests|urllib|http|ctypes|threading|multiprocessing)\b",
                       re.M)
LEAK = re.compile(r"작성해라|작성하라|만들어라|JSON|출제 명세|gen_random|gen_edge|validator|한국어로 작성|문제 설명을 작성")


def _event(x, stage, ok, **detail):
    x["history"].append({"stage": stage, "attempt": x["attempt"], "ok": ok, "t": int(time.time()), **detail})
    print(f"[진행] 시도 {x['attempt']} · {stage} · {'OK' if ok else 'FAIL ' + str(detail.get('reason', ''))[:160]}", file=sys.stderr, flush=True)
    if not ok:
        x["fail"] = {"stage": stage, **detail}
    return x


def _fix_random(code):
    """'random = random.Random(seed)' 이름 겹침 자동 수정(2026-11-05 배치에서 반복된 오류)."""
    if re.search(r"^\s*random\s*=\s*random\.Random\(", code or "", re.M):
        code = re.sub(r"\brandom\b", "_rng", code).replace("_rng.Random(", "__rand_mod.Random(")
        code = "import random as __rand_mod\n" + code
    return code


def _run(code, func, arg_lists, expected=None, wait=30):
    tests = [{"args": a, "expected": (expected[i] if expected is not None else None)} for i, a in enumerate(arg_lists)]
    return coding.sandbox_run(code, func, tests, wait=wait)


def _lit(got):
    try:
        return True, ast.literal_eval(got)
    except Exception:
        return False, None


# ---------------- 검증 1: 실행 ----------------
def _norm_code(code):
    """모양만 정리: 맨 위 주석 줄(예: '# 버그: …') 아래 코드가 통째로 들여쓰기돼 있으면 들여쓰기를 걷어 낸다. 내용은 바꾸지 않는다."""
    import textwrap
    lines = (code or "").replace("\r", "").split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i += 1
    head = [l.strip() for l in lines[:i] if l.strip()]
    body = textwrap.dedent("\n".join(lines[i:]))
    return ("\n".join(head) + "\n" if head else "") + body


def v1_static(x):
    if x.get("fail"):
        return x
    d = x["draft"]
    for key in ("solution", "brute", "validator", "gen_examples", "gen_edge", "gen_random", "gen_large"):
        d[key] = _norm_code(d.get(key))
    d["wrong_solutions"] = [_norm_code(w) for w in (d.get("wrong_solutions") or [])]
    func = (d.get("func_name") or "").strip()
    bad = []
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", func):
        bad.append("func_name이 영어 snake_case가 아님")
    params = d.get("params") or []
    if not (1 <= len(params) <= 3) or not all(re.fullmatch(r"[a-z_][a-z0-9_]*", p or "") for p in params):
        bad.append("params는 영어 이름 1~3개")
    need = {"solution": func, "brute": func, "validator": "validate", "gen_examples": "gen_examples",
            "gen_edge": "gen_edge", "gen_random": "gen_random", "gen_large": "gen_large"}
    for key, name in need.items():
        if not re.search(rf"^def\s+{re.escape(name)}\s*\(", d.get(key) or "", re.M):
            bad.append(f"{key}에 def {name}( 가 없음")
    ws = d.get("wrong_solutions") or []
    if len(ws) != 2 or not all(re.search(rf"^def\s+{re.escape(func)}\s*\(", w or "", re.M) for w in ws):
        bad.append(f"wrong_solutions는 def {func}( 로 시작하는 코드 2개")
    for key in list(need) + ["wrong_solutions"]:
        blob = "\n".join(ws) if key == "wrong_solutions" else (d.get(key) or "")
        m = FORBIDDEN.search(blob)
        if m:
            bad.append(f"{key}에 금지된 호출: {m.group(0).strip()}")
        try:
            for part in (ws if key == "wrong_solutions" else [blob]):
                ast.parse(part)
        except SyntaxError as e:
            bad.append(f"{key} 문법 오류: {e.msg} (줄 {e.lineno})")
    d["gen_random"], d["gen_large"] = _fix_random(d.get("gen_random")), _fix_random(d.get("gen_large"))
    return _event(x, "V1_static", not bad, **({"reason": "; ".join(bad)} if bad else {}))


PROBE = '''
import json
{validator}

{gen_examples}

{gen_edge}

{gen_random}

def __probe(kind, i):
    if kind == "count":
        return json.dumps([len(gen_examples()), len(gen_edge())])
    if kind == "ex":
        a = gen_examples()[i]
    elif kind == "edge":
        a = gen_edge()[i]
    else:
        a = gen_random(i)
    # 형식만 맞춘다: 매개변수가 1개인데 값만 돌려준 경우([1,2,3] → [[1,2,3]]). 값은 바꾸지 않는다
    if {np} == 1 and not (isinstance(a, (list, tuple)) and len(a) == 1):
        a = [a]
    a = list(a)
    ok = bool(validate(*a))
    # 매개변수 1개일 때 [5]가 'nums=5'인지 'nums=[5]'인지 모호하다 → 그대로는 실패하고 감싸면 통과할 때만 감싼 쪽으로 본다
    if not ok and {np} == 1:
        try:
            if validate(a):
                a, ok = [a], True
        except Exception:
            pass
    return json.dumps({{"ok": ok, "args": a}}, ensure_ascii=False)
'''


def v2_inputs(x):
    if x.get("fail"):
        return x
    d = x["draft"]
    code = PROBE.format(np=len(d["params"]), **{k: d[k] for k in ("validator", "gen_examples", "gen_edge", "gen_random")})
    r = _run(code, "__probe", [["count", 0]])
    if r.get("status") != "ok" or not r.get("results") or "error" in r["results"][0]:
        err = (r.get("results") or [{}])[0].get("error") or r.get("stderr", "")
        return _event(x, "V2_inputs", False, reason=f"입력 생성 함수 실행 실패: {str(err)[:200]}")
    ok, raw = _lit(r["results"][0]["got"])
    n_ex, n_edge = json.loads(raw) if ok else (0, 0)
    if n_ex != 3:
        return _event(x, "V2_inputs", False, reason=f"gen_examples가 {n_ex}개를 반환(정확히 3개)")
    if n_edge < MIN_EDGE:
        return _event(x, "V2_inputs", False, reason=f"gen_edge가 {n_edge}개를 반환(최소 {MIN_EDGE}개)")
    calls = [["ex", i] for i in range(3)] + [["edge", i] for i in range(min(n_edge, 10))] + [["rand", s] for s in range(1, N_RANDOM + 1)]
    r = _run(code, "__probe", calls, wait=40)
    if r.get("status") != "ok":
        return _event(x, "V2_inputs", False, reason=f"입력 생성 실행 실패: {r.get('status')} {r.get('stderr', '')[:150]}")
    ex, edge, rand, seen, too_long = [], [], [], set(), 0
    drop = {"edge_long": 0, "edge_dup": 0}
    for (kind, i), res in zip(calls, r["results"]):
        if "error" in res:
            hint = (f" — 각 입력은 매개변수 {len(d['params'])}개({', '.join(d['params'])})를 순서대로 담은 list여야 함"
                    if "positional argument" in res["error"] else "")
            return _event(x, "V2_inputs", False, reason=f"{kind}[{i}] 생성 오류: {res['error'][:150]}{hint}")
        ok, raw = _lit(res["got"])
        if not ok:                                         # 실행기 출력 300자 제한에 걸림 = 입력이 너무 큼
            if kind == "ex":
                return _event(x, "V2_inputs", False, reason=f"예시 {i + 1} 입력이 너무 김(직렬화 300자 이하로)")
            too_long += 1                                  # 경계·무작위의 큰 입력은 버린다 — 최대 크기는 V7에서 따로 검사
            if kind == "edge":
                drop["edge_long"] += 1
            continue
        item = json.loads(raw)
        if not item["ok"]:
            return _event(x, "V2_inputs", False, reason=f"{kind}[{i}] 입력이 validate()를 통과하지 못함(제약 조건 위반): {json.dumps(item['args'], ensure_ascii=False)[:120]}")
        if len(item["args"]) != len(d["params"]):
            return _event(x, "V2_inputs", False, reason=f"{kind}[{i}] 인자 수 {len(item['args'])} ≠ 매개변수 수 {len(d['params'])}")
        key = json.dumps(item["args"], sort_keys=True)
        if kind == "ex":
            ex.append(item["args"])
        elif key not in seen:
            (edge if kind == "edge" else rand).append(item["args"])
        elif kind == "edge":
            drop["edge_dup"] += 1
        seen.add(key)
    if len(edge) < MIN_EDGE:
        return _event(x, "V2_inputs", False, reason=(
            f"쓸 수 있는 경계 입력이 {len(edge)}개(최소 {MIN_EDGE}개). 버린 것: 너무 김 {drop['edge_long']}개, 예시와 같거나 중복 {drop['edge_dup']}개. "
            "gen_edge에는 예시와 다른 짧은 경계값(직렬화 200자 이하, 원소 10개 이하)을 4개 이상 — 최대 크기 입력은 gen_large에만"))
    if len(rand) < MIN_RANDOM:
        return _event(x, "V2_inputs", False, reason=f"쓸 수 있는 무작위 입력이 {len(rand)}개(최소 {MIN_RANDOM}개, 너무 김 {too_long}개·중복 제외)")
    x["inputs"] = {"examples": ex, "edge": edge, "random": rand}
    return _event(x, "V2_inputs", True, examples=len(ex), edge=len(edge), random=len(rand), dropped_too_long=too_long)


def v3_expected(x):
    if x.get("fail"):
        return x
    d, inp = x["draft"], x["inputs"]
    groups = [("ex", a) for a in inp["examples"]] + [("edge", a) for a in inp["edge"]] + [("rand", a) for a in inp["random"]]
    r = _run(d["solution"], d["func_name"], [a for _, a in groups], wait=40)
    if r.get("status") != "ok":
        return _event(x, "V3_expected", False, reason=f"기준 풀이 실행 실패: {r.get('status')} {r.get('stderr', '')[:200]}")
    cases = []
    for (kind, a), res in zip(groups, r["results"]):
        if "error" in res:
            return _event(x, "V3_expected", False, reason=f"기준 풀이 오류: 입력 {json.dumps(a, ensure_ascii=False)[:100]} → {res['error'][:150]}")
        ok, val = _lit(res["got"])
        if not ok:
            if kind == "ex":
                return _event(x, "V3_expected", False, reason="예시 출력이 너무 김(300자 이하)")
            continue
        try:
            same = json.loads(json.dumps(val)) == val and not isinstance(val, (tuple, set, dict))
        except (TypeError, ValueError):
            same = False
        if not same:
            return _event(x, "V3_expected", False, reason=f"반환 값 형식 금지(tuple·set·dict 등): {res['got'][:60]}")
        cases.append({"kind": kind, "args": a, "expected": val})
    want = (x.get("spec") or {}).get("structure")
    if want and "자유" not in want:                       # 프로그래머스 framework의 입출력 구조를 지켰는지
        from .coding_taxonomy import signature
        sig, fam = signature([{"args": c["args"], "expected": c["expected"]} for c in cases if c["kind"] == "ex"], d["params"])
        if fam != want:
            return _event(x, "V3_expected", False, reason=f"입출력 구조가 명세와 다름: 명세 '{want}' · 실제 '{fam}' ({sig}). 매개변수·반환 타입을 명세 구조로 맞출 것")
    x["cases"] = cases
    return _event(x, "V3_expected", True, cases=len(cases), structure=want)


def v4_brute(x):
    if x.get("fail"):
        return x
    d, cases = x["draft"], x["cases"]
    r = _run(d["brute"], d["func_name"], [c["args"] for c in cases], [c["expected"] for c in cases], wait=40)
    res = r.get("results", [])
    bad = [c for c, o in zip(cases, res) if not o.get("ok")]
    if r.get("status") != "ok" or len(res) != len(cases) or bad:
        ex = f" 예: 입력 {json.dumps(bad[0]['args'], ensure_ascii=False)[:100]} 기준 {json.dumps(bad[0]['expected'], ensure_ascii=False)[:40]}" if bad else ""
        return _event(x, "V4_brute", False, reason=f"단순 풀이와 기준 풀이 불일치 {len(bad)}/{len(cases)}{ex} {r.get('stderr', '')[:100]}")
    return _event(x, "V4_brute", True, agreed=len(cases))


def _baselines():
    def num_list(v):
        return isinstance(v, list) and v and all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in v)
    return [
        *[(f"상수 {json.dumps(c)}", (lambda c: lambda a: c)(c)) for c in (0, 1, -1, True, False, "", [], None)],
        ("첫 인자 그대로", lambda a: a[0]),
        ("마지막 인자 그대로", lambda a: a[-1]),
        ("첫 인자 길이", lambda a: len(a[0]) if hasattr(a[0], "__len__") else None),
        ("첫 인자 합", lambda a: sum(a[0]) if num_list(a[0]) else None),
        ("첫 인자 최댓값", lambda a: max(a[0]) if num_list(a[0]) else None),
        ("첫 인자 최솟값", lambda a: min(a[0]) if num_list(a[0]) else None),
        ("첫 인자 정렬", lambda a: sorted(a[0]) if num_list(a[0]) else None),
        ("첫 인자 원소 수(중복 제거)", lambda a: len(set(map(json.dumps, a[0]))) if isinstance(a[0], list) else None),
    ]


def v5_trivial(x):
    if x.get("fail"):
        return x
    cases = x["cases"]
    distinct = len({json.dumps(c["expected"], sort_keys=True) for c in cases})
    if distinct < 3:
        return _event(x, "V5_trivial", False, reason=f"출력 종류가 {distinct}개뿐(답이 거의 고정된 문제)")
    for name, f in _baselines():
        try:
            if all(f(c["args"]) == c["expected"] for c in cases):
                return _event(x, "V5_trivial", False, reason=f"자명한 풀이('{name}')가 모든 테스트를 통과 — 문제가 너무 단순하거나 테스트가 약함")
        except Exception:
            continue
    return _event(x, "V5_trivial", True, distinct_outputs=distinct)


MUTATIONS = [(" < ", " <= "), (" <= ", " < "), (" > ", " >= "), (" >= ", " > "), (" == ", " != "), (" + 1", ""),
             (" - 1", ""), ("range(1,", "range(0,"), (" and ", " or "), ("min(", "max("), ("max(", "min(")]


def _mutants(code):
    out = []
    for a, b in MUTATIONS:
        i = code.find(a)
        if i < 0:
            continue
        m = code[:i] + b + code[i + len(a):]
        try:
            ast.parse(m)
        except SyntaxError:
            continue
        if m != code and m not in out:
            out.append(m)
        if len(out) >= 6:
            break
    return out


def v6_wrong(x):
    if x.get("fail"):
        return x
    d, cases = x["draft"], x["cases"]
    args, exp = [c["args"] for c in cases], [c["expected"] for c in cases]

    def killed(code):
        r = _run(code, d["func_name"], args, exp, wait=40)
        return r.get("status") != "ok" or not all(o.get("ok") for o in r.get("results", [])) or len(r.get("results", [])) != len(cases)

    survivors = [i + 1 for i, w in enumerate(d["wrong_solutions"]) if not killed(w)]
    if survivors:
        w = d["wrong_solutions"][survivors[0] - 1]
        return _event(x, "V6_wrong", False, reason=(
            f"틀린 풀이 {survivors}번이 경계·무작위 입력 {len(cases)}개 모두에서 정답과 같음 — 사실상 맞는 다른 구현일 가능성이 큼. "
            "wrong_solutions를 solution 복사본에 버그 1개(부등호·경계·초기값·동점 처리)를 일부러 넣은 코드로 바꾸고 '# 버그:' 주석을 달 것"
            "(필요하면 그 버그를 드러내는 경계 입력도 gen_edge에 추가). "
            f"통과해 버린 풀이: {' '.join(w.split())[:400]}"))
    muts = _mutants(d["solution"])
    kills = sum(1 for m in muts if killed(m))
    rate = round(kills / len(muts), 2) if muts else None
    if muts and len(muts) >= 3 and rate < MUTANT_KILL_MIN:
        return _event(x, "V6_wrong", False, reason=f"자동 변이 {len(muts)}개 중 {kills}개만 잡음({rate}) — 경계 테스트 보강 필요",
                      mutants=len(muts), killed=kills)
    return _event(x, "V6_wrong", True, wrong_killed=2, mutants=len(muts), mutants_killed=kills, mutation_score=rate)


def v7_large(x):
    if x.get("fail"):
        return x
    d = x["draft"]
    code = (d["solution"] + "\n\n" + d["validator"] + "\n\nimport random\n" + d["gen_large"] +
            f"\n\ndef __perf(seed):\n    a = gen_large(seed)\n    if {len(d['params'])} == 1 and not (isinstance(a, (list, tuple)) and len(a) == 1):\n        a = [a]\n    a = list(a)\n    if not validate(*a):\n        info = []\n        for v in a:\n            if isinstance(v, (list, str)):\n                flat = [t for t in v if isinstance(t, (int, float))] if isinstance(v, list) else []\n                info.append(f'len={{len(v)}}' + (f' min={{min(flat)}} max={{max(flat)}} distinct={{len(set(flat))}}' if flat else ''))\n            else:\n                info.append(f'value={{v!r}}'[:60])\n        return 'INVALID ' + '; '.join(info)\n    {d['func_name']}(*a)\n    return True\n")
    r = _run(code, "__perf", [[1], [2]], [True, True], wait=40)
    res = r.get("results", [])
    if r.get("status") != "ok" or len(res) != 2 or not all(o.get("ok") for o in res):
        why = "; ".join((o.get("error") or o.get("got") or "")[:80] for o in res if not o.get("ok"))
        return _event(x, "V7_large", False, reason=f"최대 입력 검사 실패: {r.get('status')} {why} {r.get('stderr', '')[:100]}"
                      + (" — gen_large가 만든 입력이 validate()를 통과하지 못함(위 len·min·max·distinct 참고). 최대 길이·값 범위를 넘지 않는지, "
                         "'중복 없음'이면 값 범위가 길이보다 넓고 서로 다른 값만 뽑는지(rng.sample 등) 확인할 것" if "INVALID" in why else ""))
    lvl, topic = (x.get("spec") or {}).get("difficulty"), (x.get("spec") or {}).get("topic", "")
    if lvl == "applied" and topic not in ("완전탐색", "백트래킹"):        # Q-06 시간 제한 판별력: Senior는 필수(2026-11-06 A/B 결과)
        from .coding_quality import discrimination
        disc = discrimination({"brute": d["brute"], "validator": d["validator"], "gen_large": d["gen_large"]}) or {}
        if not disc.get("brute_timeout"):
            return _event(x, "V7_large", False, reason=(
                f"시간 제한 판별력 부족: 단순 풀이가 최대 크기 입력에서도 시간 안에 끝남({disc.get('status')}, {disc.get('sec')}초). "
                "Senior 문제는 목표 복잡도 풀이만 통과하도록 제약(입력 크기)을 키우거나, 효율적인 알고리즘이 꼭 필요한 문제로 바꿀 것"))
        return _event(x, "V7_large", True, runs=2, discrimination=True)
    return _event(x, "V7_large", True, runs=2)


execution_verifier = (RunnableLambda(v1_static).with_config(run_name="V1_static")
                      | RunnableLambda(v2_inputs).with_config(run_name="V2_inputs")
                      | RunnableLambda(v3_expected).with_config(run_name="V3_expected")
                      | RunnableLambda(v4_brute).with_config(run_name="V4_brute")
                      | RunnableLambda(v5_trivial).with_config(run_name="V5_trivial")
                      | RunnableLambda(v6_wrong).with_config(run_name="V6_wrong_solutions")
                      | RunnableLambda(v7_large).with_config(run_name="V7_large")).with_config(run_name="coding_verifier_execution")


# ---------------- 검증 2: 내용 ----------------
def _out_strs(v):
    s = {json.dumps(v, ensure_ascii=False), json.dumps(v, ensure_ascii=False, separators=(",", ":")), str(v), repr(v)}
    if isinstance(v, bool):
        s |= {"true" if v else "false", "True" if v else "False"}
    return s


def v8_examples(x):
    if x.get("fail"):
        return x
    exs = x.get("examples") or []
    bad = [i + 1 for i, e in enumerate(exs) if not any(o in (e.get("explanation") or "") for o in _out_strs(e["expected"]))]
    if len(exs) != 3 or bad:
        return _event(x, "V8_examples", False, reason=f"예시 설명 {bad or '개수'}이(가) 계산된 출력 값을 담고 있지 않음")
    return _event(x, "V8_examples", True)


def rule_issues(d):
    t, st = (d.get("title") or "").strip(), d.get("statement") or ""
    cons = " ".join(d.get("constraints") or [])
    out = []
    if not re.search(r"[가-힣]", t) or len(t) > 40:
        out.append("제목은 한국어 40자 이하")
    for name, text in (("설명", st), ("입출력 규격", d.get("io_spec") or ""), ("제약 조건", cons)):
        m = LEAK.search(text)
        if m:
            out.append(f"{name}에 출제 지시문이 그대로 들어 있음({m.group(0)})")
        if text.count("`") % 2:
            out.append(f"{name}의 백틱(`) 짝이 맞지 않음")
    if len(d.get("constraints") or []) < 2:
        out.append("제약 조건이 2개 미만")
    if not re.search(r"빈\s*(배열|리스트|문자열|입력|목록|격자)|비어", cons):
        out.append("제약 조건에 빈 입력 허용 여부가 없음")
    if "중복" not in cons:
        out.append("제약 조건에 중복 허용 여부가 없음")
    if len(st) < 80:
        out.append("설명이 너무 짧음(80자 미만)")
    if not (d.get("io_spec") or "").strip():
        out.append("입출력 규격이 비어 있음")
    return out


def v9_rules(x):
    if x.get("fail"):
        return x
    bad = rule_issues(x["draft"])
    return _event(x, "V9_rules", not bad, **({"reason": "; ".join(bad)} if bad else {}))


def v10_review(x):
    if x.get("fail"):
        return x
    sp, d = x["spec"], x["draft"]
    system = prompts.load("evaluate/coding_problem_review", topic=sp["topic"], difficulty=sp["difficulty"],
                          level_desc=LEVEL_DESC[sp["difficulty"]], learning_objective=sp["learning_objective"],
                          target_complexity=sp["target_complexity"])
    user = (f"[제목] {d['title']}\n[설명]\n{d['statement']}\n[입출력 규격]\n{d['io_spec']}\n[제약 조건]\n- " + "\n- ".join(d["constraints"])
            + f"\n[기준 풀이]\n{d['solution']}\n[시간 복잡도] {d.get('time_complexity')}")
    try:
        r = llm.chat_json(system, user + "\n/no_think", REVIEW_SCHEMA, timeout=600, temperature=0.0, model=config.CODING_GEN_MODEL,
                          max_tokens=800, num_ctx=8192)
    except llm.LLMError as e:
        return _event(x, "V10_review", False, reason=f"검토 모델 호출 실패: {e}")
    x["review"] = r
    ok = (not r["ambiguous"]) and r["difficulty_fit"] >= 3 and r["concept_fit"] >= 3
    reason = "" if ok else ("모호함: " + "; ".join(r.get("issues") or []) if r["ambiguous"] else
                            f"난이도 적합 {r['difficulty_fit']}·주제 적합 {r['concept_fit']} (3 미만)")
    return _event(x, "V10_review", ok, **({"reason": reason} if reason else {}), difficulty_fit=r["difficulty_fit"],
                  concept_fit=r["concept_fit"], issues=r.get("issues") or [])


GEN_IDX, GEN_PREFIX = "cpgen_idx", "cpgen:"
REF_IDX, REF_PREFIX = "cpref_idx", "cpref:"


def _ensure(r, idx, prefix):
    try:
        r.ft(idx).info()
    except Exception:
        r.ft(idx).create_index([TagField("kind"), VectorField("embedding", "HNSW", {"TYPE": "FLOAT32", "DIM": config.EMBED_DIM,
                                                                                    "DISTANCE_METRIC": "COSINE"})],
                               definition=IndexDefinition(prefix=[prefix], index_type=IndexType.HASH))


def _knn(r, idx, vec, k=3):
    q = Query("*=>[KNN %d @embedding $v AS dist]" % k).sort_by("dist").return_fields("dist").dialect(2).paging(0, k)
    try:
        res = r.ft(idx).search(q, query_params={"v": vectorstore.vec(vec)})
    except Exception:
        return []
    return [(d.id, round(1 - float(d.dist), 4)) for d in res.docs]


def _sync_reference(r):
    """수집한 문제 설명의 벡터(유사도 비교 전용, 생성 입력에는 쓰지 않음). 없는 것만 임베딩."""
    _ensure(r, REF_IDX, REF_PREFIX)
    rows = db.all_("SELECT source, source_id, title, statement FROM collected_problems")
    todo = [x for x in rows if not r.exists(f"{REF_PREFIX}{x['source']}:{x['source_id']}")]
    for i in range(0, len(todo), 32):
        part = todo[i:i + 32]
        vecs = llm.embed([f"{p['title']}\n{(p['statement'] or '')[:1500]}" for p in part])
        pipe = r.pipeline(transaction=False)
        for p, v in zip(part, vecs):
            pipe.hset(f"{REF_PREFIX}{p['source']}:{p['source_id']}", mapping={"kind": "ref", "embedding": vectorstore.vec(v)})
        pipe.execute()
    return len(todo)


def v11_similarity(x):
    if x.get("fail"):
        return x
    d = x["draft"]
    r = vectorstore.client()
    try:
        _ensure(r, GEN_IDX, GEN_PREFIX)
        _sync_reference(r)
        vec = llm.embed([f"{d['title']}\n{d['statement'][:1500]}"])[0]
    except llm.LLMError as e:
        return _event(x, "V11_similarity", False, reason=f"임베딩 실패: {e}")
    gen, ref = _knn(r, GEN_IDX, vec), _knn(r, REF_IDX, vec)
    x["embedding"] = vec
    top_gen, top_ref = (gen[0][1] if gen else 0.0), (ref[0][1] if ref else 0.0)
    ok = top_gen < SIM_DUP and top_ref < SIM_SOURCE
    reason = "" if ok else ("이미 만든 자체 문제와 중복" if top_gen >= SIM_DUP else "수집한 외부 문제와 설명이 너무 비슷 — 새로 설계할 것")
    return _event(x, "V11_similarity", ok, **({"reason": reason} if reason else {}), top_generated=top_gen, top_collected=top_ref,
                  note="참고 지표: 유사도는 독창성·법적 적합성을 확정하지 않음")


content_verifier = (RunnableLambda(v8_examples).with_config(run_name="V8_examples")
                    | RunnableLambda(v9_rules).with_config(run_name="V9_rules")
                    | RunnableLambda(v10_review).with_config(run_name="V10_ai_review")
                    | RunnableLambda(v11_similarity).with_config(run_name="V11_similarity_reference")).with_config(run_name="coding_verifier_content")

verifier_agent = (execution_verifier | content_verifier).with_config(run_name="coding_verifier_agent")
