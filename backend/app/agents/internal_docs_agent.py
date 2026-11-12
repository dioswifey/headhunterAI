"""사내문서 RAG 적재·동기화 에이전트 A13 — ADLC v0.7 6.13.

`internal_documents/internal_documents/*.md`(META_SPEC v0.3) → meta 주석 파싱 → 정규화 →
섹션 청킹 → 임베딩(`llm.embed`, 로컬 qwen3-embedding:0.6b) → Chroma 두 컬렉션.

질문 은행 에이전트(questionbank_agents.py)와 같은 조립 방식·같은 부품 이름을 쓴다.
모델 판단은 없다 — 임베딩만 부른다. 번역 에이전트도 거치지 않는다(사내문서는 한국어 원본 그대로).

    python -m app.agents.internal_docs_agent ingest [--dry-run]   전체 적재
    python -m app.agents.internal_docs_agent sync   [--dry-run]   바뀐 문서만
    python -m app.agents.internal_docs_agent stats                저장 현황
"""
import argparse
import hashlib
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import internal_store, llm  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DOCS_DIR = os.getenv("INTERNAL_DOCS_DIR",
                     "/internal_documents" if os.path.isdir("/internal_documents")
                     else os.path.join(_ROOT, "internal_documents", "internal_documents"))

# 청킹 (6.13.3)
TARGET, MAX_LEN, OVERLAP = 1000, 1500, 150

# dept 값이 9종으로 흩어져 있다(6.13.1 실측) → 5개로 정규화
DEPT_MAP = {"AIAX": "ai_ax", "AI_AX": "ai_ax", "BE": "backend", "BACKEND": "backend",
            "DATA": "data_sci", "DA": "data_sci", "CNS": "consult", "CONSULT": "consult",
            "CONSULTING": "consult", "SALES": "sales"}
DEPTS = ("ai_ax", "backend", "data_sci", "consult", "sales")

META_RE = re.compile(r"<!--\s*meta:\s*(.*?)-->", re.S)
FORBID_RE = re.compile(r"<!--\s*forbid:\s*(.*?)-->", re.S)
ANY_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def _kv(s):
    out = {}
    for part in s.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _pipe(values):
    """목록을 |a|b| 꼴 문자열로. Chroma 메타데이터 값은 스칼라만 되므로."""
    vs = [v for v in values if v]
    return "|" + "|".join(vs) + "|" if vs else ""


# ---------------- 1 scan / 2 parse_meta ----------------
def scan():
    if not os.path.isdir(DOCS_DIR):
        raise SystemExit(f"사내문서 폴더가 없습니다: {DOCS_DIR}")
    return sorted(os.path.join(DOCS_DIR, f) for f in os.listdir(DOCS_DIR) if f.endswith(".md"))


def parse_doc(path):
    """문서 1개 → {doc_id, doc_type, tier, topic, disclosure, status, doc_title, sections[], hash}."""
    with open(path, "rb") as f:
        raw = f.read()
    raw = raw.replace(b"\r\n", b"\n")   # git core.autocrlf PC(CRLF)와 LF PC의 해시를 같게 — 안 하면 sync가 80개 전부 재임베딩
    text = raw.decode("utf-8")
    doc_hash = hashlib.sha256(raw).hexdigest()

    m = re.search(r"^#\s+(.+)$", text, re.M)
    doc_title = m.group(1).strip() if m else os.path.basename(path)

    # 문서 메타 = '# 제목' 바로 뒤의 첫 meta 주석
    head = text[m.end():] if m else text
    dm = META_RE.search(head)
    dmeta = _kv(dm.group(1)) if dm else {}
    if not dmeta.get("doc"):
        return None

    sections, cur = [], None
    for line in text.splitlines():
        h = re.match(r"^##\s+(.+)$", line)
        if h:
            cur = {"sec_title": h.group(1).strip(), "meta": {}, "forbid": [], "body": []}
            sections.append(cur)
            continue
        if cur is None:
            continue
        sm = META_RE.match(line.strip())
        if sm and "sec=" in line:
            cur["meta"] = _kv(sm.group(1))
            continue
        fm = FORBID_RE.match(line.strip())
        if fm:
            cur["forbid"] = [t.strip() for t in fm.group(1).split("|") if t.strip()]
            continue
        cur["body"].append(line)

    return {"path": path, "doc_hash": doc_hash, "doc_title": doc_title,
            "doc_id": dmeta.get("doc"), "doc_type": dmeta.get("doc_type", ""),
            "tier": int(dmeta.get("tier") or 0), "topic": dmeta.get("topic", ""),
            "disclosure": dmeta.get("disclosure", "internal"), "status": dmeta.get("status", "active"),
            "sections": sections}


