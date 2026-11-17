"""M-63 질문 다양성 — 출제된 사내문서 질문의 역량·출처 문서 쏠림을 센다(모델 호출 0회).

    python -u -m tools.eval_diversity --gen /tmp/sim2/gen.json --out /tmp/sim2/diversity.json

정의
  M-63a 응시당 역량 다양성 = 한 응시에서 출제된 사내문서 질문의 **서로 다른 competency 수 / 그 질문 수**
  M-63b 최다 역량 점유율  = 전체 출제 질문 중 가장 많이 나온 역량의 비율(낮을수록 고름)
  M-63c 출처 문서 집중도  = 출제 질문이 나온 서로 다른 doc_id 수 / 질문 수
은행 전체(저장된 528개)의 분포도 같이 내 '은행이 쏠렸나 / 검색이 쏠렸나'를 가른다.
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import internal_store  # noqa: E402
from app.agents import internal_qcat  # noqa: E402


def main(a):
    # 1) 저장된 질문 전체의 메타 (id → competency·doc_id·q_cat)
    meta, bank = {}, collections.Counter()
    bank_doc = collections.Counter()
    for cat in internal_qcat.CATS:
        for r in internal_store.questions_all(cat, documents=False):
            m = r["metadata"] or {}
            meta[r["id"]] = {"cat": cat, "competency": (m.get("competency") or "(없음)").strip(),
                             "doc_id": m.get("doc_id") or "", "intent": m.get("intent") or ""}
            bank[(cat, meta[r["id"]]["competency"])] += 1
            bank_doc[(cat, meta[r["id"]]["doc_id"])] += 1

    g = json.load(open(a.gen, encoding="utf-8"))
    people = g["people"]
    used, per_person, unknown = collections.Counter(), [], 0
    used_doc = collections.Counter()
    for p in people:
        comps, docs, n = [], [], 0
        for q in p["questions"]:
            gm = q.get("gen_method") or ""
            if not gm.startswith("internal_q:"):
                continue
            qid = gm.split("|")[0][len("internal_q:"):]
            info = meta.get(qid)
            n += 1
            if not info:
                unknown += 1
                continue
            comps.append(info["competency"])
            docs.append(info["doc_id"])
            used[(info["cat"], info["competency"])] += 1
            used_doc[info["doc_id"]] += 1
        per_person.append({"pid": p.get("pid"), "internal_questions": n,
                           "distinct_competency": len(set(comps)), "competencies": sorted(set(comps)),
                           "distinct_docs": len(set(docs)),
                           "m63a": round(len(set(comps)) / n, 4) if n else None})

    tot = sum(used.values())
    comp_only = collections.Counter({k[1]: 0 for k in used})
    for (cat, c), v in used.items():
        comp_only[c] += v
    top_comp, top_n = (comp_only.most_common(1) or [("-", 0)])[0]
    bank_comp = collections.Counter()
    for (cat, c), v in bank.items():
        bank_comp[c] += v
    b_top, b_n = (bank_comp.most_common(1) or [("-", 0)])[0]

    rep = {
        "gen_file": a.gen, "people": len(people), "internal_questions": tot, "unknown_ids": unknown,
        "M-63a_per_attempt_mean": round(sum(x["m63a"] for x in per_person if x["m63a"]) / len(per_person), 4),
        "M-63b_top_competency": {"name": top_comp, "count": top_n, "share": round(top_n / tot, 4) if tot else None},
        "M-63c_distinct_docs": {"docs": len(used_doc), "questions": tot,
                                "ratio": round(len(used_doc) / tot, 4) if tot else None},
        "used_competency_top10": comp_only.most_common(10),
        "used_docs_top10": used_doc.most_common(10),
        "bank_total": sum(bank.values()),
        "bank_competency_top10": bank_comp.most_common(10),
        "bank_top_competency_share": round(b_n / sum(bank.values()), 4) if bank else None,
        "bank_by_cat": {c: sum(v for (cc, _), v in bank.items() if cc == c) for c in internal_qcat.CATS},
        "per_person": per_person,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"출제된 사내문서 질문 {tot}개 (지원자 {len(people)}명) · id를 못 찾은 질문 {unknown}개")
    print(f"M-63a 응시당 역량 다양성 평균 = {rep['M-63a_per_attempt_mean']}  (1.0 = 5문항이 모두 다른 역량)")
    print(f"M-63b 최다 역량 '{top_comp}' {top_n}/{tot} = {rep['M-63b_top_competency']['share']}")
    print(f"M-63c 출처 문서 {len(used_doc)}개 / 질문 {tot}개 = {rep['M-63c_distinct_docs']['ratio']}")
    print(f"\n출제 역량 상위: {comp_only.most_common(8)}")
    print(f"출제 출처 문서 상위: {used_doc.most_common(8)}")
    print(f"\n은행 전체 {rep['bank_total']}개 · 카테고리별 {rep['bank_by_cat']}")
    print(f"은행 최다 역량 점유율 {rep['bank_top_competency_share']} · 상위: {bank_comp.most_common(8)}")
    print(f"\n저장: {a.out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--gen", required=True)
    p.add_argument("--out", required=True)
    main(p.parse_args())
