"""Question set composition (PRD 4.6 QST-01~04, RSQ-01~07; TRD §6).

기본 질문 8개 (2026-11-09 사용자 지시, 이전 10개):
  인성 2 → 이력서 기반 3 → 직무 이해도 2 → 기업 1
  - 인성: questionbank/regularDB/character_assessment/*.md → 인성 다듬기 에이전트(직무·이력서에 맞게 상황만 바꿈)
  - 이력서 기반: 확인 완료 이력서(OCR) 항목 + 질문 은행 참고 문항 → 합치는 생성 에이전트
  - 직무 이해도: 질문 은행 문항 + 공고 + 이력서 키워드 세 가지를 섞어 다시 씀
  - 기업: 질문 은행 문항을 회사 맞춤으로(contextualize)
"""
import os
import random
import re
import threading

from . import config, db, llm, prompts

EVAL_ITEMS = {
    "personality": ["logic", "relevance", "delivery", "fit"],
    "company": ["logic", "relevance", "delivery", "fit"],
    "job": ["expertise", "logic", "relevance", "delivery"],
    "resume": ["expertise", "logic", "relevance", "delivery"],
}
PLAN = [("personality", 2), ("resume", 3), ("job", 2), ("company", 1)]
RESUME_N = dict(PLAN)["resume"]
BASE_TOTAL = sum(n for _, n in PLAN)

AI_TERMS = ["ai", "ml", "머신러닝", "딥러닝", "llm", "rag", "nlp", "자연어", "비전", "vision", "yolo", "cnn", "transformer",
            "pytorch", "tensorflow", "huggingface", "모델", "추천", "데이터", "python", "파이썬", "mlops", "학습", "분류", "예측",
            "langchain", "fine-tuning", "파인튜닝", "임베딩", "agent", "에이전트", "자동화", "ax"]

# RSQ-06 금지 유형 차단 목록
BLOCKLIST = re.compile(r"(나이|몇 살|연령|공백기|결혼|자녀|가족|부모|종교|출신|고향|성별|병역|군대|키와|외모|"
                       r"학벌|명문|서열|(?<!우선)순위|어느 학교|학교 이름|대학 이름|회사 규모|대기업|중소기업이라)")
TEMPLATE = "이력서에 적은 '{label}'에서 맡은 역할과 가장 어려웠던 문제를 어떻게 해결했는지 설명해 주세요."
TEMPLATE_2 = "이력서에 적은 '{label}'에서 사용한 기술을 선택한 이유와 그 결과를 설명해 주세요."
TEMPLATE_3 = "이력서에 적은 '{label}'의 결과가 좋았는지 어떻게 확인했는지 설명해 주세요."
TEMPLATES = (TEMPLATE, TEMPLATE_2, TEMPLATE_3)
ANGLES = ("역할과 문제 해결 과정", "기술 선택 이유와 결과", "성과를 측정·검증한 방법")


# ---------- QST-01: bank selection (least used first, dedupe by dup_group) ----------
def pick_bank(category, n, exclude_groups=()):
    rows = db.all_("SELECT * FROM question_bank WHERE category=%s ORDER BY use_count, random()", category)
    picked, groups = [], set(exclude_groups)
    for r in rows:
        g = r["dup_group"] or f"id{r['id']}"
        if g in groups:
            continue
        picked.append(r)
        groups.add(g)
        if len(picked) == n:
            break
    return picked


# ---------- QST-04: scope check ----------
def _too_broad(q, seconds):
    asks = len(re.findall(r"[,·]|와 |과 |그리고|및 ", q))
    limit = 3 if seconds >= 90 else 2
    return asks > limit or len(q) > (160 if seconds >= 90 else 110)


SCOPE_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}


