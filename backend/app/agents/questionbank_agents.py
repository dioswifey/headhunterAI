"""질문 은행 → Redis 벡터 저장소: 적재 에이전트 · 동기화 에이전트 (LCEL, v0.7). 번역은 언어 번역 에이전트를 부른다

두 에이전트 모두 langchain-core의 Runnable(LCEL)로 조립한다. 모델 호출은 직접 하지 않고
`llm.embed`(임베딩)와 언어 번역 에이전트(`agents/translation_agent.py`, 안에서 `llm.chat_json`)만 부른다.

    공통 부품
      load_sources  : 원본 파일 → Document 목록 (CSV 영어 원본 · questionbank.xlsx 시트)
      hash_docs     : 원본 행의 SHA-256을 metadata.content_hash에 붙임
      embed_docs    : 임베딩 → 저장용 레코드
      write_records : Redis에 쓰기

    ⓪' scope_docs   : 범위 필터 스킬(번역 에이전트의 kind: scope). IT 면접용 — 언어는 한국어·영어만, 나라·문화는 한국·영어권만.
                      질문 자체가 다른 나라·언어를 다루면 제외(삭제), 곁가지 칸만 걸리면 한국 맥락으로 바꿈(adapt).
                      제외된 문항은 원본에 남아 있어도 Redis에서 지운다(sync·ingest 때마다). 스킬 판단은 qbmeta:scope에 캐시
    ⓪ translate_docs: 문항마다 언어 번역 에이전트(Skills 패턴)를 불러 영어 원본 → 한국어, 한국어 원본 → 영어를 채운다
                      (질문·평가 의도·꼬리질문, *_ko·*_en 두 벌). 번역 스킬 → '어색하지 않게' 다듬기 스킬 → 규칙 검사.
                      실패면 translated=0 → 다음 sync 때 재시도. 벡터 저장 '전'에 반드시 거친다
      enrich        = translate_docs | embed_docs              (①②가 같이 씀 → 번역 없이 저장되는 경로 없음)

    ① ingest_agent (전체 적재)  : load → scope → hash → ensure_index → enrich(번역→임베딩) → write → 원본에 없는 문서 삭제
    ② sync_agent   (변경 동기화): load → scope → hash → diff(저장된 해시와 비교) → 추가·변경·번역 재시도분만 enrich → write → 삭제분 delete

실행(컨테이너 안):
    python -m app.agents.questionbank_agents ingest --roles ai_ax
    python -m app.agents.questionbank_agents sync               # 모든 직무, 바뀐 것만
    python -m app.agents.questionbank_agents sync --dry-run     # 무엇이 바뀌는지만 출력
    python -m app.agents.questionbank_agents stats
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import time
from operator import itemgetter

from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from .. import config, llm, vectorstore
from . import translation_agent

QB = config.QUESTIONBANK_DIR
SOURCES = {
    # 직무 코드 → 원본 목록. 직무·파일을 늘리면 여기에 한 줄 추가한다
    "ai_ax": [("csv_aidev", os.path.join(QB, "ai-engineering-interview-questions-main", "data",
                                          "ai-engineering-interview-questions.csv")),
              ("xlsx", os.path.join(QB, "questionbank.xlsx"))],
    # 인성 = questionbank/character_assessment/*.md (2026-11-09). xlsx common 시트의 인성 8행은 md로 옮겼으므로 md판만 적재(중복 방지)
    "common": [("character_md", os.path.join(QB, "character_assessment")),
               ("xlsx", os.path.join(QB, "questionbank.xlsx"))],
    "backend": [("xlsx", os.path.join(QB, "questionbank.xlsx"))],
    "data_sci": [("xlsx", os.path.join(QB, "questionbank.xlsx"))],
}


# ---------------- 원본 읽기 ----------------
def _rel(path):
    return os.path.relpath(path, QB).replace("\\", "/")


def _load_csv_aidev(path):
    """aidevdayindia 'AI Engineering Interview Questions' CSV (영어, 7개 세부 직무 × 30)."""
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    docs = []
    for x in rows:
        doc_id = f"ai_ax:{x['role_slug']}:{int(x['question_id']):02d}"
        meta = {
            "doc_id": doc_id, "role_code": "ai_ax", "category": "job", "lang": "en",
            "sub_role": x["role_slug"], "sub_role_name": x["role_name"], "competency": x["competency"],
            "difficulty": x["difficulty"], "question": x["question"], "intent": x["what_it_tests"],
            "model_answer": x["model_answer"], "red_flags": x["red_flags"], "follow_up": x["follow_up"],
            "expected_keywords": "", "dup_group": "", "source": x["source_page"], "source_file": _rel(path),
        }
        docs.append(Document(id=doc_id, page_content=x["question"], metadata=meta))
    return docs


def _load_xlsx(path, sheet):
    """questionbank.xlsx 시트 하나 (한국어, 열 = ADLC 6.2). id가 빈 행은 건너뛴다."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet not in wb.sheetnames:
        return []
    rows = list(wb[sheet].iter_rows(values_only=True))
    head = [str(h or "").strip() for h in rows[0]]
    docs = []
    for vals in rows[1:]:
        x = {h: ("" if v is None else str(v).strip()) for h, v in zip(head, vals)}
        if not x.get("id") or not x.get("question"):
            continue
        doc_id = f"{sheet}:{x['id']}"
        meta = {
            "doc_id": doc_id, "role_code": x.get("role_code") or sheet, "category": x.get("category") or "job",
            "lang": "ko", "sub_role": "", "sub_role_name": "", "competency": x.get("subcategory", ""),
            "difficulty": x.get("difficulty", ""), "question": x["question"], "intent": x.get("intent", ""),
            "model_answer": x.get("ideal_answer", ""), "red_flags": "", "follow_up": "",
            "expected_keywords": x.get("expected_keywords", ""), "dup_group": x.get("dup_group", ""),
            "source": x.get("source", ""), "source_file": f"{_rel(path)}#{sheet}",
        }
        docs.append(Document(id=doc_id, page_content=x["question"], metadata=meta))
    return docs


