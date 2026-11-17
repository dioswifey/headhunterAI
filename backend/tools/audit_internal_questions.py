"""G-06 갈래 A — 저장된 사내문서 질문 **전수 감사** (M-6 ⑤ · PRD M-36 ③). ADLC v0.8 6.19.11.

**모델 호출 0회.** 저장된 질문 전부(2026-11-17 기준 528개)와 그 근거 문구·출처 ID를 규칙으로만 검사한다.
모집단이 유한하므로 "위반 0건"을 표본 추정이 아니라 **전수로** 증명한다.

    python -u -m tools.audit_internal_questions --out /tmp/audit/audit.json
    python -u -m tools.audit_internal_questions --out … --sample 3      # 위반 예시를 3건까지 출력(원문 일부)

검사 항목 (하나라도 걸리면 그 질문은 위반 — T-11 기준)
  C1 금지 용어 사전(`idoc:forbid` 전 용어)이 질문·근거 문구에 있는가          → 질문 문장 검사라 일반 명사까지 전부 본다
  C2 마스킹 표식·가명·사본 ID(`[금액]`·`고객사A`·`MSK-12`)가 있는가
  C3 차단 목록(나이·학벌 등 인적 사정, `questions.BLOCKLIST`)
  C4 면접 방식·평가 기준을 묻는 질문(`questions.asks_interview_method`)
  C5 한 문장·길이 10~160자
  C6 근거 문구(`company_facts`)가 **출처 조각 본문의 부분 문자열**인가(공백 제거 비교)
  C7 `doc_refs`가 비어 있지 않고 **실제 존재하는 조각**을 가리키는가
  C8 출처 조각이 지금도 `disclosure=public` · `status ∈ ALLOWED_STATUS` 인가(등급이 내려갔는데 질문이 남아 있지 않은가)
  C9 질문에 든 영문·숫자 토큰이 출처 조각 본문에 있는가(지어낸 수치·제품명 차단)
보고서에는 **사유 종류·건수·질문 ID**만 남기고 원문은 남기지 않는다(B-08). `--sample`을 준 경우에만 앞 120자를 보여 준다.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import internal_store  # noqa: E402
from app.agents import internal_forbid, internal_qcat, internal_search  # noqa: E402

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|\d+(?:\.\d+)?%?")
SENT_END = re.compile(r"(?:[다요까]|니까)[.?!]\s*(?=\S)")
MIN_LEN, MAX_LEN = 10, 160


def _squash(s):
    return re.sub(r"\s+", "", (s or "").lower())


def main(a):
    from app import questions as Q

    terms = internal_forbid.load_terms()          # 질문 문장 검사 → 일반 명사까지 전부
    rep = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "model_calls": 0,
           "forbid_terms": len(terms), "checks": {}, "by_cat": {}, "violations": [], "samples": []}

    # 출처 조각 본문·메타를 미리 읽어 둔다(공개 컬렉션 전체)
    chunks = {}
    for c in internal_store.chunks(internal_store.PUBLIC, documents=True):
        chunks[c["id"]] = {"body": c.get("document") or "", "meta": c["metadata"] or {}}
    rep["public_chunks"] = len(chunks)

    counts = {k: 0 for k in ("C1_forbid", "C2_mask", "C3_blocklist", "C4_interview_meta",
                             "C5_shape", "C6_facts_not_grounded", "C7_doc_refs", "C8_disclosure", "C9_bad_tokens")}
    total = 0
    for cat in internal_qcat.CATS:
        rows = internal_store.questions_all(cat, documents=True)
        cat_bad = 0
        for r in rows:
            total += 1
            m = r["metadata"] or {}
            q = (r.get("document") or m.get("question") or "").strip()
            facts = internal_qcat.unpipe(m.get("company_facts"))
            refs = internal_qcat.unpipe(m.get("doc_refs"))
            chunk = chunks.get(m.get("chunk_id") or "")
            why = []

            if internal_forbid.find_terms(q, terms) or any(internal_forbid.find_terms(f, terms) for f in facts):
                why.append("C1_forbid")
            if internal_forbid.masked_hits(q) or any(internal_forbid.masked_hits(f) for f in facts):
                why.append("C2_mask")
            if Q.BLOCKLIST.search(q):
                why.append("C3_blocklist")
            if Q.asks_interview_method(q):
                why.append("C4_interview_meta")
            if not (MIN_LEN <= len(q) <= MAX_LEN) or SENT_END.search(q):
                why.append("C5_shape")
            if chunk is None:
                why.append("C7_doc_refs")          # 출처 조각이 사라졌다
            else:
                allowed = _squash(chunk["body"])
                if not all(_squash(f) in allowed for f in facts):
                    why.append("C6_facts_not_grounded")
                cm = chunk["meta"]
                if cm.get("disclosure") != "public" or cm.get("status") not in internal_search.ALLOWED_STATUS:
                    why.append("C8_disclosure")
                bad = sorted(t for t in set(TOKEN_RE.findall(q)) if _squash(t.strip(".")) not in allowed)
                if bad:
                    why.append("C9_bad_tokens")
            if not refs:
                why.append("C7_doc_refs")

            if why:
                cat_bad += 1
                for w in set(why):
                    counts[w] += 1
                rep["violations"].append({"q_id": r["id"], "cat": cat, "doc_id": m.get("doc_id"),
                                          "chunk_id": m.get("chunk_id"), "why": sorted(set(why))})
                if a.sample and len(rep["samples"]) < a.sample:
                    rep["samples"].append({"q_id": r["id"], "why": sorted(set(why)), "question_head": q[:120]})
        rep["by_cat"][cat] = {"questions": len(rows), "violations": cat_bad}

    rep["questions"] = total
    rep["checks"] = counts
    rep["violations_total"] = len(rep["violations"])
    rep["verdict"] = "PASS (0건)" if not rep["violations"] else f"FAIL ({len(rep['violations'])}건) — T-11"
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"전수 감사 — 모델 호출 0회 · 금지 용어 사전 {len(terms)}개 · 공개 조각 {len(chunks)}개")
    for cat, v in rep["by_cat"].items():
        print(f"  {cat:<12} 질문 {v['questions']:>4} · 위반 {v['violations']}")
    print(f"\n질문 총 {total}개 · 위반 {len(rep['violations'])}건")
    print("검사별 위반 수:")
    for k, v in counts.items():
        print(f"  {k:<22} {v}")
    for s in rep["samples"]:
        print(f"  예시 {s['q_id']} {s['why']}: {s['question_head']}")
    print(f"\n판정: {rep['verdict']}")
    print(f"저장: {a.out}")
    return 1 if rep["violations"] else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--sample", type=int, default=0)
    sys.exit(main(p.parse_args()))