def fit_scope(q, seconds, purpose):
    if not _too_broad(q, seconds):
        return q, False
    try:
        out = llm.chat_json(
            "너는 면접 질문 편집자다. 평가 목적은 유지하되, 제한 시간 안에 답할 수 있도록 요구 과제를 1~2개로 줄인 한국어 질문 한 문장을 만든다.",
            f"답변 시간: {seconds}초\n평가 목적: {purpose}\n원래 질문: {q}", SCOPE_SCHEMA, timeout=60)
        nq = out["question"].strip()
        if nq and not _too_broad(nq, seconds):
            return nq, True
    except llm.LLMError:
        pass
    first = re.split(r"(?<=[?요])\s", q)[0]  # fallback: keep the first ask only
    return first, True


# ---------- 회사 맞춤 출제 (v0.6, 프롬프트 = backend/app/prompts/question_generator.md) ----------
CTX_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"},
              "company_facts": {"type": "array", "items": {"type": "string"}}}, "required": ["question", "company_facts"]}


def contextualize(q, job, intent=""):
    """질문 은행 문항을 {회사}가 직접 출제한 것처럼 다시 쓴다. 회사 정보에 없는 사실·차단 목록·과도한 길이면 원문 유지."""
    info = " ".join([job.get("company_intro") or "", job.get("duties") or "", job.get("requirements") or ""])
    system = prompts.load("generate/question_generator", company_name=job.get("company_name") or "우리 회사", role_name=job.get("role_name") or "",
                          company_intro=(job.get("company_intro") or "")[:400], duties=(job.get("duties") or "")[:400],
                          requirements=(job.get("requirements") or "")[:300], intent=intent or "원래 질문과 같음", seconds=config.QUESTION_SECONDS)
    try:
        out = llm.chat_json(system, f"기본 질문: {q}", CTX_SCHEMA, timeout=60, temperature=0.4)
    except llm.LLMError:
        return q
    nq = (out.get("question") or "").strip()
    squash = re.sub(r"\s+", "", info)
    facts_ok = all(re.sub(r"\s+", "", f) in squash for f in out.get("company_facts", []) if f and f.strip())
    if not (10 <= len(nq) <= 160) or not one_sentence(nq) or BLOCKLIST.search(nq) or not facts_ok:
        return q
    return nq


def _info_squash(job, snapshot):
    info = " ".join([job.get("company_intro") or "", job.get("duties") or "", job.get("requirements") or "",
                     job.get("role_name") or "", job.get("company_name") or ""])
    return re.sub(r"\s+", "", (info + " " + _snapshot_text(snapshot or {})).lower())