# ---------------- 3 normalize ----------------
def normalize_body(lines):
    """메타·forbid 주석을 본문에서 **제거**한다. 모델 입력에 <!--meta:…-->가 들어가면
    모델이 그 문법을 따라 쓸 수 있다(6.13.3)."""
    body = "\n".join(lines)
    body = ANY_COMMENT_RE.sub("", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


# ---------------- 4 chunk ----------------
def _split_blocks(body):
    """코드블록·표를 쪼개지 않도록 블록 단위로 나눈다."""
    blocks, buf, in_code = [], [], False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            buf.append(line)
            if not in_code:
                blocks.append("\n".join(buf)); buf = []
            continue
        if in_code:
            buf.append(line); continue
        if line.strip() == "":
            if buf:
                blocks.append("\n".join(buf)); buf = []
            continue
        buf.append(line)
    if buf:
        blocks.append("\n".join(buf))
    return [b for b in blocks if b.strip()]


def chunk_section(doc, sec):
    """섹션 1개 = 조각 1개가 기본. 상한을 넘으면 **문단 경계에서만** 자른다."""
    body = normalize_body(sec["body"])
    if not body:
        return []
    header = f"# {doc['doc_title']} > ## {sec['sec_title']}"
    blocks = _split_blocks(body)

    chunks, cur = [], []
    cur_len = 0
    for b in blocks:
        if cur and cur_len + len(b) > MAX_LEN:
            chunks.append("\n\n".join(cur))
            tail = "\n\n".join(cur)[-OVERLAP:] if OVERLAP else ""
            cur = [tail, b] if tail else [b]
            cur_len = len(tail) + len(b)
        else:
            cur.append(b)
            cur_len += len(b)
            if cur_len >= TARGET and len(b) > 0 and cur_len > MAX_LEN * 0.9:
                chunks.append("\n\n".join(cur)); cur = []; cur_len = 0
    if cur:
        chunks.append("\n\n".join(cur))

    return [f"{header}\n\n{c}".strip() for c in chunks if c.strip()]


def build_chunks(doc):
    """문서 1개 → 조각 레코드 목록."""
    recs = []
    for si, sec in enumerate(doc["sections"]):
        sm = sec["meta"]
        depts = [DEPT_MAP.get(d.strip().upper(), d.strip().lower())
                 for d in (sm.get("dept") or "").split("|") if d.strip()]
        texts = chunk_section(doc, sec)
        for i, body in enumerate(texts):
            meta = {
                "doc_id": doc["doc_id"], "doc_type": doc["doc_type"], "tier": doc["tier"],
                "topic": doc["topic"], "disclosure": doc["disclosure"], "status": doc["status"],
                "doc_title": doc["doc_title"], "sec_title": sec["sec_title"],
                "sec": sm.get("sec", ""), "dept": _pipe(depts),
                "level": _pipe([v.strip() for v in (sm.get("level") or "").split("|") if v.strip()]),
                "ct_band": sm.get("ct_band", ""),
                "related": _pipe([v.strip() for v in (sm.get("related") or "").split("|") if v.strip()]),
                "forbid_terms": _pipe(sec["forbid"]),
                "has_code": "```" in body,
                "chunk_index": i, "chunk_of": len(texts),
                "doc_content_hash": doc["doc_hash"],
                "source_file": os.path.relpath(doc["path"], _ROOT).replace("\\", "/"),
                "spec_version": "META_SPEC v0.3",
                "embed_model": _embed_model(), "updated_at": int(time.time()),
            }
            for d in DEPTS:
                meta[f"dept_{d}"] = d in depts
            # 조각 ID는 **문서 안 섹션 순번(si)** 으로 만든다. 원본의 sec 값에 의존하면
            # 같은 문서에 sec=s3가 두 번 적힌 경우(09_수요예측대시보드_공개판) ID가 충돌한다.
            recs.append({"id": f"{doc['doc_id']}#{si:03d}#{i}", "body": body, "meta": meta})
    return recs


def _embed_model():
    from app import config
    return config.EMBED_MODEL


# ---------------- 5 route_disclosure ----------------
def route(doc):
    """1차 관문: public만 PUBLIC 컬렉션으로. 나머지는 RESTRICTED(질문 생성 경로가 열지 않는다)."""
    return internal_store.PUBLIC if doc["disclosure"] == "public" else internal_store.RESTRICTED


# ---------------- 6 embed / 7 upsert ----------------
def embed_and_upsert(coll, recs, batch=16):
    texts = [r["body"] for r in recs]
    vecs = llm.embed(texts, batch=batch)
    internal_store.upsert(coll, [r["id"] for r in recs], texts, [r["meta"] for r in recs], vecs)
    return len(recs)


# ---------------- main ----------------
def run(mode="sync", dry=False):
    paths = scan()
    docs = [d for d in (parse_doc(p) for p in paths) if d]
    print(f"문서 {len(docs)}개 (폴더 {DOCS_DIR})")

    have = {internal_store.PUBLIC: {}, internal_store.RESTRICTED: {}}
    if mode == "sync" and not dry:
        for c in have:
            have[c] = internal_store.doc_hashes(c)

    new, changed, same = [], [], []
    for d in docs:
        coll = route(d)
        prev = have[coll].get(d["doc_id"])
        (same if prev == d["doc_hash"] else (changed if prev else new)).append((d, coll))

    print(f"새 문서 {len(new)} · 바뀐 문서 {len(changed)} · 그대로 {len(same)}")
    todo = new + changed if mode == "sync" else [(d, route(d)) for d in docs]
    if dry:
        for d, c in todo[:10]:
            print(f"  [{c}] {d['doc_id']} {d['doc_title'][:40]} 섹션 {len(d['sections'])}")
        print(f"(--dry-run) 적재 대상 {len(todo)}개")
        return

    t0, total = time.time(), 0
    for n, (d, coll) in enumerate(todo, 1):
        recs = build_chunks(d)
        if not recs:
            print(f"  [{n}/{len(todo)}] {d['doc_id']} 조각 0개 — 건너뜀")
            continue
        internal_store.delete_doc(coll, d["doc_id"])
        try:
            embed_and_upsert(coll, recs)
        except llm.LLMError as e:
            print(f"  [{n}/{len(todo)}] {d['doc_id']} 임베딩 실패({e}) — 다음 sync 재시도")
            continue
        total += len(recs)
        print(f"  [{n}/{len(todo)}] {d['doc_id']:<14} {coll.split('_')[-1]:<10} 조각 {len(recs):3d}  "
              f"({time.time() - t0:.0f}초)")
    print(f"\n조각 {total}개 적재, {time.time() - t0:.0f}초")
    stats()


def stats():
    print(f"\n저장 위치: {internal_store.PERSIST_DIR}")
    for c in (internal_store.PUBLIC, internal_store.RESTRICTED):
        print(f"  {c:<26} 조각 {internal_store.count(c)}개")


def main(argv=None):
    ap = argparse.ArgumentParser(description="사내문서 RAG 적재·동기화 (ADLC v0.7 6.13)")
    ap.add_argument("cmd", choices=["ingest", "sync", "stats"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "stats":
        stats()
    else:
        run("ingest" if a.cmd == "ingest" else "sync", a.dry_run)


if __name__ == "__main__":
    main()
