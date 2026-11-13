"""수집 공고 RAG 재생성 핵심 함수 (2026-11-12, 개발 도구 — 제품 실행 경로 아님).

원문(`job_posting_resources/<site>/*.json`) → 조각 → llm.embed(qwen3-embedding:0.6b) → Chroma `job_postings_ref`
→ 시드 공고마다 같은 직무 참고 조각 검색 → llm.chat_json(LLM_MODEL)으로 새 공고 초안 → 원문 유사성 검사 → 검토용 파일.

- 모델 호출은 llm.chat_json · llm.embed만(외부 API 없음, CLAUDE.md 2026-11-11 임베딩 로컬 Qwen).
- Chroma는 `job_postings_ref` 컬렉션만 연다. 사내문서 컬렉션(internal_docs_*)은 건드리지 않는다.
- DB에는 **쓰지 않는다**(시드 공고 SELECT만). 공고는 기업이 확정한다(PRD JOB-08~10) → 결과는 검토용 파일만.
- 결과 파일에 참고 원문 본문을 넣지 않는다(출처 id·URL·유사도만).
- 순서 제어는 LangGraph `job_posting_graph`, CLI·자체 시험은 `backend/tools/regenerate_jobs.py`.

원문 JSON 공통 형식(파일 1개 = 공고 1개): {source, id, company, title, role_code, url, fetched_at, text}
  collect_jobs.py 형식(site · collected_at · body_text)도 normalize_raw가 맞춰 읽는다.

원문 유사성 검사(코딩 문제 생성과 같은 0.90 기준):
  - 문장: 참고 원문 문장과 임베딩 코사인 ≥ 0.90 또는 공백 정리 후 13자 연속 일치 → 1회 고쳐 쓰기, 그래도 걸리면 버림
  - 공고 전체: 초안 전체 vs 참고 공고 원문 전체 코사인 ≥ 0.90 → 폐기
"""
import datetime as dt
import hashlib
import json
import math
import os
import re
from pathlib import Path

# 원문 폴더: 컨테이너에서는 /job_posting_resources(마운트), 호스트에서는 저장소 루트
_REPO = Path(__file__).resolve().parents[3]
RESOURCES_DIR = os.getenv("JOB_RESOURCES_DIR") or (
    "/job_posting_resources" if os.path.isdir("/job_posting_resources") else str(_REPO / "job_posting_resources"))

COLLECTION = "job_postings_ref"
SENT_COS = 0.90
DOC_COS = 0.90
NGRAM = 13
CHUNK_MAX = 600
ROLE_CODES = ["ai_ax", "backend", "data_eng", "data_sci", "devops", "frontend", "mobile"]
SECTIONS = ("overview", "duties", "requirements", "preferred", "tech_stack")
SEC_LABEL = {"overview": "직무 개요", "duties": "주요 업무", "requirements": "자격 요건",
             "preferred": "우대 사항", "tech_stack": "기술 스택"}

# 모델 호출 지점 — selftest가 가짜 함수로 바꿔 끼운다
EMBED = None
CHAT = None


def use_local_models(force=False):
    """EMBED·CHAT을 로컬 llm.embed·llm.chat_json으로. 시험에서 가짜 함수를 끼웠으면(force=False) 그대로 둔다."""
    global EMBED, CHAT
    from .. import llm
    if force or EMBED is None:
        EMBED = llm.embed
    if force or CHAT is None:
        CHAT = llm.chat_json


# ---------------- 원문 ----------------
def normalize_raw(d):
    """출처별 키 차이를 공통 형식으로."""
    rec = {
        "source": d.get("source") or d.get("site") or "unknown",
        "id": str(d.get("id") or d.get("rec_idx") or ""),
        "company": d.get("company") or "",
        "title": d.get("title") or "",
        "role_code": d.get("role_code") or "",
        "url": d.get("url") or "",
        "fetched_at": d.get("fetched_at") or d.get("collected_at") or "",
        "text": d.get("text") or d.get("body_text") or "",
    }
    if not rec["id"] or not rec["text"] or rec["role_code"] not in ROLE_CODES:
        return None
    rec["key"] = f"{rec['source']}_{rec['id']}"
    rec["hash"] = hashlib.sha256(rec["text"].encode("utf-8")).hexdigest()[:16]
    return rec


