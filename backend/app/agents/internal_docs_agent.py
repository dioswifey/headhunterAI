"""사내문서 RAG 적재·동기화 에이전트 A13 — ADLC v0.7 6.13.

`internal_documents/internal_documents/*.md`(META_SPEC v0.3) → meta 주석 파싱 → 정규화 →
섹션 청킹 → 임베딩(`llm.embed`, 로컬 qwen3-embedding:0.6b) → Chroma 두 컬렉션.

질문 은행 에이전트(questionbank_agents.py)와 같은 조립 방식·같은 부품 이름을 쓴다.
모델 판단은 없다 — 임베딩만 부른다. 번역 에이전트도 거치지 않는다(사내문서는 한국어 원본 그대로).

    python -m app.agents.internal_docs_agent ingest [--dry-run]           전체 적재
    python -m app.agents.internal_docs_agent sync   [--dry-run]           바뀐 문서만
    python -m app.agents.internal_docs_agent prune  [--dry-run] [--force] 원본에 없는 문서·등급이 바뀐 문서의 옛 조각 정리
    python -m app.agents.internal_docs_agent audit  [--dry-run]           감사 게이트(읽기만, 판정을 Redis idoc:audit에 기록)
    python -m app.agents.internal_docs_agent forbid [--dry-run]           금지 용어 사전 idoc:forbid만 다시 만들기(임베딩 없음)
    python -m app.agents.internal_docs_agent mask                         마스킹 사본 미리 보기(규칙별 건수·R2 검토 위치만, 쓰기 없음)
    python -m app.agents.internal_docs_agent stats                        저장 현황

ingest·sync는 끝에 8 prune → 9 audit을 이어서 돈다. 금지 용어 사전 Redis `idoc:forbid`(모든 등급 문서의
forbid 주석)는 5 route 단계에서 만든다 — 임베딩이 필요 없어 임베딩이 실패해도 사전은 최신이 된다.

2-B 마스킹 사본(2026-11-13 사용자 승인, RETAG_PROPOSAL 7·7.8절): `_masking/mask_rules.json`의 `publish` 문서마다
마스킹한 가상 문서 `MSK-<번호>`(disclosure=public)를 문서 목록에 더한다(`internal_masking.build`). 원본은 그대로
restricted. 규칙 파일이 없거나 깨지면 사본을 만들지 않고, prune은 저장된 MSK-*를 지우지 않고 보류한다.

감사 게이트는 **아무것도 지우지 않는다**. 실패하면 `idoc:audit.ok=0`을 남기고, 그래프 1-A는
`internal_forbid.rag_ready()`가 False라 사내문서 없이 질문을 만든다(H-08 — 설계의 'public 비우기' 대신 비파괴 차단).
"""
import argparse
import hashlib
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import internal_store, llm  # noqa: E402
from app.agents import internal_forbid, internal_masking  # noqa: E402

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
    if doc.get("masked"):
        doc["density_flags"] = []                 # 부를 때마다 다시 센다
    for si, sec in enumerate(doc["sections"]):
        sm = sec["meta"]
        depts = [DEPT_MAP.get(d.strip().upper(), d.strip().lower())
                 for d in (sm.get("dept") or "").split("|") if d.strip()]
        texts = chunk_section(doc, sec)
        dens = doc.get("density")
        if dens and dens.get("max"):
            # R16 표식 밀도(마스킹 사본만): 문장 절반 넘게 표식이면 warn = 기록만 / drop = 그 조각 제외
            comp = MASK_STATE.get("compiled")
            flagged = [t for t in texts if comp and comp.marked_ratio(t) > dens["max"]]
            if flagged:
                doc.setdefault("density_flags", []).extend(f"{si:03d}" for _ in flagged)
                if dens.get("mode") == "drop":
                    texts = [t for t in texts if t not in flagged]
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
            meta.update(doc.get("extra_meta") or {})      # 마스킹 사본: masked · disclosure_src · mask_rules_version
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


