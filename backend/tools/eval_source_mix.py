"""질문 출처 섞임 지표 — M-55(사내문서 출처 비율 ≥ 0.80) · M-58(Redis 비율 < 0.20) · M-56.
ADLC v0.8 6.19.2, 2026-11-17 사용자 지시("Redis는 지원자에게 가는 질문의 20% 미만").

모델·DB·Chroma를 쓰지 않는다 — 저장된 생성 결과(JSON)의 `gen_method` 문자열만 센다.

    python -m tools.eval_source_mix --gen documents/03_QA_doc/eval_runs/sim_applicants_2026-11-16/gen.json
    python -m tools.eval_source_mix --gen <gen.json> --out <결과.json>

출처 판정 규칙(`question_graph._bank_fields` · `finalize`가 쓰는 형식):
    internal_q:<질문 ID>        사내문서 질문(A17)            → 사내문서 출처
    …|internal:<조각 ID>,…      조각 근거가 최종 문장까지 남음  → 사내문서 출처
    redis:<문서 ID>             Redis 질문 은행               → Redis 출처
    character:<CA-ID>           인성 md                      → 파일 은행
    db · fallback_job           DB question_bank             → DB 은행
    llm · template              이력서 기반 질문(RSQ)          → 이력서
"""
import argparse
import collections
import json
import os
import sys

M55_TARGET = 0.80       # 사내문서 출처 비율 목표
M58_LIMIT = 0.20        # Redis 비율 상한(미만)


def classify(gen_method):
    """(출처 종류, 사내문서 여부, Redis 여부)."""
    gm = gen_method or ""
    head = gm.split("|")[0]
    has_internal_ref = "internal:" in gm
    if head.startswith("internal_q:"):
        return "internal_q", True, False
    if head.startswith("redis:"):
        return "redis", has_internal_ref, True
    if head.startswith("character:"):
        return "character_md", has_internal_ref, False
    if head in ("db", "fallback_job"):
        return "db_bank", has_internal_ref, False
    if head in ("llm", "template"):
        return "resume", has_internal_ref, False
    return head or "-", has_internal_ref, False


def summarize(people):
    """사람 목록(각 {questions:[{category, gen_method}]}) → 지표."""
    kinds = collections.Counter()
    by_cat = collections.Counter()
    n = internal = redis = 0
    per_person = []
    for p in people:
        qs = p.get("questions") or []
        pi = pr = 0
        for q in qs:
            kind, is_int, is_redis = classify(q.get("gen_method"))
            kinds[kind] += 1
            by_cat[(q.get("category") or "-", kind)] += 1
            n += 1
            internal += is_int
            redis += is_redis
            pi += is_int
            pr += is_redis
        per_person.append({"pid": p.get("pid"), "questions": len(qs), "internal": pi, "redis": pr})
    ratio_i = internal / n if n else 0.0
    ratio_r = redis / n if n else 0.0
    return {"people": len(people), "questions": n,
            "internal": internal, "internal_ratio": round(ratio_i, 4),
            "redis": redis, "redis_ratio": round(ratio_r, 4),
            "m55_target": M55_TARGET, "m55_pass": ratio_i >= M55_TARGET,
            "m58_limit": M58_LIMIT, "m58_pass": ratio_r < M58_LIMIT,
            "by_kind": dict(kinds),
            "by_category": {f"{c}/{k}": v for (c, k), v in sorted(by_cat.items())},
            "per_person": per_person}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--gen", required=True, help="그래프 1-A 생성 결과 JSON(sim_applicants의 gen.json 형식)")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    if not os.path.exists(a.gen):
        raise SystemExit(f"파일이 없습니다: {a.gen}")
    d = json.load(open(a.gen, encoding="utf-8"))
    people = d["people"] if isinstance(d, dict) and "people" in d else (d if isinstance(d, list) else [d])
    rep = summarize(people)
    rep["source_file"] = a.gen
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    print(f"\nM-55 사내문서 출처 {rep['internal']}/{rep['questions']} = {rep['internal_ratio'] * 100:.1f}% "
          f"(목표 {M55_TARGET * 100:.0f}% 이상) → {'통과' if rep['m55_pass'] else '미달'}")
    print(f"M-58 Redis 출처     {rep['redis']}/{rep['questions']} = {rep['redis_ratio'] * 100:.1f}% "
          f"(상한 {M58_LIMIT * 100:.0f}% 미만) → {'통과' if rep['m58_pass'] else '미달'}")
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        json.dump(rep, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n저장: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
