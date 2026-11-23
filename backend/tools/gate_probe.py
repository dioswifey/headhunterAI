# -*- coding: utf-8 -*-
"""관문 2(공고 관련성) 판별력 실측 — blob(공고 전문 1벡터) vs fields(공고 단위별 벡터, 최댓값).

왜 만들었나(2026-11-24): 11-23 재집계에서 관문 2 코사인이 중앙값 0.550 · 사분위 범위 0.513~0.578(폭 0.065)
이고 후보의 54%가 0.50~0.58 안에 뭉쳐 있었다. 하한을 노이즈 폭만큼 옮기면 풀의 절반이 뒤집히므로,
이 관문은 '판별'이 아니라 '추첨'에 가깝다. 원인 가설 = **길이 비대칭**(공고 1,500자 blob ↔ 질문 평균 100자).
이 도구는 같은 후보 집합에 두 모드를 걸어 **띠 폭**과 **교차 공고 분리도(AUC)**를 비교한다.

모델 호출: 임베딩만(chat 0회). 공고 16개 × (단위 ≤8 + blob 1) + 질문 후보 임베딩(캐시 재사용).

판별력 지표
  · band_iqr    후보 점수의 사분위 범위 — **넓을수록** 좋다(하한을 둘 여지가 생긴다)
  · near_floor  하한 ±0.04 안에 든 후보 비율 — **낮을수록** 좋다(경계에서 흔들리는 후보가 적다)
  · auc         같은 공고에서 뽑은 후보(양성 프록시) vs 다른 직무 공고에서 뽑은 후보(음성 프록시)를
                그 공고 기준으로 점수 매겼을 때의 AUC — **높을수록** 좋다
  양성·음성은 **프록시**다(사람이 매긴 정답이 아니라 '그 공고로 검색된 것/다른 직무 공고로 검색된 것').
  그래서 절대값이 아니라 **두 모드의 차이**만 본다.

실행(컨테이너):
  docker exec AI-Interview-project-api python -m tools.gate_probe --out /tmp/gate_probe.json
"""
import argparse
import json
import random
import statistics as st
import sys

from app import config, db, llm, questions as Q
from app.agents import question_chain as QC, question_graph as QG

CATS = ("job", "company")
N_BY_CAT = dict(Q.PLAN)        # 카테고리별 요구 개수(직무 3 · 기업 1)
SAFETY_FLOOR = 0.42           # 상대 관문의 안전 하한 — "이 공고에 맞는 질문이 아예 없다"를 막는 값만 둔다
                              # (음성 프록시 중앙값 0.39~0.52 구간의 아래쪽. 손조정 대상이 아니다)


def jobs_rows():
    db.pool.open()
    db.pool.wait()
    return db.all_("""SELECT j.*, c.name company_name, r.name role_name FROM jobs j
                      JOIN companies c ON c.id=j.company_id
                      JOIN job_roles r ON r.code=j.role_code
                      WHERE j.status='published' ORDER BY j.id""")


def query_vector(job):
    """이력서 없이 공고만으로 만든 질의 벡터(검색 쪽은 두 모드가 같다 — 관문만 비교한다)."""
    return llm.embed([QC.QUERY_INSTRUCT + QC._query({"job": job, "resume_keywords": []})])[0]


def gate_scores(job, texts, mode):
    """(점수 목록, 하한) — mode는 'blob' 또는 'fields'."""
    qvs = QG._gate_vectors([QG._gate_text(t) for t in texts])
    if mode == "fields":
        jvs = QG._posting_field_vectors(job)
        floor = config.POSTING_FIELD_MIN_SIM
        if not jvs:
            return None, floor
    else:
        jv = QG._posting_vector(job)
        if jv is None:
            return None, QG.POSTING_MIN_SIM
        jvs, floor = [jv], QG.POSTING_MIN_SIM
    return [max(QG._cos(v, qv) for v in jvs) for qv in qvs], floor


