"""코딩 문제 품질 채점 · 모델 A/B (2026-11-06 사용자 지시). 외부 API는 부르지 않는다(로컬 실행기·로컬 Qwen·Redis만).

품질 기준표 Q-01~Q-09 = 사용자 기준 3개 + 업계 출제 기준(HackerEarth·CodeChef 출제 가이드, Polygon·testlib 실무):
    Q-01 통계·개념 충실도   입출력 구조 일치(필수) + 레벨에 맞는 풀이 복잡도 + 짝 비교(로컬 Qwen)에서 '개념이 실제로 필요한가'
    Q-02 문장 자연스러움     규칙 점수(번역투·문장 길이·영어 비율·끝맺음 일관) + 짝 비교 '자연스러움'
    Q-03 독창적 재해석       수집 문제와 최대 임베딩 유사도 + 가장 가까운 수집 문제와 3글자 겹침률 + 묶음 안 중복(참고 지표 — 법적 판단 아님)
    Q-04 지문 명확성         검토 모호함 없음 + 예시 설명이 실제 출력과 일치 + 짝 비교 '명확성'
    Q-05 테스트 커버리지     테스트 수 · 경계 입력 수 · 틀린 풀이 검출 · 자동 변이 사망률
    Q-06 시간 제한 판별력    최대 크기 입력에서 단순 풀이는 시간 초과, 기준 풀이는 통과(느린 풀이를 걸러 내는가)
    Q-07 입력 검증           모든 입력이 validator 통과(V2·V7)
    Q-08 난이도 보정         Junior/Senior 레벨과 기준 풀이 규모(줄·반복·분기) 일치, 검토 난이도 적합
    Q-09 생성 비용·시간       문제당 토큰·초·시도

    python -m app.agents.coding_quality ab gpt-5.5 gpt-6.1-sol      # 두 모델 비교 JSON
    python -m app.agents.coding_quality score gpt-6.1-sol            # 한 모델 문제별 점수
"""
import ast
import json
import re
import sys
import time

from .. import coding, config, db, llm, vectorstore
from .coding_verifier_agent import REF_IDX, REF_PREFIX, _knn

CRITERIA = [
    ("Q-01", "통계·개념 충실도", "사용자 기준", "구조 일치(필수) · 레벨-복잡도 · 짝 비교 '개념 필요'", "자동 + 로컬 Qwen", "구조 100% 일치, 짝 비교 우세", 20),
    ("Q-02", "문장 자연스러움(eloquence)", "사용자 기준", "규칙 점수 0~100 + 짝 비교 '자연스러움'", "자동 + 로컬 Qwen + 사람 1~5", "규칙 80 이상", 15),
    ("Q-03", "독창적 재해석", "사용자 기준 · HackerEarth(다른 사이트 문제 금지, 해답이 쉽게 검색되지 않을 것)", "수집 문제 최대 유사도 · 3글자 겹침률 · 묶음 안 중복", "자동 + 사람 확인", "유사도 0.90 미만, 겹침률 0.30 미만", 20),
    ("Q-04", "지문 명확성", "HackerEarth(모호하지 않은 지문)", "검토 모호함 0 · 예시 설명=실제 출력 · 짝 비교 '명확성'", "자동 + 로컬 Qwen", "모호함 0, 예시 일치 100%", 15),
    ("Q-05", "테스트 커버리지·강도", "HackerEarth(경계 사례 모두) · CodeChef(약한 테스트 보강)", "테스트 수 · 경계 수 · 틀린 풀이 검출 · 변이 사망률", "자동", "테스트 30개 이상, 틀린 풀이 2/2, 변이 0.5 이상", 10),
    ("Q-06", "시간 제한 판별력", "HackerEarth(최대 입력으로 느린 풀이 시간 초과)", "최대 입력에서 단순 풀이 시간 초과 · 기준 풀이 통과", "자동(실행기)", "단순 풀이 시간 초과", 10),
    ("Q-07", "입력 검증", "Polygon·testlib 실무(validator·generator)", "모든 생성 입력 validate()=True", "자동", "100%", 5),
    ("Q-08", "난이도 보정", "HackerEarth·CodeChef(출제자·검수자 난이도 합의)", "레벨별 기준 풀이 규모 · 검토 난이도 적합", "자동 + 로컬 Qwen + 사람", "레벨 범위 안", 5),
    ("Q-09", "생성 비용·시간", "운영 기준", "문제당 토큰 · 초 · 시도", "자동", "참고(모델 비교용)", 0),
]

