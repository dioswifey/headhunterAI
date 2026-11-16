"""Multi-Query + RRF(Reciprocal Rank Fusion) — 그래프 1-A 검색 최적화 (2026-11-16 사용자 지시, ADLC v0.8 6.18).

여러 관점의 검색 쿼리(Multi-Query)로 각각 검색한 순위 목록을 RRF로 합친다. HyDE는 쓰지 않는다.

    RRF(d) = Σ_i 1 / (RRF_K + rank_i(d))      rank는 1부터, 목록 i에 d가 없으면 그 항은 0

- 점수(유사도)가 아니라 **순위만** 본다 → Redis·Chroma처럼 점수 척도가 달라도 합칠 수 있다.
- 한 목록 안에 같은 키가 두 번 나오면 앞 순위만 센다(중복 제거).
- 같은 점수는 (가장 좋은 순위 → 나온 목록 수 → 키) 순으로 정해 결과가 항상 같다(결정적).
- 안전 조건(유사도 하한·공개 범위·금지 용어)은 **합치기 전에 목록마다** 적용하는 것이 호출 쪽 책임이다.
  RRF는 순위만 보므로, 관련 없는 항목이 여러 목록에 들었다는 이유만으로 올라오지 않게 하려면 먼저 걸러야 한다.
"""
from concurrent.futures import ThreadPoolExecutor

RRF_K = 60


def rrf(lists, key, k=RRF_K, limit=None):
    """lists: 순위 목록들(앞이 1위). key: 항목 → 고유 키.
    반환: [{**대표 항목, rrf_score, rrf_lists(나온 목록 수), rrf_best_rank, rrf_ranks{목록 번호: 순위}}] 점수 내림차순."""
    agg = {}
    for li, lst in enumerate(lists):
        seen = set()
        for rank, item in enumerate(lst or [], 1):
            kk = key(item)
            if kk in seen:
                continue
            seen.add(kk)
            a = agg.setdefault(kk, {"item": item, "score": 0.0, "ranks": {}})
            if a["ranks"] and rank < min(a["ranks"].values()):   # 대표 항목 = 가장 좋은 순위로 나온 것(유사도 등 필드)
                a["item"] = item
            a["score"] += 1.0 / (k + rank)
            a["ranks"][li] = rank
    out = []
    for kk, a in agg.items():
        best = min(a["ranks"].values())
        out.append({**a["item"], "rrf_score": round(a["score"], 6), "rrf_lists": len(a["ranks"]),
                    "rrf_best_rank": best, "rrf_ranks": dict(a["ranks"]), "_rrf_key": kk})
    out.sort(key=lambda x: (-x["rrf_score"], x["rrf_best_rank"], -x["rrf_lists"], str(x["_rrf_key"])))
    for x in out:
        x.pop("_rrf_key", None)
    return out[:limit] if limit else out


def parallel(fn, args_list, workers=None):
    """fn(*args)를 병렬로 돌린다. 반환 [(ok, 결과|예외)] — 순서 유지. 한 항목이 실패해도 나머지는 계속."""
    def wrap(args):
        try:
            return True, fn(*args)
        except Exception as e:  # noqa: BLE001 — 호출 쪽이 실패 목록만 빼고 합친다(부분 대체)
            return False, e
    if len(args_list) <= 1:
        return [wrap(a) for a in args_list]
    with ThreadPoolExecutor(max_workers=workers or len(args_list)) as ex:
        return list(ex.map(wrap, args_list))