def build_forbid(docs, dry=False):
    """**모든 등급** 문서의 forbid 주석 → Redis `idoc:forbid` (6.13.7 ③, 6.14.0 P-1). 파일로는 저장하지 않는다."""
    mapping = internal_forbid.collect(docs)
    if dry:
        print(f"(--dry-run) 금지 용어 사전: 용어 {len(mapping)}개 · 문서 {len(docs)}개 (Redis에 쓰지 않음)")
        return mapping
    try:
        internal_forbid.write(mapping, len(docs))
        print(f"금지 용어 사전 {internal_forbid.KEY}: 용어 {len(mapping)}개 (문서 {len(docs)}개, 모든 등급)")
    except Exception as e:      # Redis 실패 → 사전이 옛것·없음 → 감사에서 critical로 잡힌다
        print(f"금지 용어 사전 쓰기 실패({type(e).__name__}: {e}) — 감사 게이트가 사내문서 경로를 막는다")
    return mapping


# ---------------- 6 embed / 7 upsert ----------------
def embed_and_upsert(coll, recs, batch=16):
    texts = [r["body"] for r in recs]
    vecs = llm.embed(texts, batch=batch)
    internal_store.upsert(coll, [r["id"] for r in recs], texts, [r["meta"] for r in recs], vecs)
    return len(recs)


# ---------------- main ----------------
MASK_STATE = {"ok": False, "error": "not_loaded"}     # 마지막 load_docs의 마스킹 상태(prune·audit이 읽는다)


def load_docs(mask=True):
    """(문서 목록 = 원본 + 마스킹 사본, doc 메타가 없어 건너뛴 파일 이름 목록). 마스킹 상태는 MASK_STATE."""
    global MASK_STATE
    docs, skipped = [], []
    for p in scan():
        d = parse_doc(p)
        if d:
            docs.append(d)
        else:
            skipped.append(os.path.basename(p))
    if not mask:
        MASK_STATE = {"ok": False, "error": "disabled"}
        return docs, skipped
    try:
        masked, MASK_STATE = internal_masking.build(docs, DOCS_DIR)
    except internal_masking.MaskRulesError as e:
        MASK_STATE = {"ok": False, "error": str(e)}
        print(f"마스킹 규칙 파일을 읽지 못함({e}) — 공개용 사본을 만들지 않고, 저장된 MSK-* 사본은 보류")
        return docs, skipped
    if MASK_STATE["missing"] or MASK_STATE["drop_mismatch"] or MASK_STATE["unlisted"]:
        print(f"마스킹: 규칙에 있는데 원본 없음 {len(MASK_STATE['missing'])} · 제외 섹션 idx/title 불일치로 사본 안 만듦 "
              f"{MASK_STATE['drop_mismatch']} · 규칙에 없는 비공개 문서(사본 안 만듦) {len(MASK_STATE['unlisted'])}")
    return docs + masked, skipped


def run(mode="sync", dry=False, force=False):
    docs, skipped = load_docs()
    print(f"문서 {len(docs)}개 (폴더 {DOCS_DIR})" + (f" · doc 메타 없음 {len(skipped)}개 건너뜀" if skipped else ""))
    build_forbid(docs, dry)

    have = {internal_store.PUBLIC: {}, internal_store.RESTRICTED: {}}
    if mode == "sync":          # --dry-run도 해시는 읽는다(읽기만) — 안 그러면 dry-run이 늘 '전부 새 문서'로 보인다
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
        prune(docs, skipped, dry=True, force=force)
        return

    t0, total = time.time(), 0
    for n, (d, coll) in enumerate(todo, 1):
        recs = build_chunks(d)
        if not recs:
            print(f"  [{n}/{len(todo)}] {d['doc_id']} 조각 0개 — 건너뜀")
            continue
        # 두 컬렉션 모두에서 먼저 지운다(6.13.5) — 등급이 public → restricted로 내려간 문서가 public에 남지 않게
        for c in (internal_store.PUBLIC, internal_store.RESTRICTED):
            internal_store.delete_doc(c, d["doc_id"])
        try:
            embed_and_upsert(coll, recs)
        except llm.LLMError as e:
            print(f"  [{n}/{len(todo)}] {d['doc_id']} 임베딩 실패({e}) — 다음 sync 재시도")
            continue
        total += len(recs)
        print(f"  [{n}/{len(todo)}] {d['doc_id']:<14} {coll.split('_')[-1]:<10} 조각 {len(recs):3d}  "
              f"({time.time() - t0:.0f}초)")
    print(f"\n조각 {total}개 적재, {time.time() - t0:.0f}초")
    prune(docs, skipped, dry=False, force=force)
    rep = audit(docs, skipped)
    stats()
    return rep