def load_raw(raw_dir):
    out = []
    for p in sorted(Path(raw_dir).rglob("*.json")):   # saramin/ · wanted/ 하위 폴더(2026-11-13)
        try:
            r = normalize_raw(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            r = None
        if r:
            out.append(r)
    return out


def chunk(text, limit=CHUNK_MAX):
    """빈 줄·제목 줄 기준 문단 → limit 이하로 묶는다. 긴 문단은 줄 단위로 자른다."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces = []
    for p in paras:
        if len(p) <= limit:
            pieces.append(p)
            continue
        buf = ""
        for line in p.splitlines():
            if buf and len(buf) + len(line) + 1 > limit:
                pieces.append(buf)
                buf = ""
            buf = (buf + "\n" + line).strip()
            while len(buf) > limit:
                pieces.append(buf[:limit])
                buf = buf[limit:]
        if buf:
            pieces.append(buf)
    out, buf = [], ""
    for p in pieces:
        if buf and len(buf) + len(p) + 2 > limit:
            out.append(buf)
            buf = ""
        buf = (buf + "\n\n" + p).strip()
    if buf:
        out.append(buf)
    return [c for c in out if len(c) >= 30]


# ---------------- Chroma ----------------
def _coll(persist_dir=None):
    import chromadb
    if persist_dir is None:
        from .. import internal_store
        persist_dir = internal_store.PERSIST_DIR
    os.makedirs(persist_dir, exist_ok=True)
    c = chromadb.PersistentClient(path=persist_dir)
    return c.get_or_create_collection(name=COLLECTION, metadata={"hnsw:space": "cosine"})


def stored_hashes(coll):
    """{원문 key: hash} — 증분 적재 판단용."""
    have = {}
    got = coll.get(include=["metadatas"])
    for m in got.get("metadatas") or []:
        if m and m.get("key"):
            have[m["key"]] = m.get("hash")
    return have


def changed(coll, recs):
    have = stored_hashes(coll)
    return [r for r in recs if have.get(r["key"]) != r["hash"]], len(have)


def upsert_one(coll, r):
    """공고 1개를 조각 → 임베딩 → 문서째 교체. 성공 True, 임베딩 실패 False(다음 실행이 재시도)."""
    chunks = chunk(r["text"])
    if not chunks:
        return False
    try:
        vecs = EMBED([f"{r['title']}\n{c}" for c in chunks])
    except Exception as e:
        print(f"[ingest] 임베딩 실패 {r['key']}: {e} — 다음 실행 때 재시도", flush=True)
        return False
    coll.delete(where={"key": r["key"]})
    coll.upsert(
        ids=[f"{r['key']}#{i}" for i in range(len(chunks))],
        documents=chunks,
        embeddings=vecs,
        metadatas=[{"key": r["key"], "source": r["source"], "rec_id": r["id"], "role_code": r["role_code"],
                    "company": r["company"], "title": r["title"], "url": r["url"], "hash": r["hash"],
                    "chunk_no": i} for i in range(len(chunks))],
    )
    return True


def ingest(raw_dir, persist_dir=None, dry=False):
    coll = _coll(persist_dir)
    recs = load_raw(raw_dir)
    todo, n_have = changed(coll, recs)
    print(f"[ingest] 원문 {len(recs)}개 · 바뀐/새 공고 {len(todo)}개 · 기존 {n_have}개", flush=True)
    if dry:
        return {"total": len(recs), "todo": len(todo), "ingested": 0}
    done = 0
    for r in todo:
        if upsert_one(coll, r):
            done += 1
            if done % 10 == 0:
                print(f"[ingest] {done}/{len(todo)}", flush=True)
    print(f"[ingest] 완료 {done}개 · 컬렉션 조각 {coll.count()}개", flush=True)
    return {"total": len(recs), "ingested": done, "chunks": coll.count()}


def search(coll, vector, role_code, k):
    r = coll.query(query_embeddings=[vector], n_results=k, where={"role_code": role_code},
                   include=["documents", "metadatas", "distances"])
    return [{"id": cid, "text": r["documents"][0][i], "meta": r["metadatas"][0][i],
             "sim": round(1.0 - r["distances"][0][i], 4)} for i, cid in enumerate(r["ids"][0])]


# ---------------- 유사성 검사 ----------------
def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def sentences(text):
    parts = re.split(r"(?<=[.!?。])\s+|\n+|(?<=다\.)", text)
    return [p.strip(" •-·*\t") for p in parts if len(p.strip(" •-·*\t")) >= 8]


def ngram_set(texts, n=NGRAM):
    s = set()
    for t in texts:
        t = _norm(t)
        for i in range(len(t) - n + 1):
            s.add(t[i:i + n])
    return s


def has_copy(sentence, grams, n=NGRAM):
    t = _norm(sentence)
    return any(t[i:i + n] in grams for i in range(len(t) - n + 1))


def cos(a, b):
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


# ---------------- 생성 ----------------
SYSTEM = """너는 IT 채용 공고 작성 도우미다. 한국어로 쓴다.
아래 [참고 공고 조각]은 다른 회사의 실제 공고 일부다. 이 직무의 공고가 보통 어떤 요건 구조와 경향(업무 범위, 요구 경력 수준,
자주 요구되는 기술, 우대 경험의 종류)을 갖는지 파악하는 데에만 써라.
- 참고 공고의 문장·표현을 그대로 옮기거나 어순만 바꿔 쓰지 마라. 모든 문장을 [우리 회사 공고]의 사업·서비스 맥락으로 새로 써라.
- 참고 공고에 나온 회사명·제품명·서비스명·고객사명·수치(연봉·인원·연차 외 수치)는 쓰지 마라.
- [우리 회사 공고]에 없는 사실(회사 규모·매출·복지 등)을 지어내지 마라.
- 각 항목은 짧은 한 문장. duties 4~6개, requirements 4~6개, preferred 3~5개, tech_stack 5~10개(기술 이름만), overview 2~3문장.
JSON으로만 답한다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "duties": {"type": "array", "items": {"type": "string"}},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "preferred": {"type": "array", "items": {"type": "string"}},
        "tech_stack": {"type": "array", "items": {"type": "string"}},
    },
    "required": list(SECTIONS),
}