def _load_character_md(path):
    """인성 질문 은행 md (questions.load_character_bank 형식). 좋은 답변 신호 → model_answer, 파고들 지점 → follow_up."""
    from .. import questions
    docs = []
    for it in questions.load_character_bank(path):
        doc_id = f"common:{it['char_id']}"
        meta = {
            "doc_id": doc_id, "role_code": "common", "category": "personality", "lang": "ko",
            "sub_role": "character_assessment", "sub_role_name": "", "competency": it["competency"],
            "difficulty": it["level"], "question": it["text"], "intent": it["intent"],
            "model_answer": it["signals"], "red_flags": "", "follow_up": it["probes"],
            "expected_keywords": "", "dup_group": it["dup_group"], "source": "직접 작성",
            "source_file": f"{_rel(path)}/{it['file']}",
        }
        docs.append(Document(id=doc_id, page_content=it["text"], metadata=meta))
    return docs


def _load_sources(inp):
    roles = inp.get("roles") or list(SOURCES)
    docs, seen = [], set()
    for role in roles:
        has_char = False
        for kind, path in SOURCES.get(role, []):
            if not os.path.exists(path):
                continue
            if kind == "csv_aidev":
                part = _load_csv_aidev(path)
            elif kind == "character_md":
                part = _load_character_md(path)
                has_char = has_char or bool(part)
            else:
                part = _load_xlsx(path, role)
                if has_char:                     # 인성 원본 = character_assessment md(같은 문항을 옮김) → xlsx 사본은 중복이라 적재하지 않음. 인성은 md판으로 Redis에 있다
                    part = [d for d in part if d.metadata["category"] != "personality"]
            for d in part:
                if d.id not in seen:
                    seen.add(d.id)
                    docs.append(d)
    return docs


HASH_KEYS = ("role_code", "category", "lang", "sub_role", "competency", "difficulty", "question", "intent",
             "model_answer", "red_flags", "follow_up", "expected_keywords", "dup_group", "source")


def _hash_docs(docs):
    """범위 필터(adapt) 뒤에 계산 → 한국 맥락으로 바뀐 문항은 해시가 달라져 다시 번역된다."""
    for d in docs:
        d.metadata["content_hash"] = _row_hash(d.metadata)
    return docs


# ---------------- ⓪' 범위 필터 (번역 에이전트의 scope 스킬) ----------------
SCOPE_CACHE = "qbmeta:scope"       # 접두사가 qb:가 아니라 인덱스에 안 걸린다. {doc_id: {src_hash, decision, reason, fields}}