def auc(pos, neg):
    """순위 기반 AUC(동점은 0.5). 작은 표본이라 Mann-Whitney 직접 계산."""
    if not pos or not neg:
        return None
    win = sum((1.0 if p > n else 0.5 if p == n else 0.0) for p in pos for n in neg)
    return win / (len(pos) * len(neg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--depth", type=int, default=12, help="공고마다 모을 후보 수")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    random.seed(a.seed)

    jobs = jobs_rows()
    terms, _ = QG._iq_terms()
    if not terms:
        print("금지 용어 사전이 없다(A13 적재 먼저).", file=sys.stderr)
        return 2

    # 1) 공고마다 후보 질문을 모은다(검색 경로는 두 모드 공통)
    cand = {}       # (job_id, cat) -> [질문 문장]
    for job in jobs:
        v = query_vector(job)
        for cat in CATS:
            got = QG._pick_internal([v], job["role_code"], cat, a.depth, set(), terms) \
                or QG._pick_internal([v], job["role_code"], cat, 1, set(), terms) or []
            cand[(job["id"], cat)] = [c.get("text") or "" for c in got]
        print(f"공고 {job['id']:>3} {job['role_code']:<10} 후보 " +
              " ".join(f"{c}:{len(cand[(job['id'], c)])}" for c in CATS), flush=True)

    out = {"meta": {"depth": a.depth, "embed_model": config.EMBED_MODEL,
                    "blob_floor": QG.POSTING_MIN_SIM, "fields_floor": config.POSTING_FIELD_MIN_SIM,
                    "units_per_job": {}}, "modes": {}}
    for job in jobs:
        out["meta"]["units_per_job"][job["id"]] = len(QG._posting_field_texts(job))

    # 2) 모드마다 같은 후보에 점수를 매긴다
    for mode in ("blob", "fields"):
        allsc, near, rows, aucs = [], 0, [], []
        for job in jobs:
            for cat in CATS:
                texts = cand[(job["id"], cat)]
                if not texts:
                    continue
                sc, floor = gate_scores(job, texts, mode)
                if sc is None:
                    continue
                allsc += sc
                near += sum(1 for x in sc if abs(x - floor) <= 0.04)
                # 음성 프록시 = 직무가 다른 공고의 후보(같은 카테고리)
                others = [j for j in jobs if j["role_code"] != job["role_code"] and cand[(j["id"], cat)]]
                neg_texts = []
                for j in random.sample(others, min(3, len(others))):
                    neg_texts += cand[(j["id"], cat)][:4]
                nsc, _ = gate_scores(job, neg_texts, mode) if neg_texts else (None, floor)
                if nsc:
                    aucs.append(auc(sc, nsc))
                # 상대(순위) 관문: 점수 순 top-n + **낮은 안전 하한**만 둔다(공고별 손조정 없음).
                want = N_BY_CAT.get(cat, 1)
                top = sorted(sc, reverse=True)[:want]
                rows.append({"job": job["id"], "cat": cat, "n": len(sc),
                             "pass": sum(1 for x in sc if x >= floor),
                             "min": round(min(sc), 3), "med": round(st.median(sc), 3),
                             "max": round(max(sc), 3),
                             "neg_med": round(st.median(nsc), 3) if nsc else None,
                             "neg_max": round(max(nsc), 3) if nsc else None,
                             "auc": round(auc(sc, nsc), 3) if nsc else None,
                             "abs_ok": sum(1 for x in sc if x >= floor) >= want,
                             "rel_ok": len([x for x in top if x >= SAFETY_FLOOR]) >= want,
                             "rel_top": [round(x, 3) for x in top],
                             "rel_above_negmax": (sum(1 for x in top if nsc and x > max(nsc)) if nsc else None),
                             "sims": [round(x, 3) for x in sorted(sc, reverse=True)]})
        allsc.sort()
        q1, q3 = allsc[len(allsc) // 4], allsc[3 * len(allsc) // 4]
        aucs = [x for x in aucs if x is not None]
        out["modes"][mode] = {
            "n": len(allsc),
            "min": round(allsc[0], 3), "q1": round(q1, 3), "median": round(st.median(allsc), 3),
            "q3": round(q3, 3), "max": round(allsc[-1], 3), "band_iqr": round(q3 - q1, 3),
            "near_floor_rate": round(near / len(allsc), 3),
            "auc_mean": round(sum(aucs) / len(aucs), 3) if aucs else None,
            "auc_min": round(min(aucs), 3) if aucs else None,
            # 절대 하한 vs 상대(순위+안전하한)의 **충족률**과 음성 대비 여유
            "abs_fill_rate": round(sum(1 for r in rows if r["abs_ok"]) / len(rows), 3),
            "rel_fill_rate": round(sum(1 for r in rows if r["rel_ok"]) / len(rows), 3),
            "rel_above_negmax_rate": round(
                sum(r["rel_above_negmax"] or 0 for r in rows) / sum(len(r["rel_top"]) for r in rows), 3),
            "rows": rows}
        m = out["modes"][mode]
        m = out["modes"][mode]
        print("", flush=True)
        print(f"[{mode}] n={m['n']} 중앙값 {m['median']} 사분위 {m['q1']}~{m['q3']} "
              f"(폭 {m['band_iqr']}) 경계±0.04 {m['near_floor_rate']} AUC평균 {m['auc_mean']}", flush=True)
        print(f"        충족률 절대하한 {m['abs_fill_rate']} · 상대순위 {m['rel_fill_rate']} · "
              f"상대 선택분이 음성 최고치보다 높은 비율 {m['rel_above_negmax_rate']}", flush=True)

    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("저장:", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