REWRITE_SYSTEM = """너는 채용 공고 문장을 고쳐 쓰는 도우미다. 주어진 문장이 다른 회사 공고와 표현이 너무 비슷하다.
뜻(요구하는 역량·업무)은 유지하되 단어 선택과 문장 구조를 완전히 바꿔 한 문장으로 다시 써라. 회사 맥락: {ctx}
JSON {"sentence": "..."} 로만 답한다."""
REWRITE_SCHEMA = {"type": "object", "properties": {"sentence": {"type": "string"}}, "required": ["sentence"]}


def build_user(job, refs):
    ours = (f"회사: {job['company_name']}\n직무: {job['role_name']}\n공고 제목: {job['title']}\n"
            f"회사 소개: {job.get('company_intro') or ''}\n현재 주요 업무 메모: {job.get('duties') or ''}\n"
            f"현재 자격 요건 메모: {job.get('requirements') or ''}")
    ref_txt = "\n\n".join(f"[참고 {i + 1}]\n{r['text']}" for i, r in enumerate(refs))
    return f"[우리 회사 공고]\n{ours}\n\n[참고 공고 조각]\n{ref_txt}\n\n위 규칙대로 새 공고 초안을 JSON으로 써라."


def draft_items(d):
    """(section, idx, sentence) 목록. overview는 문장 단위로 쪼갠다."""
    items = [("overview", i, s) for i, s in enumerate(sentences(d.get("overview") or ""))]
    for sec in ("duties", "requirements", "preferred"):
        items += [(sec, i, s.strip()) for i, s in enumerate(d.get(sec) or []) if s and s.strip()]
    return items