def _row_hash(m):
    blob = json.dumps({k: m.get(k, "") for k in HASH_KEYS}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _scope_docs(docs):
    """반환: {docs: 남길 문서(adapt 반영), excluded: [{doc_id, reason, by}], adapted: [{doc_id, reason}]}"""
    r = vectorstore.client()
    keep, excluded, adapted = [], [], []
    for d in docs:
        m = d.metadata
        src_hash = _row_hash(m)
        raw = r.hget(SCOPE_CACHE, d.id)
        cached = json.loads(raw) if raw else None
        if cached and cached.get("src_hash") == src_hash:
            res = cached
        else:
            res = translation_agent.scope({f: m.get(f, "") for f in translation_agent.SCOPE_FIELDS})
            if res["by"] == "skill":            # 모델 판단만 캐시(규칙은 매번 계산해도 빠름, 실패는 다음에 재시도)
                r.hset(SCOPE_CACHE, d.id, json.dumps({"src_hash": src_hash, **res}, ensure_ascii=False))
            elif cached:
                r.hdel(SCOPE_CACHE, d.id)
        if res["decision"] == "drop":
            excluded.append({"doc_id": d.id, "reason": res["reason"], "by": res["by"]})
            continue
        if res["decision"] == "adapt":
            m.update({f: res["fields"][f] for f in translation_agent.SCOPE_FIELDS})
            d.page_content = m["question"]
            m["scope"] = ("adapt: " + res["reason"])[:300]
            adapted.append({"doc_id": d.id, "reason": res["reason"]})
        keep.append(d)
    return {"docs": keep, "excluded": excluded, "adapted": adapted}


# ---------------- ⓪ 번역 단계 (언어 번역 에이전트 호출) ----------------
# 실제 번역은 agents/translation_agent.py (Skills 패턴: 번역 스킬 → '어색하지 않게' 다듬기 스킬 → 규칙 검사).
# 질문 은행은 원본 언어(lang)의 반대 언어를 채워 *_ko·*_en 두 벌을 저장한다 → 면접 때 지원자가 고른 언어로 꺼내 쓴다.
FIELDS = translation_agent.FIELDS
BANK_LANGS = ("ko", "en")


def _translate_doc(d):
    m = d.metadata
    src = m["lang"]
    for f in FIELDS:                                    # 원본 언어 쪽은 원문 그대로
        m[f"{f}_{src}"] = m.get(f, "")
    ok, models, notes = True, [], []
    for to in (lang for lang in BANK_LANGS if lang != src):
        res = translation_agent.translate({f: m.get(f, "") for f in FIELDS}, src, to, competency=m.get("competency", ""))
        for f in FIELDS:
            m[f"{f}_{to}"] = res["fields"].get(f"{f}_{to}", "") if res["ok"] else ""
        ok = ok and res["ok"]
        models.append(res["model"])
        if res["fluency_changed"] and res["fluency_notes"]:
            notes.append(res["fluency_notes"])
    m["translated"] = "1" if ok else "0"     # 실패해도 흐름을 멈추지 않는다: 원문만 두고 다음 sync 때 재시도
    m["translate_model"] = ",".join(sorted(set(models)))
    m["fluency_notes"] = " / ".join(notes)[:300]
    return d


# ---------------- 임베딩 · 쓰기 ----------------
def _embed_text(m):
    """두 언어를 함께 넣어 한국어·영어 검색어 모두에 걸리게 한다."""
    parts = [f"[{m['role_code']}/{m['category']}] {m['competency']}"]
    if m.get("question_ko"):
        parts.append(f"질문: {m['question_ko']}")
    if m.get("question_en"):
        parts.append(f"Question: {m['question_en']}")
    intent = m.get("intent_ko") or m.get("intent_en") or m["intent"]
    if intent:
        parts.append(f"평가 의도: {intent}")
    return "\n".join(parts)


def _embed_docs(docs):
    if not docs:
        return []
    vecs = llm.embed([_embed_text(d.metadata) for d in docs])
    now = int(time.time())
    recs = []
    for d, v in zip(docs, vecs):
        fields = {k: v2 for k, v2 in d.metadata.items() if k != "doc_id"}
        fields.update(updated_at=now, embed_model=config.EMBED_MODEL)
        recs.append({"doc_id": d.id, "fields": fields, "embedding": v})
    return recs


def _write(recs):
    return vectorstore.upsert(recs) if recs else 0


load_sources = RunnableLambda(_load_sources).with_config(run_name="load_sources")
hash_docs = RunnableLambda(_hash_docs).with_config(run_name="hash_docs")
scope_docs = RunnableLambda(_scope_docs).with_config(run_name="scope_docs")
# 원본 읽기 → 범위 필터 → 해시. 결과 {docs, excluded, adapted}
prepare = (load_sources | scope_docs
           | RunnableLambda(lambda x: {**x, "docs": _hash_docs(x["docs"])})).with_config(run_name="prepare")
translate_doc = RunnableLambda(_translate_doc).with_config(run_name="translate_doc")
embed_docs = RunnableLambda(_embed_docs).with_config(run_name="embed_docs")
write_records = RunnableLambda(_write).with_config(run_name="write_records")

# ⓪ 번역 단계: Document 목록 → *_ko·*_en이 채워진 Document 목록 (단독 확인: translate 명령, 저장 안 함)
translate_docs = translate_doc.map().with_config(run_name="translate_docs", max_concurrency=1)
enrich = (translate_docs | embed_docs).with_config(run_name="enrich")    # ①② 공통: 번역 → 임베딩


# ---------------- ① 적재 에이전트 ----------------
def _stale_ids(x):
    keep = {d.id for d in x["docs"]}
    return [i for i in vectorstore.stored_hashes(role_codes=x.get("roles")) if i not in keep]


ingest_agent = (
    RunnablePassthrough.assign(index=RunnableLambda(lambda x: vectorstore.ensure_index()))
    | RunnablePassthrough.assign(prep=prepare)
    | RunnablePassthrough.assign(docs=lambda x: x["prep"]["docs"])
    | RunnablePassthrough.assign(written=itemgetter("docs") | enrich | write_records)
    | RunnablePassthrough.assign(deleted=RunnableLambda(lambda x: vectorstore.delete(_stale_ids(x))))
    | RunnableLambda(lambda x: {"agent": "ingest", "index": x["index"], "source_docs": len(x["docs"]),
                                "written": x["written"], "deleted": x["deleted"],
                                "excluded": x["prep"]["excluded"], "adapted": x["prep"]["adapted"],
                                "translate_failed": sum(d.metadata["translated"] == "0" for d in x["docs"])})
).with_config(run_name="questionbank_ingest_agent")


# ---------------- ② 동기화 에이전트 ----------------
def _diff(x):
    stored = vectorstore.stored_hashes(role_codes=x.get("roles"))
    new, changed, retry, same = [], [], [], 0
    for d in x["docs"]:
        old = stored.get(d.id)
        if old is None:
            new.append(d)
        elif old[0] != d.metadata["content_hash"]:
            changed.append(d)
        elif old[1] != "1":
            retry.append(d)          # 내용은 같지만 지난번 번역 실패
        else:
            same += 1
    keep = {d.id for d in x["docs"]}
    removed = [i for i in stored if i not in keep]
    return {"new": new, "changed": changed, "retry": retry, "removed": removed, "unchanged": same}


def _apply(x):
    if x.get("dry_run"):
        return {"written": 0, "deleted": 0}
    todo = x["diff"]["new"] + x["diff"]["changed"] + x["diff"]["retry"]
    written = (enrich | write_records).invoke(todo) if todo else 0
    return {"written": written, "deleted": vectorstore.delete(x["diff"]["removed"])}


def _sync_report(x):
    df = x["diff"]
    return {"agent": "sync", "dry_run": bool(x.get("dry_run")), "index": x["index"],
            "new": [d.id for d in df["new"]], "changed": [d.id for d in df["changed"]],
            "retry_translate": [d.id for d in df["retry"]], "removed": df["removed"],
            "unchanged": df["unchanged"], "excluded": x["prep"]["excluded"], "adapted": x["prep"]["adapted"],
            **x["applied"]}


sync_agent = (
    RunnablePassthrough.assign(index=RunnableLambda(lambda x: vectorstore.ensure_index()))
    | RunnablePassthrough.assign(prep=prepare)
    | RunnablePassthrough.assign(docs=lambda x: x["prep"]["docs"])
    | RunnablePassthrough.assign(diff=RunnableLambda(_diff).with_config(run_name="diff"))
    | RunnablePassthrough.assign(applied=RunnableLambda(_apply).with_config(run_name="apply"))
    | RunnableLambda(_sync_report)
).with_config(run_name="questionbank_sync_agent")


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("ingest", "sync"):
        s = sub.add_parser(name)
        s.add_argument("--roles", nargs="*", help="직무 코드(ai_ax common backend data_sci). 생략하면 전부")
        if name == "sync":
            s.add_argument("--dry-run", action="store_true")
    t = sub.add_parser("translate", help="번역 에이전트만 실행해 결과를 본다(저장 안 함)")
    t.add_argument("--roles", nargs="*")
    t.add_argument("--limit", type=int, default=3)
    sub.add_parser("stats")
    a = p.parse_args(argv)
    if a.cmd == "stats":
        out = vectorstore.stats()
    elif a.cmd == "translate":
        docs = prepare.invoke({"roles": a.roles})["docs"]
        docs = [d for d in docs if d.metadata["lang"] == "en"][:a.limit] + [d for d in docs if d.metadata["lang"] == "ko"][:a.limit]
        out = [{k: d.metadata.get(k) for k in ("doc_id", "lang", "question_ko", "question_en", "intent_ko", "intent_en",
                                                "follow_up_ko", "follow_up_en", "translated", "translate_model", "fluency_notes")}
               for d in translate_docs.invoke(docs)]
    elif a.cmd == "ingest":
        out = ingest_agent.invoke({"roles": a.roles})
    else:
        out = sync_agent.invoke({"roles": a.roles, "dry_run": a.dry_run})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