# ---------------- 8 prune ----------------
_HOLD_HELP = {"source_unparsed": "원본 파일은 있으나 doc 메타 없음", "mask_rules_unavailable": "마스킹 규칙 파일 오류(사본 보류)"}
PRUNE_MAX_RATIO = 0.25     # 저장된 문서의 25% 넘게 지우게 되면 멈춘다(마운트 사고·폴더 오지정 대비). --force로 무시


def _stored_docs(coll):
    """{doc_id: {n, files{basename}, hashes{}}} — 한 컬렉션에 저장된 문서별 요약."""
    per = {}
    for c in internal_store.chunks(coll):
        m = c["metadata"]
        e = per.setdefault(m.get("doc_id") or "", {"n": 0, "files": set(), "hashes": set()})
        e["n"] += 1
        if m.get("source_file"):
            e["files"].add(os.path.basename(m["source_file"]))
        e["hashes"].add(m.get("doc_content_hash") or "")
    return per


def plan_prune(docs, skipped=(), mask_ok=None):
    """지울 것 [(컬렉션, doc_id, 사유, 조각 수)] + 보류 [(…)].

    `gone`  = 그 doc_id의 원본이 없다(어떤 원본 문서도 그 doc_id가 아님)
    `moved` = 원본은 있는데 등급이 바뀌어 지금은 다른 컬렉션 소속(옛 등급 컬렉션에 남은 조각)
    저장된 source_file이 폴더에 있는데 doc 메타가 없어 파싱되지 않은 경우는 **지우지 않고 보류**한다
    (원본이 사라진 게 아니라 메타 주석이 깨진 것일 수 있다).
    """
    want = {d["doc_id"]: route(d) for d in docs}
    skipped = set(skipped)
    if mask_ok is None:
        mask_ok = MASK_STATE.get("ok", False)
    plan, hold = [], []
    for coll in (internal_store.PUBLIC, internal_store.RESTRICTED):
        for did, e in _stored_docs(coll).items():
            if did in want:
                if want[did] != coll:
                    plan.append((coll, did, "moved", e["n"]))
                continue
            if e["files"] & skipped:
                hold.append((coll, did, "source_unparsed", e["n"]))
                continue
            if did.startswith(internal_masking.MSK_PREFIX) and not mask_ok:
                hold.append((coll, did, "mask_rules_unavailable", e["n"]))   # 규칙 파일 사고 — 사본을 지우지 않는다
                continue
            plan.append((coll, did, "gone", e["n"]))
    return plan, hold


def prune(docs, skipped=(), dry=False, force=False):
    """8 prune — 원본에서 사라진 문서 · 등급이 바뀐 문서의 옛 조각을 **두 컬렉션 모두에서** 지운다(6.13.5).
    원본이 있는 문서는 지우지 않는다(교체는 6·7 단계가 한다)."""
    try:
        plan, hold = plan_prune(docs, skipped)
        stored = {d for c in (internal_store.PUBLIC, internal_store.RESTRICTED) for d in _stored_docs(c)}
    except Exception as e:
        print(f"prune: 저장소를 읽지 못함({type(e).__name__}) — 건너뜀")
        return {"deleted": [], "hold": [], "plan": [], "refused": "store_error"}
    for coll, did, why, n in hold:
        print(f"  prune 보류 [{coll.split('_')[-1]}] {did} 조각 {n} — {_HOLD_HELP.get(why, why)}")
    if not plan:
        print("prune: 지울 것 없음")
        return {"deleted": [], "hold": hold, "plan": [], "refused": None}
    gone = {did for _, did, why, _ in plan if why == "gone"}
    refused = None
    if not docs:
        refused = "no_source_docs"          # --force로도 풀지 않는다: 원본 0개면 전부 지우게 된다
    elif stored and len(gone) / len(stored) > PRUNE_MAX_RATIO and not force:
        refused = "ratio"
    for coll, did, why, n in plan:
        print(f"  prune {'(--dry-run) ' if dry else ''}[{coll.split('_')[-1]}] {did} 조각 {n} — {why}")
    if refused:
        print(f"prune 멈춤({refused}): 원본 문서 {len(docs)}개, 저장된 문서 {len(stored)}개 중 {len(gone)}개를 지우게 됨"
              f" — 폴더·마운트를 확인하고, 정말 지울 거면 --force")
        return {"deleted": [], "hold": hold, "plan": plan, "refused": refused}
    if dry:
        print(f"(--dry-run) prune 대상 {len(plan)}건")
        return {"deleted": [], "hold": hold, "plan": plan, "refused": None}
    for coll, did, _, _ in plan:
        internal_store.delete_doc(coll, did)
    print(f"prune: {len(plan)}건 정리")
    return {"deleted": plan, "hold": hold, "plan": plan, "refused": None}