def _tokens_ok(question, base, job, snapshot, used_terms=()):
    """다시 쓴 질문의 영문·숫자 토큰과 used_terms가 (원문 문항 + 공고 + 이력서) 안에 있어야 한다(지어내기 차단)."""
    allowed = _info_squash(job, snapshot) + re.sub(r"\s+", "", (base or "").lower())
    toks = set(re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|\d+(?:\.\d+)?%?", question)) | {t for t in used_terms if t}
    return all(re.sub(r"\s+", "", t.lower().strip(".")) in allowed for t in toks)


SENT_END = re.compile(r"(?:[다요까]|니까)[.?!]\s*(?=\S)")


def one_sentence(q):
    """질문은 한 문장('…설명해 주세요.' 뒤에 서술문이 또 붙으면 실패)."""
    return not SENT_END.search(q.strip())


def resume_keywords(snapshot, limit=12):
    """확인 완료 이력서에서 프로젝트·경력 이름·기술·보유 기술 키워드(중복 제거)."""
    out, seen = [], set()
    for sec, key in (("projects", "name"), ("career", "company")):
        for it in (snapshot or {}).get(sec, []):
            for t in [it.get(key, ""), it.get("role", "")] + list(it.get("tech", []) or []):
                t = (t or "").strip()
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    out.append(t)
    for t in (snapshot or {}).get("skills", []):
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return [t for t in out if not BLOCKLIST.search(t)][:limit]


# ---------- 인성 질문 은행 (questionbank/regularDB/character_assessment/*.md, 2026-11-09 생성 · 2026-11-10 원본은 questionbank/ 바로 밑 확정) ----------
CHAR_DIR = os.path.join(config.QUESTIONBANK_DIR, "regularDB", "character_assessment")
_CHAR_FIELDS = {"질문": "text", "의도": "intent", "좋은 답변 신호": "signals", "파고들 지점": "probes",
                "맞춤 범위": "scope", "바꾸면 안 되는 것": "keep"}


def load_character_bank(path=CHAR_DIR):
    """md 문항 목록. `## 제목` + `<!--meta: k=v; ...-->` + `- 항목: 값`. 폴더가 없으면 빈 목록(→ DB 인성 문항)."""
    items = []
    if not os.path.isdir(path):
        return items
    for name in sorted(os.listdir(path)):
        if not name.endswith(".md") or name.lower() == "readme.md":
            continue
        with open(os.path.join(path, name), encoding="utf-8") as f:
            text = f.read()
        for block in re.split(r"^## ", text, flags=re.M)[1:]:
            meta_m = re.search(r"<!--\s*meta:(.*?)-->", block, flags=re.S)
            meta = dict(kv.split("=", 1) for kv in (x.strip() for x in (meta_m.group(1) if meta_m else "").split(";")) if "=" in kv)
            it = {"char_id": meta.get("id", "").strip(), "competency": meta.get("competency", "").strip(),
                  "dup_group": meta.get("dup_group", "").strip(), "level": meta.get("level", "").strip(), "file": name}
            for label, key in _CHAR_FIELDS.items():
                m = re.search(rf"^- {re.escape(label)}:\s*(.+)$", block, flags=re.M)
                it[key] = m.group(1).strip() if m else ""
            if it["char_id"] and it["text"] and not BLOCKLIST.search(it["text"]):
                items.append(it)
    return items


def pick_character(n, used=(), snapshot=None):
    """인성 문항 n개: 같은 dup_group·같은 역량은 한 번만, 경력 유무로 level 필터. 은행이 비면 DB 인성 문항."""
    level = "경력" if (snapshot or {}).get("career") else "신입"
    pool = [it for it in load_character_bank() if not it["level"] or level in it["level"].split("|")]
    random.shuffle(pool)
    picked, groups, comps = [], set(used), set()
    for it in pool:
        g = it["dup_group"] or it["char_id"]
        if g in groups or (it["competency"] and it["competency"] in comps):
            continue
        groups.add(g)
        comps.add(it["competency"])
        picked.append({"id": None, "text": it["text"], "subcategory": it["competency"], "dup_group": g, **it})
        if len(picked) == n:
            return picked
    return picked + pick_bank("personality", n - len(picked), groups)


CHAR_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"},
               "used_terms": {"type": "array", "items": {"type": "string"}}}, "required": ["question", "used_terms"]}


def contextualize_character(item, job, snapshot):
    """인성 다듬기 에이전트: 역량·의도는 그대로, 상황 배경만 직무·이력서에 맞게. 규칙 위반이면 원문."""
    q = item["text"]
    system = prompts.load("generate/character_contextualizer", company_name=job.get("company_name") or "우리 회사",
                          role_name=job.get("role_name") or "", duties=(job.get("duties") or "")[:400],
                          competency=item.get("competency") or item.get("subcategory") or "", intent=item.get("intent") or "원래 질문과 같음",
                          scope=item.get("scope") or "상황 배경만", keep=item.get("keep") or "평가 역량",
                          resume_keywords=", ".join(resume_keywords(snapshot)) or "(없음)", seconds=config.QUESTION_SECONDS)
    try:
        out = llm.chat_json(system, f"원문 질문: {q}", CHAR_SCHEMA, timeout=60, temperature=0.4)
    except llm.LLMError:
        return q
    nq = (out.get("question") or "").strip()
    role = (job.get("role_name") or "").strip()
    if role and re.search(re.escape(role) + r"\s*(?:이)?(?:로|으로|으로서|로서)\s*(?:근무|일하|일할|일했|재직|활동)", nq):
        return q                                   # 지원 직무를 지원자의 경력처럼 전제하면 원문
    base = " ".join([q, item.get("scope") or "", item.get("intent") or ""])   # md의 맞춤 범위 예시는 허용
    if not (10 <= len(nq) <= 160) or not one_sentence(nq) or BLOCKLIST.search(nq) or not _tokens_ok(nq, base, job, snapshot, out.get("used_terms", [])):
        return q
    return nq


