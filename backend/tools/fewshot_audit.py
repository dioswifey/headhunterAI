"""few-shot 예문 검증 도구 — 1층(규칙·지표) CLI. 2026-11-20.

    docker exec -w /srv AI-Interview-project-api python -m tools.fewshot_audit registry
    docker exec -w /srv AI-Interview-project-api python -m tools.fewshot_audit pool --out /tmp/fewshot_pool.json
    docker exec -w /srv AI-Interview-project-api python -m tools.fewshot_audit trial --variant /tmp/internal_question_new.md --n 10 --out /tmp/trial.json

  registry  등록부(`app.agents.fewshot_guard.REGISTRY`)의 예문이 실제 지시문 파일에 있는지 — 모델 호출 0회
  pool      저장된 사내문서 질문 풀 전체: ② 예문 베끼기(내용어 Jaccard + 임베딩 코사인) ③ 예문 핵심어가 근거 조각 없이 새어 들어옴
            ④ 예문 핵심어 빈도 · 같은 카테고리 안 유사 질문 묶음(코사인 ≥ --cluster) 크기 — 질문 임베딩은 저장된 것을 쓰고
            예문 임베딩만 새로 계산(예문 수만큼, 로컬 qwen3-embedding)
  trial     ⑤ 예문(지시문)을 바꾸기 전/후로 **같은 조각**에서 시범 생성해 비교 — 규칙 위반율·예문 새기·베끼기·반복률.
            나빠지면 '미반영' 판정. **OpenAI 생성 호출이 2 × n회 든다 → 리드 GO 뒤에만 실행**
"""
import argparse
import collections
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import internal_store as S, llm, prompts  # noqa: E402
from app.agents import fewshot_guard as FG, internal_forbid, internal_question_agent as IQ, question_rules as QR  # noqa: E402

COPY_COS = 0.90


def _cos(a, b):
    s = sum(x * y for x, y in zip(a, b))
    return s / ((math.sqrt(sum(x * x for x in a)) or 1) * (math.sqrt(sum(y * y for y in b)) or 1))


def cmd_registry(a):
    bad = []
    for e in FG.REGISTRY:
        raw = (prompts.DIR / f"{e['prompt']}.md").read_text(encoding="utf-8").replace("{{", "{").replace("}}", "}")
        if e["text"] not in raw:
            bad.append(e["id"])
    print(json.dumps({"registered": len(FG.REGISTRY), "missing_in_prompt": bad}, ensure_ascii=False))
    return 1 if bad else 0


def _example_vectors():
    from app.agents import question_chain as QC
    texts = [QR.core(e["text"]).replace("{company_name}", "").replace("**", "") for e in FG.REGISTRY]
    return llm.embed([QC.QUERY_INSTRUCT + t for t in texts])


def cmd_pool(a):
    pub = {c["id"]: c for c in S.chunks(S.PUBLIC, documents=True)}
    ev = _example_vectors()
    out = {"cats": {}, "copies": [], "leaks": [], "key_term_freq": {}, "clusters": {}}
    freq = collections.Counter()
    for cat in IQ.CATS:
        g = S.question_collection(cat).get(include=["documents", "metadatas", "embeddings"])
        docs, metas, vecs = g["documents"], g["metadatas"], g["embeddings"]
        n_copy = n_leak = 0
        for qid, q, m, v in zip(g["ids"], docs, metas, vecs):
            body = (pub.get(m.get("chunk_id")) or {}).get("document") or ""
            leak = FG.leak_terms(q, body)
            if leak:
                n_leak += 1
                out["leaks"].append({"cat": cat, "id": qid, "terms": leak, "q": q})
            jid, jac = FG.copy_score(q)
            cosv = [(_cos(v, e), FG.REGISTRY[i]["id"]) for i, e in enumerate(ev)]
            cmax, cid = max(cosv)
            if jac >= FG.COPY_JACCARD or cmax >= COPY_COS:
                n_copy += 1
                out["copies"].append({"cat": cat, "id": qid, "jaccard": [jid, jac], "cos": [cid, round(cmax, 3)], "q": q})
            for e in FG.REGISTRY:
                for t in e["key_terms"] + e.get("style_terms", []):
                    if t.replace(" ", "") in q.replace(" ", ""):
                        freq[t] += 1
        # 같은 카테고리 안 유사 질문 묶음(코사인 ≥ cluster) — union-find
        parent = list(range(len(vecs)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for i in range(len(vecs)):
            for j in range(i + 1, len(vecs)):
                if _cos(vecs[i], vecs[j]) >= a.cluster:
                    parent[find(i)] = find(j)
        groups = collections.defaultdict(list)
        for i in range(len(vecs)):
            groups[find(i)].append(i)
        big = sorted((gidx for gidx in groups.values() if len(gidx) >= 3), key=len, reverse=True)
        out["clusters"][cat] = [{"size": len(gidx), "samples": [docs[i] for i in gidx[:3]]} for gidx in big[:5]]
        out["cats"][cat] = {"questions": len(docs), "copy_suspect": n_copy, "leak": n_leak,
                            "cluster_ge3": len(big), "largest_cluster": len(big[0]) if big else 1}
    out["key_term_freq"] = dict(freq.most_common())
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("cats", "key_term_freq")}, ensure_ascii=False))