# ---------------- 9 audit 감사 게이트 ----------------
CRITICAL_HELP = {
    "public_mixed": "public 컬렉션에 disclosure≠public 조각",
    "public_forbid": "public 조각 본문에 전 문서 사전의 금지 용어",
    "moved_left": "등급이 public이 아닌 문서의 조각이 public에 남음",
    "forbid_unavailable": "금지 용어 사전을 읽을 수 없음",
    "forbid_stale": "Redis 사전이 원본에서 모은 용어와 다름",
    "store_error": "Chroma를 읽을 수 없음",
    "public_meta_forbid": "public 조각 메타 문자열에 금지 용어",
    "mask_rules_unavailable": "public에 마스킹 사본이 있는데 규칙 파일을 읽을 수 없어 검사 불가",
    "mask_residual": "마스킹 사본 본문에 금액·내부 ID·문서 참조·연락처가 남음",
    "mask_dropped_left": "제외해야 할 섹션이 사본에 남음",
    "mask_source": "사본의 source_file이 없거나 공개 목록(publish)에 없음",
}


def audit(docs, skipped=(), record=True, r=None):
    """9 audit — **읽기만 한다(삭제 없음).** 반환 = 보고서 dict. record=True면 Redis `idoc:audit`에 판정을 남긴다.

    critical(하나라도 있으면 게이트 실패 → `internal_forbid.rag_ready()` False): CRITICAL_HELP 참고.
    warnings(게이트는 통과, 보고만): count_mismatch · total_mismatch · hash_stale · missing · orphan · mixed_hash ·
      embed_model · restricted_has_public · unknown_dept(H-09) · source_unparsed
    보고서·기록에는 조각 본문·금지 용어 원문을 넣지 않는다(B-08) — 조각 ID·doc_id·건수·사유 종류만.
    """
    from app import config
    rep = {"ok": False, "critical": set(), "warnings": set(), "details": {},
           "counts": {"public": 0, "restricted": 0}, "disclosure": {}, "doc_type": {}, "dict_hash": ""}
    det = rep["details"]

    def add(level, kind, item):
        rep[level].add(kind)
        det.setdefault(kind, []).append(item)

    expected = {d["doc_id"]: {"coll": route(d), "n": len(build_chunks(d)), "hash": d["doc_hash"]} for d in docs}
    src_terms = internal_forbid.collect(docs)

    try:
        terms = internal_forbid.load_terms(r)
        rep["dict_hash"] = internal_forbid.terms_hash(terms)
        if rep["dict_hash"] != internal_forbid.terms_hash(src_terms):
            add("critical", "forbid_stale", f"redis {len(terms)} / source {len(src_terms)}")
    except internal_forbid.ForbidUnavailable as e:
        terms = sorted(src_terms)                                  # 본문 검사는 원본 용어로라도 한다
        add("critical", "forbid_unavailable", str(e))

    # 조각 검사는 일반 단어(GENERIC_TERMS)를 뺀 용어로 — 검색 ⑧과 같은 기준(internal_forbid.chunk_terms)
    cterms = internal_forbid.chunk_terms(terms)
    mst = MASK_STATE
    comp = mst.get("compiled") if mst.get("ok") else None
    dropped = {d["doc_id"]: set(d.get("dropped_titles") or []) for d in docs if d.get("masked")}
    publish = mst.get("publish") or set()

    try:
        pub = internal_store.chunks(internal_store.PUBLIC, documents=True)
        res = internal_store.chunks(internal_store.RESTRICTED)
    except Exception as e:
        add("critical", "store_error", type(e).__name__)
        pub, res = [], []
    rep["counts"] = {"public": len(pub), "restricted": len(res)}

    for name, coll, rows in (("public", internal_store.PUBLIC, pub), ("restricted", internal_store.RESTRICTED, res)):
        per = {}
        for c in rows:
            m = c["metadata"]
            did = m.get("doc_id") or ""
            dis = m.get("disclosure") or "?"
            dmix = rep["disclosure"].setdefault(name, {})
            dmix[dis] = dmix.get(dis, 0) + 1
            dt = m.get("doc_type") or "?"
            rep["doc_type"][dt] = rep["doc_type"].get(dt, 0) + 1
            e = per.setdefault(did, {"n": 0, "hashes": set()})
            e["n"] += 1
            e["hashes"].add(m.get("doc_content_hash") or "")
            if (m.get("embed_model") or "") != config.EMBED_MODEL:
                add("warnings", "embed_model", c["id"])
            for dept in [x for x in (m.get("dept") or "").split("|") if x]:
                if dept not in DEPTS:
                    add("warnings", "unknown_dept", f"{did}:{dept}")
            if name == "public":
                is_m = bool(m.get("masked")) or did.startswith(internal_masking.MSK_PREFIX)
                # public 메타 문자열 필드 전체 금지 용어 0건(7.8 ⑤). 원본 공개판의 forbid_terms는 검사 목록 자체라 제외
                for fk, fv in m.items():
                    if isinstance(fv, str) and (is_m or fk != "forbid_terms") and internal_forbid.find_terms(fv, cterms):
                        add("critical", "public_meta_forbid", f"{c['id']}:{fk}")
                        break
                if is_m:
                    if comp is None:
                        add("critical", "mask_rules_unavailable", c["id"])
                    else:
                        if comp.residual(c.get("document") or ""):
                            add("critical", "mask_residual", c["id"])
                        if m.get("sec_title") in dropped.get(did, set()):
                            add("critical", "mask_dropped_left", c["id"])
                        sf = os.path.basename(m.get("source_file") or "")
                        if not sf or internal_masking.file_num(sf) not in publish:
                            add("critical", "mask_source", c["id"])
                if dis != "public":
                    add("critical", "public_mixed", c["id"])
                if internal_forbid.find_terms(c.get("document") or "", cterms):
                    add("critical", "public_forbid", c["id"])
            elif dis == "public":
                add("warnings", "restricted_has_public", c["id"])
        for did, e in per.items():
            exp = expected.get(did)
            if exp is None:
                add("warnings", "orphan", f"{name}:{did}")
            elif exp["coll"] != coll:
                add("critical" if name == "public" else "warnings", "moved_left", f"{name}:{did}")
            else:
                if len(e["hashes"]) > 1:
                    add("warnings", "mixed_hash", did)
                if exp["hash"] not in e["hashes"]:
                    add("warnings", "hash_stale", did)
                elif e["n"] != exp["n"]:
                    add("warnings", "count_mismatch", f"{did}: {e['n']}/{exp['n']}")
        for did, exp in expected.items():
            if exp["coll"] == coll and did not in per:
                add("warnings", "missing", did)
    for f in skipped:
        add("warnings", "source_unparsed", f)
    for kind in ("missing", "unlisted", "drop_mismatch"):
        for did in mst.get(kind) or []:
            add("warnings", f"mask_{kind}", did)
    for d in docs:                              # 밀도 표시는 위 expected 계산(build_chunks)에서 채워진다
        for sec in (d.get("density_flags") or []) if d.get("masked") else []:
            add("warnings", "mask_density", f"{d['doc_id']}#{sec}")

    rep["expected_total"] = sum(e["n"] for e in expected.values())
    rep["stored_total"] = rep["counts"]["public"] + rep["counts"]["restricted"]
    if rep["expected_total"] != rep["stored_total"]:
        add("warnings", "total_mismatch", f"{rep['stored_total']}/{rep['expected_total']}")
    rep["terms"], rep["chunk_terms"] = len(terms), len(cterms)
    rep["ok"] = not rep["critical"]

    print(f"\n감사 게이트: {'통과' if rep['ok'] else '실패 (H-08 — 사내문서 경로 차단)'} · public {rep['counts']['public']}"
          f" · restricted {rep['counts']['restricted']} · 원본 기준 {rep['expected_total']}조각 · 사전 {len(terms)}용어(조각 검사 {len(cterms)})")
    print(f"  disclosure 분포: {rep['disclosure']}")
    for lv in ("critical", "warnings"):
        for k in sorted(rep[lv]):
            items = det.get(k, [])
            print(f"  [{lv}] {k} {len(items)}건 {CRITICAL_HELP.get(k, '')}"
                  + (f" — 예: {', '.join(map(str, items[:3]))}" if items else ""))
    if record:
        try:
            internal_forbid.record_audit(rep, r)
        except Exception as e:
            print(f"  감사 결과 기록 실패({type(e).__name__}) — rag_ready()는 audit_missing으로 막힌다")
    return rep