def check_and_fix(job, draft, refs, ref_full_texts):
    """문장 검사 → 1회 고쳐 쓰기 → 그래도 걸리면 버림. 반환: (정리된 초안, 문장 검사 기록)."""
    grams = ngram_set(ref_full_texts)
    ref_sents = []
    for t in ref_full_texts:
        ref_sents += sentences(t)
    ref_sents = list(dict.fromkeys(ref_sents))[:400]
    ref_vecs = EMBED(ref_sents) if ref_sents else []
    ctx = f"{job['company_name']} · {job['title']}"

    def score(s):
        v = EMBED([s])[0]
        mx = max((cos(v, rv) for rv in ref_vecs), default=0.0)
        return round(mx, 4), has_copy(s, grams)

    log, clean = [], {"overview": [], "duties": [], "requirements": [], "preferred": []}
    for sec, _, s in draft_items(draft):
        sim, cp = score(s)
        entry = {"section": sec, "max_cos": sim, "ngram_copy": cp, "action": "keep"}
        if sim >= SENT_COS or cp:
            try:
                s2 = (CHAT(REWRITE_SYSTEM.replace("{ctx}", ctx), s, REWRITE_SCHEMA) or {}).get("sentence", "").strip()
            except Exception:
                s2 = ""
            sim2, cp2 = score(s2) if s2 else (1.0, True)
            entry.update({"rewritten": True, "max_cos_after": sim2, "ngram_copy_after": cp2})
            if s2 and sim2 < SENT_COS and not cp2:
                s, entry["action"] = s2, "rewrite"
            else:
                entry["action"] = "drop"
                log.append(entry)
                continue
        clean[sec].append(s)
        entry["text"] = s
        log.append(entry)
    out = {"overview": " ".join(clean["overview"]), "duties": clean["duties"],
           "requirements": clean["requirements"], "preferred": clean["preferred"],
           "tech_stack": [t.strip() for t in (draft.get("tech_stack") or []) if t and t.strip()][:12]}
    return out, log


def draft_text(d):
    return "\n".join([d["overview"]] + d["duties"] + d["requirements"] + d["preferred"] + [", ".join(d["tech_stack"])])


def regenerate_one(coll, job, k):
    qtext = f"{job['role_name']} {job['title']}\n{job.get('duties') or ''}"[:1500]
    qv = EMBED([qtext])[0]
    refs = search(coll, qv, job["role_code"], k)
    if not refs:
        return {"job_id": job["id"], "status": "no_refs"}
    # 참고 공고 원문 전체(같은 공고 조각을 이어 붙임) — 문장·n-gram·전체 유사도 비교 대상
    keys = list(dict.fromkeys(r["meta"]["key"] for r in refs))
    full = {}
    for key in keys:
        g = coll.get(where={"key": key}, include=["documents", "metadatas"])
        pairs = sorted(zip(g["metadatas"], g["documents"]), key=lambda x: x[0].get("chunk_no", 0))
        full[key] = "\n\n".join(doc for _, doc in pairs)
    try:
        raw = CHAT(SYSTEM, build_user(job, refs), SCHEMA, temperature=0.5, max_tokens=1600, num_ctx=8192)
    except Exception as e:
        return {"job_id": job["id"], "status": "llm_failed", "error": str(e)[:200]}
    draft, sent_log = check_and_fix(job, raw, refs, list(full.values()))
    dvec = EMBED([draft_text(draft)])[0]
    fvecs = EMBED([t[:4000] for t in full.values()])
    doc_sims = {key: round(cos(dvec, v), 4) for key, v in zip(full.keys(), fvecs)}
    max_doc = max(doc_sims.values()) if doc_sims else 0.0
    passed = max_doc < DOC_COS and len(draft["duties"]) >= 3 and len(draft["requirements"]) >= 3
    meta = {r["meta"]["key"]: r["meta"] for r in refs}
    return {
        "job_id": job["id"], "company": job["company_name"], "role_code": job["role_code"], "title": job["title"],
        "status": "pass" if passed else ("discard_similar" if max_doc >= DOC_COS else "discard_too_short"),
        "draft": draft if passed else None,
        "references": [{"source": meta[key]["source"], "rec_id": meta[key]["rec_id"], "url": meta[key]["url"],
                        "retrieval_sim": max(r["sim"] for r in refs if r["meta"]["key"] == key),
                        "doc_cos": doc_sims[key]} for key in keys],
        "max_doc_cos": max_doc,
        "sentences": {"total": len(sent_log),
                      "kept": sum(1 for e in sent_log if e["action"] == "keep"),
                      "rewritten": sum(1 for e in sent_log if e["action"] == "rewrite"),
                      "dropped": sum(1 for e in sent_log if e["action"] == "drop"),
                      "max_cos": max((e["max_cos"] for e in sent_log), default=0.0)},
        "sentence_log": [{k2: v for k2, v in e.items() if k2 != "text"} for e in sent_log],
    }