def _trial_once(chunks, cat, variant_text, terms):
    real = prompts.load
    if variant_text is not None:
        def patched(name, **values):
            if name == "generate/internal_question":
                return variant_text.format_map(prompts._Blank({k: ("" if v is None else str(v)) for k, v in values.items()}))
            return real(name, **values)
        prompts.load = patched
    rows = []
    try:
        for c in chunks:
            try:
                cands, usage = IQ.generate_for_chunk(c, cat)
            except Exception as e:  # noqa: BLE001
                rows.append({"chunk": c["chunk_id"], "error": type(e).__name__})
                continue
            for cand in cands:
                ok, why = IQ.validate(cand, c, cat, terms)
                q = (cand.get("question") or "").strip()
                rows.append({"chunk": c["chunk_id"], "ok": ok, "why": why, "q": q, "copy": FG.copy_score(q),
                             "leak": FG.leak_terms(q, c["body"])})
    finally:
        prompts.load = real
    qs = [r for r in rows if "q" in r]
    n = len(qs) or 1
    texts = [r["q"] for r in qs]
    rep = sum(1 for i in range(len(texts)) for j in range(i + 1, len(texts)) if QR.shared_topic(texts[i], texts[j]))
    return {"rows": rows, "n": len(qs), "violation_rate": round(sum(1 for r in qs if not r["ok"]) / n, 3),
            "copy_rate": round(sum(1 for r in qs if r["copy"][1] >= FG.COPY_JACCARD) / n, 3),
            "leak_rate": round(sum(1 for r in qs if r["leak"]) / n, 3), "same_topic_pairs": rep}


def cmd_trial(a):
    terms = internal_forbid.load_terms()
    variant = open(a.variant, encoding="utf-8").read()
    out = {"variant": a.variant, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "cats": {}}
    for cat in a.cats:
        ch = IQ.select_chunks(cat, per_doc=IQ.PER_DOC_BY_CAT.get(cat, IQ.PER_DOC))
        step = max(1, len(ch) // a.n)
        pick = ch[::step][:a.n]
        base = _trial_once(pick, cat, None, terms)
        new = _trial_once(pick, cat, variant, terms)
        worse = (new["violation_rate"] > base["violation_rate"] + 0.05 or new["copy_rate"] > base["copy_rate"]
                 or new["leak_rate"] > base["leak_rate"] or new["same_topic_pairs"] > base["same_topic_pairs"] + 1)
        out["cats"][cat] = {"base": {k: v for k, v in base.items() if k != "rows"}, "variant": {k: v for k, v in new.items() if k != "rows"},
                            "decision": "미반영(나빠짐)" if worse else "반영 가능", "base_rows": base["rows"], "variant_rows": new["rows"]}
        print(cat, out["cats"][cat]["base"], "→", out["cats"][cat]["variant"], out["cats"][cat]["decision"], flush=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("registry")
    p = sp.add_parser("pool")
    p.add_argument("--out", default="/tmp/fewshot_pool.json")
    p.add_argument("--cluster", type=float, default=0.93)
    t = sp.add_parser("trial")
    t.add_argument("--variant", required=True)
    t.add_argument("--n", type=int, default=10)
    t.add_argument("--cats", nargs="*", default=["personality"])
    t.add_argument("--out", default="/tmp/fewshot_trial.json")
    a = ap.parse_args()
    return {"registry": cmd_registry, "pool": cmd_pool, "trial": cmd_trial}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
