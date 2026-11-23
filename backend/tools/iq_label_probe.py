# -*- coding: utf-8 -*-
"""② 색인 때 개념 라벨을 함께 임베딩하면 검색이 좋아지는지 A/B — **원본 컬렉션은 건드리지 않는다**.

지금 질문은 **문장만** 임베딩돼 있다(`internal_question_agent.run_generate` → `llm.embed([질문])`).
질의 쪽은 공고(직무명·업무·요건)라서, 질문 문장에 그 질문이 **무엇을 보는 것인지**(역량·출처 섹션)가
적혀 있지 않으면 같은 공간에서 멀어진다. 그래서 그림자 컬렉션 `internal_questions_<cat>_lab`에
  "<doc_title> · <sec_title> · <competency>" + 줄바꿈 + 질문
을 임베딩해 넣고, 같은 공고 질의로 양쪽에서 뽑아 비교한다.

비교 지표(공고 16개 × 카테고리 2):
  · on_role   뽑힌 질문의 직무 태그가 공고 직무와 맞는 비율(관문 1 통과율)
  · gate_pass 관문 2(fields 모드) 통과율 · gate_med 통과 점수 중앙값
  · overlap   base 상위 k와 lab 상위 k가 겹치는 비율

실행(컨테이너):
  docker exec AI-Interview-project-api python -m tools.iq_label_probe build
  docker exec AI-Interview-project-api python -m tools.iq_label_probe probe --out /tmp/iq_label.json
"""
import argparse
import json
import statistics as st
import sys

from app import config, db, internal_store, llm
from app.agents import internal_qcat, question_chain as QC, question_graph as QG

CATS = internal_qcat.CATS
SHADOW = "{}_lab"


def shadow_name(cat):
    return SHADOW.format(internal_store.QUESTION_BY_CAT[cat])


def label_text(meta, question):
    """개념 라벨 + 질문. 라벨은 **이미 메타에 있는 값**만 쓴다(새로 만들지 않는다)."""
    parts = [meta.get("doc_title") or "", meta.get("sec_title") or "", meta.get("competency") or ""]
    head = " · ".join(p.strip() for p in parts if p and p.strip())
    return (head + chr(10) + question).strip() if head else question


def build(batch=32):
    for cat in CATS:
        rows = internal_store.questions_all(cat)
        name = shadow_name(cat)
        col = internal_store.collection(name)
        try:
            col.delete(ids=[r["id"] for r in rows])     # 다시 만들 때 옛 벡터가 남지 않게
        except Exception:                               # noqa: BLE001 — 빈 컬렉션
            pass
        ids, docs, metas, texts = [], [], [], []
        for r in rows:
            q = (r.get("document") or r["metadata"].get("question") or "").strip()
            if not q:
                continue
            ids.append(r["id"])
            docs.append(q)                              # 문서는 질문 문장 그대로(라벨은 벡터에만)
            metas.append(r["metadata"])
            texts.append(label_text(r["metadata"], q))
        vecs = []
        for i in range(0, len(texts), batch):
            vecs += llm.embed(texts[i:i + batch])
            print(f"  {cat} {len(vecs)}/{len(texts)}", flush=True)
        assert len(vecs) == len(ids)
        internal_store.upsert(name, ids, docs, metas, vecs)
        print(f"{name}: {len(ids)}개")


def top_hits(collection_name, vector, k):
    r = internal_store.collection(collection_name).query(
        query_embeddings=[vector], n_results=k, include=["documents", "metadatas", "distances"])
    return [{"id": r["ids"][0][i], "text": r["documents"][0][i], "meta": r["metadatas"][0][i],
             "sim": 1.0 - r["distances"][0][i]} for i in range(len(r["ids"][0]))]


def probe(out_path, k=12):
    db.pool.open()
    db.pool.wait()
    jobs = db.all_("""SELECT j.*, c.name company_name, r.name role_name FROM jobs j
                      JOIN companies c ON c.id=j.company_id JOIN job_roles r ON r.code=j.role_code
                      WHERE j.status='published' ORDER BY j.id""")
    out = {"meta": {"k": k, "embed_model": config.EMBED_MODEL,
                    "fields_floor": config.POSTING_FIELD_MIN_SIM}, "rows": [], "summary": {}}
    acc = {"base": {"on_role": [], "pass": [], "sim": []}, "lab": {"on_role": [], "pass": [], "sim": []}}
    for job in jobs:
        v = llm.embed([QC.QUERY_INSTRUCT + QC._query({"job": job, "resume_keywords": []})])[0]
        jvs = QG._posting_field_vectors(job) or [QG._posting_vector(job)]
        floor = config.POSTING_FIELD_MIN_SIM
        for cat in ("job", "company"):
            row = {"job": job["id"], "role": job["role_code"], "cat": cat}
            got = {}
            for tag, name in (("base", internal_store.QUESTION_BY_CAT[cat]), ("lab", shadow_name(cat))):
                hits = top_hits(name, v, k)
                got[tag] = {h["id"] for h in hits}
                on_role = sum(1 for h in hits if h["meta"].get("dept_" + job["role_code"]))
                qvs = QG._gate_vectors([QG._gate_text(h["text"]) for h in hits])
                sims = [max(QG._cos(jv, qv) for jv in jvs) for qv in qvs]
                passed = sum(1 for x in sims if x >= floor)
                row[tag] = {"on_role": round(on_role / len(hits), 3), "pass": round(passed / len(hits), 3),
                            "gate_med": round(st.median(sims), 3)}
                acc[tag]["on_role"].append(on_role / len(hits))
                acc[tag]["pass"].append(passed / len(hits))
                acc[tag]["sim"].append(st.median(sims))
            row["overlap"] = round(len(got["base"] & got["lab"]) / k, 3)
            out["rows"].append(row)
            print(f"공고 {job['id']:>3} {cat:<9} base on_role {row['base']['on_role']:.2f} pass {row['base']['pass']:.2f}"
                  f" | lab on_role {row['lab']['on_role']:.2f} pass {row['lab']['pass']:.2f}"
                  f" | 겹침 {row['overlap']:.2f}", flush=True)
    for tag in ("base", "lab"):
        out["summary"][tag] = {m: round(sum(acc[tag][m]) / len(acc[tag][m]), 3) for m in ("on_role", "pass", "sim")}
    print("\n요약:", json.dumps(out["summary"], ensure_ascii=False))
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("저장:", out_path)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    p = sub.add_parser("probe")
    p.add_argument("--out", required=True)
    p.add_argument("--k", type=int, default=12)
    a = ap.parse_args()
    if a.cmd == "build":
        build()
    else:
        probe(a.out, a.k)
    return 0


if __name__ == "__main__":
    sys.exit(main())