# ---------- 직무 이해도: 은행 문항 + 공고 + 이력서 (2026-11-09) ----------
def contextualize_job(q, job, snapshot, intent=""):
    """세 재료를 섞어 다시 쓴다. 공고·이력서·원문에 없는 토큰이 나오거나 규칙 위반이면 원문."""
    system = prompts.load("generate/job_question_mixer", company_name=job.get("company_name") or "우리 회사", role_name=job.get("role_name") or "",
                          company_intro=(job.get("company_intro") or "")[:400], duties=(job.get("duties") or "")[:400],
                          requirements=(job.get("requirements") or "")[:300], intent=intent or "원래 질문과 같음",
                          resume_keywords=", ".join(resume_keywords(snapshot)) or "(없음)", seconds=config.QUESTION_SECONDS)
    try:
        out = llm.chat_json(system, f"질문 은행 문항: {q}", CHAR_SCHEMA, timeout=60, temperature=0.4)
    except llm.LLMError:
        return q
    nq = (out.get("question") or "").strip()
    if not (10 <= len(nq) <= 160) or not one_sentence(nq) or BLOCKLIST.search(nq) or not _tokens_ok(nq, q, job, snapshot, out.get("used_terms", [])):
        return q
    return nq


# ---------- RSQ: resume-based questions ----------
def _year(period):
    ys = re.findall(r"(19|20)\d{2}", period or "")
    full = re.findall(r"(?:19|20)\d{2}", period or "")
    return max(int(y) for y in full) if full else 0


def resume_candidates(snapshot):
    """RSQ-01: career + projects, AI-related first, then most recent."""
    cands = []
    for sec, label_key in (("career", "company"), ("projects", "name")):
        for i, it in enumerate(snapshot.get(sec, [])):
            label = (it.get(label_key) or "").strip() or ("경력" if sec == "career" else "프로젝트")
            blob = " ".join([label, it.get("role", ""), it.get("detail", ""), " ".join(it.get("tech", []))]).lower()
            score = sum(1 for t in AI_TERMS if t in blob)
            cands.append({"section": sec, "index": i, "label": label, "item": it, "ai": score, "year": _year(it.get("period"))})
    cands.sort(key=lambda c: (-c["ai"], -c["year"]))
    if not cands:  # RSQ-04: 학력으로 대체
        for i, it in enumerate(snapshot.get("education", [])):
            if (it.get("major") or it.get("detail")):
                label = " ".join(x for x in [it.get("school", ""), it.get("major", "")] if x).strip() or "학력"
                cands.append({"section": "education", "index": i, "label": label, "item": it, "ai": 0, "year": _year(it.get("period"))})
    return cands


def _snapshot_text(snapshot):
    parts = []
    for sec in ("education", "career", "projects", "certificates"):
        for it in snapshot.get(sec, []):
            parts.extend(str(v) for k, v in it.items() if k not in ("page", "needs_check", "origin") and not isinstance(v, list))
            parts.extend(it.get("tech", []) or [])
    parts.extend(snapshot.get("skills", []))
    return " ".join(parts).lower()


def grounded(question, cited, snapshot):
    """RSQ-03: latin tokens / numbers / cited terms must exist in the snapshot."""
    text = _snapshot_text(snapshot)
    squash = re.sub(r"\s+", "", text)
    tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|\d+(?:\.\d+)?%?", question))
    tokens |= {c for c in cited if c}
    for t in tokens:
        tl = t.lower().strip(".")
        if tl and tl not in text and re.sub(r"\s+", "", tl) not in squash:
            return False
    return True


RSQ_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}, "cited_terms": {"type": "array", "items": {"type": "string"}}},
              "required": ["question", "cited_terms"]}