TRANSLATIONESE = [r"당신", r"그것은", r"에 대해서", r"를 가지고 있", r"되어진", r"하는 것이다", r"함으로써", r"에 의해", r"것을 반환"]


def _rows(model):
    return db.all_("""SELECT id, level, concept, spec, title, statement, io_spec, constraints, examples, solution, brute, gen_large,
                             validator, tests, wrong_solutions, verification_log v, verification_summary s, quality_status, created_at
                      FROM generated_problems WHERE model=%s ORDER BY id""", model)


def eloquence(text):
    """문장 자연스러움 규칙 점수(0~100). 사람 평가를 대신하지 않는다."""
    body = re.sub(r"`[^`]*`|\[[^\]]*\]|[A-Za-z_]+\[[^\]]*\]", " ", text or "")
    sents = [s.strip() for s in re.split(r"(?<=[.?!다요])\s+", body) if len(s.strip()) > 3]
    if not sents:
        return 0, {}
    avg = sum(len(s) for s in sents) / len(sents)
    long_ratio = sum(len(s) > 120 for s in sents) / len(sents)
    tr = sum(len(re.findall(p, body)) for p in TRANSLATIONESE)
    letters = re.findall(r"[가-힣A-Za-z]", body)
    eng = sum(c.isascii() for c in letters) / max(1, len(letters))
    endings = {"다": sum(s.endswith("다.") or s.endswith("다") for s in sents), "요": sum(s.endswith("요.") or s.endswith("요") for s in sents)}
    mixed = min(endings.values()) / max(1, sum(endings.values()))
    score = 100 - min(30, tr * 6) - min(25, long_ratio * 60) - (15 if avg > 90 else 0) - min(15, max(0, eng - 0.25) * 60) - min(15, mixed * 60)
    return round(max(0, score)), {"avg_len": round(avg), "long_ratio": round(long_ratio, 2), "translationese": tr,
                                  "english_ratio": round(eng, 2), "ending_mix": round(mixed, 2)}


def _grams(s, n=3):
    s = re.sub(r"\s+", "", s or "")
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def originality(p):
    r = vectorstore.client()
    vec = llm.embed([f"{p['title']}\n{p['statement'][:1500]}"])[0]
    near = _knn(r, REF_IDX, vec, k=1)
    sim, overlap, near_id = 0.0, 0.0, None
    if near:
        near_id, sim = near[0][0][len(REF_PREFIX):], near[0][1]
        src, sid = near_id.split(":", 1)
        row = db.one("SELECT statement FROM collected_problems WHERE source=%s AND source_id=%s", src, sid)
        a, b = _grams(p["statement"]), _grams((row or {}).get("statement", ""))
        overlap = round(len(a & b) / max(1, len(a)), 3)
    return {"sim_collected": sim, "trigram_overlap": overlap, "nearest": near_id}, vec


def code_size(code):
    try:
        t = ast.parse(code or "")
    except SyntaxError:
        return {}
    loops = sum(isinstance(n, (ast.For, ast.While, ast.comprehension)) for n in ast.walk(t))
    branches = sum(isinstance(n, (ast.If, ast.IfExp)) for n in ast.walk(t))
    lines = len([l for l in (code or "").splitlines() if l.strip() and not l.strip().startswith("#")])
    return {"lines": lines, "loops": loops, "branches": branches}