def mask_preview():
    """마스킹 사본 미리 보기 — 문서별 규칙 적용 건수와 R2 사람 검토 후보 **위치만**(원문 미출력, B-08). 쓰기 없음."""
    docs, _ = load_docs()
    if not MASK_STATE.get("ok"):
        print(f"마스킹 불가: {MASK_STATE.get('error')}")
        return MASK_STATE
    comp = MASK_STATE["compiled"]
    masked = [d for d in docs if d.get("masked")]
    print(f"마스킹 규칙 {MASK_STATE['rules_version']} · 사본 {len(masked)}개 · 고객사 가명 {MASK_STATE['customers']}개")
    keys = ("R1", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R12", "R13", "R14", "R15")
    total_chunks = 0
    for d in masked:
        c = d.get("mask_counts") or {}
        n = len(build_chunks(d))
        total_chunks += n
        cand = internal_masking.reid_candidates(d, comp)
        print(f"  {d['doc_id']:<7} 조각 {n:3d}  " + " ".join(f"{k}={c.get(k, 0)}" for k in keys)
              + (f"  R2 검토 {len(cand)}곳 {cand[:6]}" if cand else ""))
    print(f"사본 조각 합계 {total_chunks} · 규칙별 합계 {MASK_STATE['counts']}")
    return MASK_STATE


def stats():
    print(f"\n저장 위치: {internal_store.PERSIST_DIR}")
    for c in (internal_store.PUBLIC, internal_store.RESTRICTED):
        print(f"  {c:<26} 조각 {internal_store.count(c)}개")


def main(argv=None):
    ap = argparse.ArgumentParser(description="사내문서 RAG 적재·동기화 (ADLC v0.7 6.13)")
    ap.add_argument("cmd", choices=["ingest", "sync", "prune", "audit", "forbid", "mask", "stats"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="prune 안전장치(삭제 비율 상한) 무시")
    a = ap.parse_args(argv)
    if a.cmd == "stats":
        stats()
    elif a.cmd == "mask":
        mask_preview()
    elif a.cmd == "forbid":
        docs, _ = load_docs()
        build_forbid(docs, a.dry_run)
    elif a.cmd == "prune":
        docs, skipped = load_docs()
        prune(docs, skipped, dry=a.dry_run, force=a.force)
    elif a.cmd == "audit":
        docs, skipped = load_docs()
        rep = audit(docs, skipped, record=not a.dry_run)
        sys.exit(2 if rep["critical"] else (1 if rep["warnings"] else 0))
    else:
        run("ingest" if a.cmd == "ingest" else "sync", a.dry_run, a.force)


if __name__ == "__main__":
    main()