def seed_jobs(roles=None):
    """재생성 대상 = 개발 테스트 기업(헤드헌터랩_서울·경기·인천)의 게시 공고. roles를 주면 그 직무만. SELECT만 한다."""
    from .. import db
    if getattr(db.pool, "closed", False):
        db.pool.open(wait=True)          # CLI 실행: 앱 시작 때 여는 연결 풀을 직접 연다(읽기만)
    rows = db.all_("""SELECT j.id, j.role_code, j.title, j.company_intro, j.duties, j.requirements,
                             c.name company_name, r.name role_name
                      FROM jobs j JOIN companies c ON c.id=j.company_id JOIN job_roles r ON r.code=j.role_code
                      WHERE c.name LIKE '헤드헌터랩\\_%%' AND j.status='published' ORDER BY j.id""")
    return [r for r in rows if not roles or r["role_code"] in roles]


def write_report(results, out_dir, name, info):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"), **info, "results": results}
    (out / f"{name}.json").write_bytes(json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))
    L = [f"# 공고 재생성 결과 ({name})", "",
         f"- 생성 {info['generated_at_kst'] if 'generated_at_kst' in info else payload['generated_at']} · 모델 `{info['llm_model']}` · 임베딩 `{info['embed_model']}`",
         f"- 기준: 문장 코사인 ≥ {SENT_COS} 또는 {NGRAM}자 연속 일치 → 1회 고쳐 쓰기 후 실패 시 버림 · 공고 전체 코사인 ≥ {DOC_COS} → 폐기",
         "- **DB에 저장·게시하지 않는다**(PRD JOB-08~10, 기업 담당자가 검토 후 확정). 참고 원문 본문은 싣지 않는다", "",
         "| 공고 | 직무 | 상태 | 최대 전체 코사인 | 문장 유지/고침/버림 | 문장 최대 코사인 |", "|---|---|---|---|---|---|"]
    for r in results:
        s = r.get("sentences") or {}
        L.append(f"| #{r['job_id']} {r.get('company', '')} · {r.get('title', '')} | {r.get('role_code', '')} | {r['status']} | "
                 f"{r.get('max_doc_cos', '')} | {s.get('kept', '')}/{s.get('rewritten', '')}/{s.get('dropped', '')} | {s.get('max_cos', '')} |")
    for r in results:
        L += ["", f"## #{r['job_id']} {r.get('company', '')} — {r.get('title', '')}", "", f"상태: **{r['status']}**", ""]
        d = r.get("draft")
        if d:
            L += [f"**{SEC_LABEL['overview']}**", "", d["overview"], ""]
            for sec in ("duties", "requirements", "preferred"):
                L += [f"**{SEC_LABEL[sec]}**", ""] + [f"- {x}" for x in d[sec]] + [""]
            L += [f"**{SEC_LABEL['tech_stack']}**: " + ", ".join(d["tech_stack"]), ""]
        if r.get("references"):
            L += ["참고 조각 출처:", ""] + [f"- {x['source']} {x['rec_id']} ({x['url']}) — 검색 유사도 {x['retrieval_sim']}, 전체 코사인 {x['doc_cos']}"
                                         for x in r["references"]]
    (out / f"{name}.md").write_bytes(("\n".join(L) + "\n").encode("utf-8"))
    print(f"[report] {out / (name + '.json')} · .md", flush=True)


def regenerate(out_dir, k, name, persist_dir=None, jobs=None):
    from .. import config
    coll = _coll(persist_dir)
    if coll.count() == 0:
        raise SystemExit("job_postings_ref 컬렉션이 비어 있음 — 먼저 ingest")
    jobs = jobs if jobs is not None else seed_jobs()
    results = []
    for j in jobs:
        r = regenerate_one(coll, j, k)
        print(f"[regen] #{j['id']} {j['company_name']} {j['title']} → {r['status']}", flush=True)
        results.append(r)
    write_report(results, out_dir, name, {"llm_model": config.LLM_MODEL, "embed_model": config.EMBED_MODEL,
                                          "k": k, "collection_chunks": coll.count()})
    return results