def discrimination(p):
    """최대 크기 입력에서 단순 풀이가 시간 초과되는가(기준 풀이는 V7에서 통과 확인됨)."""
    code = (p["brute"] + "\n\n" + p["validator"] + "\n\nimport random\n" + p["gen_large"] +
            "\n\ndef __slow(seed):\n    a = gen_large(seed)\n    if not (isinstance(a, (list, tuple))):\n        a = [a]\n    a = list(a)\n"
            "    try:\n        validate(*a)\n    except TypeError:\n        a = [a]\n    __f(*a)\n    return True\n")
    fn = re.search(r"^def\s+(\w+)\s*\(", p["brute"], re.M)
    if not fn:
        return None
    code = code.replace("__f(", fn.group(1) + "(")
    t = time.time()
    r = coding.sandbox_run(code, "__slow", [{"args": [1], "expected": True}], wait=25)
    return {"brute_timeout": r.get("status") == "timeout", "status": r.get("status"), "sec": round(time.time() - t, 1)}


def score(model):
    out = []
    for p in _rows(model):
        v = {h["stage"]: h for h in (p["v"] or []) if h.get("ok")}
        s = p["s"] or {}
        rv = s.get("review") or {}
        tests = p["tests"] or []
        edge_n = (v.get("V2_inputs") or {}).get("edge")
        w = v.get("V6_wrong") or {}
        elo, elo_d = eloquence(p["statement"] + " " + (p["io_spec"] or ""))
        orig, vec = originality(p) if p["statement"] else ({}, None)
        cs = code_size(p["solution"])
        disc = discrimination(p) if p["quality_status"] == "verified" else None
        out.append({
            "id": p["id"], "level": "Junior" if p["level"] == "basic" else "Senior", "concept": p["concept"], "title": p["title"],
            "verified": p["quality_status"] == "verified", "structure": (p["spec"] or {}).get("structure"),
            "structure_ok": "V3_expected" in v, "attempts": s.get("attempts"), "sec": s.get("elapsed_sec"),
            "tokens": ((s.get("cost") or {}).get("prompt_tokens", 0) + (s.get("cost") or {}).get("completion_tokens", 0)),
            "eloquence": elo, "eloquence_detail": elo_d, **orig,
            "review_ambiguous": bool(rv.get("ambiguous")), "review_difficulty": rv.get("difficulty_fit"), "review_concept": rv.get("concept_fit"),
            "examples_ok": "V8_examples" in v, "tests": len(tests), "edge": edge_n,
            "wrong_killed": w.get("wrong_killed"), "mutation": w.get("mutation_score"),
            "validator_ok": "V2_inputs" in v and "V7_large" in v, "discrimination": disc, "code": cs,
            "_vec": vec,
        })
    for a in out:                                      # 묶음 안 중복(같은 모델이 만든 다른 문제와 최대 유사도)
        a["sim_in_batch"] = round(max([sum(x * y for x, y in zip(a["_vec"], b["_vec"])) for b in out if b is not a and b["_vec"] and a["_vec"]] or [0]), 3)
    for a in out:
        a.pop("_vec", None)
    return out


PAIR_SCHEMA = {"type": "object", "properties": {k: {"type": "string", "enum": ["A", "B", "같음"]} for k in ("natural", "concept", "clarity")} |
               {"reason": {"type": "string"}}, "required": ["natural", "concept", "clarity", "reason"]}
PAIR_SYS = ("너는 코딩테스트 문제 검수위원이다. 같은 출제 명세로 만든 두 문제 A와 B를 비교한다.\n"
            "natural: 한국어 지문이 더 자연스럽고 읽기 쉬운 쪽(번역투·어색한 문장이 적은 쪽).\n"
            "concept: 명세 개념이 풀이에 실제로 더 필요한 쪽(그 개념 없이 쉽게 풀리면 불리).\n"
            "clarity: 조건·입출력이 더 모호하지 않고 명확한 쪽.\n차이가 없으면 '같음'. reason은 한 문장.")


def pairwise(a, b, spec):
    def ask(x, y):
        user = (f"[명세] 개념 {spec.get('topic')} · 레벨 {spec.get('difficulty')} · 입출력 구조 {spec.get('structure')}\n\n"
                f"[A] {x['title']}\n{x['statement'][:1400]}\n\n[B] {y['title']}\n{y['statement'][:1400]}\n/no_think")
        return llm.chat_json(PAIR_SYS, user, PAIR_SCHEMA, timeout=600, temperature=0.0, model=config.CODING_GEN_MODEL, max_tokens=600, num_ctx=8192)
    r1, r2 = ask(a, b), ask(b, a)                       # 위치 편향을 줄이려고 순서를 바꿔 두 번
    flip = {"A": "B", "B": "A", "같음": "같음"}
    res = {}
    for k in ("natural", "concept", "clarity"):
        v1, v2 = r1[k], flip[r2[k]]
        res[k] = v1 if v1 == v2 else "같음"              # 두 번이 엇갈리면 무승부
    res["reason"] = r1.get("reason", "")[:150]
    return res


