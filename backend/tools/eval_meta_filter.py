"""메타데이터 필터 병목 원인 분석 (2026-11-16, ADLC v0.8 6.18.6) — 평가 도구(제품 코드 아님). 모델 호출 0회.

같은 저장 벡터(public 조각 임베딩 30개)를 질의로 써서, 조건마다 같은 순서를 섞어(교차) 반복 측정한다.
  Chroma: where 없음 / disclosure만 / status $in만 / 둘 다($and, 제품) / status=open(좁은 필터 30조각) / doc_id 한 개 /
          where만 쓰는 get()(벡터 검색 없이 메타데이터 평가만) / k=12·50
  Redis : 사전 필터 KNN vs 필터 없음 + FT.PROFILE(필터 실행 위치)

    python -m tools.eval_meta_filter --out /tmp/mq/meta_filter.json [--reps 30]
"""
import argparse
import json
import random
import statistics as st
import time


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))], 2)


def lat(xs):
    return {"n": len(xs), "mean_ms": round(st.mean(xs), 2), "p50_ms": pct(xs, 50), "p95_ms": pct(xs, 95)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=30)
    a = ap.parse_args()
    import chromadb
    import numpy as np
    from app import internal_store, vectorstore
    from app.agents import internal_search

    random.seed(7)
    coll = internal_store.collection(internal_store.PUBLIC)
    n0 = coll.count()
    vecs = [list(map(float, e)) for e in coll.get(limit=30, include=["embeddings"])["embeddings"]]
    st_in = list(internal_search.ALLOWED_STATUS)
    conds = {
        "none": None,
        "disclosure_eq": {"disclosure": {"$eq": "public"}},
        "status_in": {"status": {"$in": st_in}},
        "and_product": internal_search._where(),
        "status_eq_active": {"status": {"$eq": "active"}},
        "status_eq_open(narrow)": {"status": {"$eq": "open"}},
        "doc_id_eq(narrow)": {"doc_id": {"$eq": "JD-AIAX-01"}},
    }
    match = {k: (coll.count() if v is None else len(coll.get(where=v, include=[])["ids"])) for k, v in conds.items()}
    times = {f"query_k12|{k}": [] for k in conds}
    times.update({f"query_k50|{k}": [] for k in ("none", "and_product")})
    times.update({f"get_only|{k}": [] for k in conds if k != "none"})
    plan = [(kind, k) for kind in ("query_k12",) for k in conds] + [("query_k50", "none"), ("query_k50", "and_product")] + \
           [("get_only", k) for k in conds if k != "none"]
    for _ in range(3):     # 워밍업
        coll.query(query_embeddings=[vecs[0]], n_results=12, where=conds["and_product"])
    for rep in range(a.reps):
        random.shuffle(plan)
        v = vecs[rep % len(vecs)]
        for kind, k in plan:
            w = conds[k]
            t0 = time.perf_counter()
            if kind == "get_only":
                coll.get(where=w, include=[])
            else:
                coll.query(query_embeddings=[v], n_results=12 if kind == "query_k12" else 50, where=w, include=["distances"])
            times[f"{kind}|{k}"].append((time.perf_counter() - t0) * 1000)
    same = 0
    for v in vecs:
        a1 = coll.query(query_embeddings=[v], n_results=12, include=["metadatas"])
        a2 = coll.query(query_embeddings=[v], n_results=12, where=conds["and_product"], include=["metadatas"])
        same += a1["ids"][0] == a2["ids"][0]
    # Redis
    r = vectorstore.client()
    keys = [k for k in r.scan_iter(match="qb:*", count=500) if not k.startswith(b"qb:__")][:30]
    rv = [np.frombuffer(r.hget(k, "embedding"), dtype=np.float32).tolist() for k in keys]
    rt = {"filtered": [], "unfiltered": []}
    for rep in range(a.reps):
        v = rv[rep % len(rv)]
        for mode in random.sample(["filtered", "unfiltered"], 2):
            t0 = time.perf_counter()
            if mode == "filtered":
                vectorstore.knn(v, k=16, role_codes=["ai_ax"], categories=["job"], translated_only=True, r=r)
            else:
                vectorstore.knn(v, k=16, r=r)
            rt[mode].append((time.perf_counter() - t0) * 1000)
    prof = None
    try:
        q = "(@role_code:{ai_ax} @category:{job} @translated:{1})=>[KNN 16 @embedding $v AS dist]"
        raw = r.execute_command("FT.PROFILE", vectorstore.INDEX, "SEARCH", "QUERY", q, "PARAMS", "2", "v",
                                vectorstore.vec(rv[0]), "RETURN", "1", "dist", "DIALECT", "2")

        def dec(x):
            if isinstance(x, bytes):
                return x.decode("utf-8", "ignore")
            if isinstance(x, list):
                return [dec(y) for y in x]
            if isinstance(x, dict):
                return {dec(k): dec(v) for k, v in x.items()}
            return x
        prof = dec(raw[1]) if isinstance(raw, list) and len(raw) > 1 else dec(raw)
    except Exception as e:  # noqa: BLE001
        prof = f"FT.PROFILE 실패: {type(e).__name__}: {e}"
    out = {"measured_at": time.strftime("%Y-%m-%d %H:%M:%S"), "chromadb": chromadb.__version__, "reps": a.reps,
           "public_count_before": n0, "public_count_after": coll.count(), "match_counts": match,
           "chroma": {k: lat(v) for k, v in times.items()}, "same_top12_with_without_where": f"{same}/{len(vecs)}",
           "hnsw_config": getattr(coll, "configuration_json", None),
           "redis": {k: lat(v) for k, v in rt.items()}, "redis_ft_profile": prof}
    json.dump(out, open(a.out, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in out.items() if k != "redis_ft_profile"}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