RSQ_SYSTEM = """너는 AI/AX 엔지니어 1차 면접 질문을 만드는 면접관이다.
규칙:
1. 주어진 이력서 항목 하나에 근거해 한국어 질문 한 문장만 만든다.
2. 항목에 실제로 적힌 역할·기술·성과를 짚어 묻는다. 항목에 없는 프로젝트명·기술명·수치를 만들어 넣지 않는다.
3. 90초 안에 답할 수 있게 요구 과제는 1~2개로 제한한다.
4. 학교·회사의 이름·서열·규모, 나이·공백기·가족 등 인적 사정은 묻지 않는다. 수행한 일과 역량만 묻는다.
5. cited_terms에는 질문에 쓴 고유 명칭(프로젝트명·기술명·수치)을 이력서 표기 그대로 넣는다."""


MERGE_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}, "cited_terms": {"type": "array", "items": {"type": "string"}},
                "used_bank": {"type": "boolean"}}, "required": ["question", "cited_terms", "used_bank"]}


def resume_pairs(cands, n=RESUME_N):
    """이력서 질문 n개의 (후보 항목, 관점) 짝. 항목이 모자라면 같은 항목을 다른 관점으로 묻는다."""
    if not cands:
        return []
    pairs = [(c, 0) for c in cands[:n]]
    angle = 1
    while len(pairs) < n:
        pairs.append((cands[(len(pairs)) % len(cands)], angle % len(ANGLES)))
        angle += 1
    return pairs


def make_resume_question(cand, angle, snapshot, bank=None, role_name=""):
    """이력서 항목(근거) + 질문 은행 참고 문항(관점)을 합치는 생성 에이전트(2026-11-09).
    근거 검사(RSQ-03)·차단 목록·1회 재생성 → 실패하면 템플릿."""
    it = cand["item"]
    desc = "\n".join(f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in it.items()
                     if k not in ("page", "needs_check", "origin", "source") and v)
    ref = {"section": cand["section"], "index": cand["index"], "label": cand["label"], "quote": (it.get("detail") or it.get("source") or "")[:160]}
    if bank:
        ref["bank_doc"] = bank.get("doc_id") or (f"db:{bank['id']}" if bank.get("id") else "")
    bank_txt = f"\n질문 은행 참고 문항: {bank['text']}\n참고 문항의 평가 의도: {bank.get('subcategory') or '-'}" if bank else "\n질문 은행 참고 문항: (없음)"
    system = prompts.load("generate/resume_question_merger", role_name=role_name or "AI/AX 엔지니어")
    for _ in range(2):  # 1회 재생성 (RSQ-03)
        try:
            out = llm.chat_json(system, f"질문 관점: {ANGLES[angle]}{bank_txt}\n이력서 항목:\n{desc}", MERGE_SCHEMA, timeout=90, temperature=0.4)
        except llm.LLMError:
            break
        q = out["question"].strip()
        if q and not BLOCKLIST.search(q) and grounded(q, out.get("cited_terms", []), snapshot):
            q, _ = fit_scope(q, config.QUESTION_SECONDS, "직무 전문성·구체성")
            if not BLOCKLIST.search(q):
                if bank:
                    ref["bank_used"] = bool(out.get("used_bank"))
                return {"text": q, "resume_ref": ref, "gen_method": "llm"}
    return {"text": TEMPLATES[angle].format(label=cand["label"]), "resume_ref": ref, "gen_method": "template"}


def resume_questions(snapshot, use_llm=True, banks=None, role_name=""):
    cands = resume_candidates(snapshot)
    if not cands:  # RSQ-04: 직무 DB로 대체
        return [{"category": "job_fallback", "reason": "이력서에 경력·프로젝트·학력 내용이 없어 직무 질문으로 대체"}] * RESUME_N
    out = []
    for i, (cand, angle) in enumerate(resume_pairs(cands)):
        bank = (banks or [None] * RESUME_N)[i] if banks and i < len(banks) else None
        if use_llm:
            out.append(make_resume_question(cand, angle, snapshot, bank, role_name))
        else:
            ref = {"section": cand["section"], "index": cand["index"], "label": cand["label"], "quote": (cand["item"].get("detail") or cand["item"].get("source") or "")[:160]}
            out.append({"text": TEMPLATES[angle].format(label=cand["label"]), "resume_ref": ref, "gen_method": "template"})
    return out