def ab(model_a, model_b):
    A, B = score(model_a), score(model_b)
    rows_a, rows_b = _rows(model_a), _rows(model_b)
    pairs = []
    used = set()
    for ra in rows_a:                                   # 같은 명세(개념·레벨·구조)끼리 짝
        key = ((ra["spec"] or {}).get("topic"), (ra["spec"] or {}).get("difficulty"), (ra["spec"] or {}).get("structure"))
        rb = next((x for x in rows_b if x["id"] not in used and ((x["spec"] or {}).get("topic"), (x["spec"] or {}).get("difficulty"),
                                                                    (x["spec"] or {}).get("structure")) == key and x["statement"]), None)
        if not rb or not ra["statement"]:
            continue
        used.add(rb["id"])
        try:
            j = pairwise(ra, rb, ra["spec"] or {})
        except llm.LLMError as e:
            j = {"natural": "-", "concept": "-", "clarity": "-", "reason": f"판정 실패: {e}"}
        pairs.append({"a_id": ra["id"], "b_id": rb["id"], "spec": " / ".join(str(k) for k in key), **j})

    def agg(S):
        ok = [x for x in S if x["verified"]]
        n = len(S) or 1
        mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None   # noqa: E731
        return {
            "generated": len(S), "verified": len(ok), "pass_rate": round(len(ok) / n, 3),
            "first_try": round(sum(1 for x in ok if x["attempts"] == 1) / n, 3),
            "structure_ok": round(sum(x["structure_ok"] for x in ok) / max(1, len(ok)), 3),
            "eloquence": mean([x["eloquence"] for x in ok]),
            "sim_collected_max": max([x.get("sim_collected") or 0 for x in ok] or [0]),
            "trigram_overlap_max": max([x.get("trigram_overlap") or 0 for x in ok] or [0]),
            "sim_in_batch_max": max([x["sim_in_batch"] for x in ok] or [0]),
            "ambiguous": sum(x["review_ambiguous"] for x in ok), "examples_ok": round(sum(x["examples_ok"] for x in ok) / max(1, len(ok)), 3),
            "tests_mean": mean([x["tests"] for x in ok]), "edge_mean": mean([x["edge"] or 0 for x in ok]),
            "mutation_mean": mean([x["mutation"] for x in ok if x["mutation"] is not None]),
            "discrimination": round(sum(1 for x in ok if (x["discrimination"] or {}).get("brute_timeout")) / max(1, len(ok)), 3),
            "validator_ok": round(sum(x["validator_ok"] for x in ok) / max(1, len(ok)), 3),
            "senior_lines": mean([x["code"].get("lines", 0) for x in ok if x["level"] == "Senior"]),
            "junior_lines": mean([x["code"].get("lines", 0) for x in ok if x["level"] == "Junior"]),
            "sec_mean": mean([x["sec"] for x in S if x["sec"]]), "tokens_per_verified": round(sum(x["tokens"] for x in S) / max(1, len(ok))),
        }
    wins = {m: {k: sum(1 for p in pairs if p[k] == m) for k in ("natural", "concept", "clarity")} for m in ("A", "B", "같음")}
    return {"a": model_a, "b": model_b, "A": agg(A), "B": agg(B), "pairs": pairs, "wins": wins, "detail_a": A, "detail_b": B,
            "criteria": CRITERIA, "judge": config.CODING_GEN_MODEL}


def main(argv=None):
    argv = argv or sys.argv[1:]
    db.pool.open()
    if argv[0] == "ab":
        out = ab(argv[1], argv[2])
    else:
        out = score(argv[1])
    print(json.dumps(out, ensure_ascii=False, default=str))


if __name__ == "__main__":
    sys.exit(main())