# ---------- full set ----------
def build_set(application, use_llm=True):
    job = db.one("""SELECT j.*, c.name company_name, r.name role_name FROM jobs j JOIN companies c ON c.id=j.company_id
                    JOIN job_roles r ON r.code=j.role_code WHERE j.id=%s""", application["job_id"])
    snapshot = application["resume_snapshot"] or {}
    result, used_groups = [], set()
    rq = resume_questions(snapshot, use_llm, role_name=job.get("role_name") or "")
    for cat, n in PLAN:
        if cat == "personality":     # 그래프가 실패했을 때의 대체 경로: 인성 md 은행(없으면 DB) → 다듬기
            for b in pick_character(n, used_groups, snapshot):
                used_groups.add(b["dup_group"] or f"id{b['id']}")
                text = contextualize_character(b, job, snapshot) if use_llm else b["text"]
                result.append({"category": cat, "eval_items": EVAL_ITEMS[cat], "text": text, "bank_id": b.get("id"),
                               "gen_method": ("character:" + b["char_id"]) if b.get("char_id") else "db"})
            continue
        if cat == "resume":
            fallback = [r for r in rq if r.get("category") == "job_fallback"]
            if fallback:
                for b in pick_bank("job", len(fallback), used_groups):
                    used_groups.add(b["dup_group"] or f"id{b['id']}")
                    result.append({"category": "resume", "eval_items": EVAL_ITEMS["job"], "text": b["text"], "bank_id": b["id"],
                                   "gen_method": "fallback_job", "resume_ref": {"fallback_reason": fallback[0]["reason"]}})
            for r in rq:
                if r.get("category") != "job_fallback":
                    result.append({"category": "resume", "eval_items": EVAL_ITEMS["resume"], "bank_id": None, **r})
            continue
        for b in pick_bank(cat, n, used_groups):
            used_groups.add(b["dup_group"] or f"id{b['id']}")
            text = b["text"]
            if use_llm and cat == "company":
                text = contextualize(text, job, b.get("subcategory") or "")
            elif use_llm and cat == "job":              # 2026-11-09: 은행 + 공고 + 이력서 세 가지를 섞음
                text = contextualize_job(text, job, snapshot, b.get("subcategory") or "")
            if use_llm:
                text, _ = fit_scope(text, config.QUESTION_SECONDS, ", ".join(EVAL_ITEMS[cat]))
            result.append({"category": cat, "eval_items": EVAL_ITEMS[cat], "text": text, "bank_id": b["id"], "gen_method": "db"})
    return result


def pregenerate(application_id):
    """RSQ-05: run during environment check; interview start uses this if ready."""
    db.run("UPDATE applications SET pregen_status='running' WHERE id=%s", application_id)
    try:
        app = db.one("SELECT * FROM applications WHERE id=%s", application_id)
        try:   # 그래프 1-A 질문 생성(LangGraph, ADLC v0.5 6.1). 그래프가 실패하면 기존 함수 경로
            from .agents import question_graph
            qs, _info = question_graph.generate_set(app, use_llm=True)
        except Exception:
            qs = build_set(app, use_llm=True)
        db.run("UPDATE applications SET pregen_status='ready', pregen_questions=%s WHERE id=%s", db.J(qs), application_id)
        from . import speech   # v0.9 INT-01: 환경 점검 중에 질문 음성(Qwen3-TTS)도 미리 만든다(TTS가 실시간보다 느림)
        speech.prewarm([q["text"] for q in qs])
    except Exception:
        db.run("UPDATE applications SET pregen_status='failed' WHERE id=%s", application_id)


def start_pregen(application_id):
    threading.Thread(target=pregenerate, args=(application_id,), daemon=True).start()
