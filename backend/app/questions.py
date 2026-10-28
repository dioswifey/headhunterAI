"""Question set composition (PRD 4.6 QST-01~04, RSQ-01~07; TRD §6).

Order of the 10 base questions: 인성 2 → 이력서 2 → 직무 4 → 기업 2.
"""
import re
import threading

from . import config, db, llm

EVAL_ITEMS = {
    "personality": ["logic", "relevance", "delivery", "fit"],
    "company": ["logic", "relevance", "delivery", "fit"],
    "job": ["expertise", "logic", "relevance", "delivery"],
    "resume": ["expertise", "logic", "relevance", "delivery"],
}
PLAN = [("personality", 2), ("resume", 2), ("job", 4), ("company", 2)]

AI_TERMS = ["ai", "ml", "머신러닝", "딥러닝", "llm", "rag", "nlp", "자연어", "비전", "vision", "yolo", "cnn", "transformer",
            "pytorch", "tensorflow", "huggingface", "모델", "추천", "데이터", "python", "파이썬", "mlops", "학습", "분류", "예측",
            "langchain", "fine-tuning", "파인튜닝", "임베딩", "agent", "에이전트", "자동화", "ax"]

# RSQ-06 금지 유형 차단 목록
BLOCKLIST = re.compile(r"(나이|몇 살|연령|공백기|결혼|자녀|가족|부모|종교|출신|고향|성별|병역|군대|키와|외모|"
                       r"학벌|명문|서열|순위|어느 학교|학교 이름|대학 이름|회사 규모|대기업|중소기업이라)")
TEMPLATE = "이력서에 적은 '{label}'에서 맡은 역할과 가장 어려웠던 문제를 어떻게 해결했는지 설명해 주세요."
TEMPLATE_2 = "이력서에 적은 '{label}'에서 사용한 기술을 선택한 이유와 그 결과를 설명해 주세요."


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


# ---------- company context (기업 관련 문항 맥락화) ----------
CTX_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}


def contextualize(q, job):
    try:
        out = llm.chat_json(
            "너는 면접 질문 편집자다. 원래 질문의 의도와 범위는 그대로 두고, 회사 소개·주요 업무에 맞게 표현만 다듬은 한국어 질문 한 문장을 만든다. 새로운 요구 과제를 추가하지 않는다.",
            f"회사 소개: {job['company_intro'][:400]}\n주요 업무: {job['duties'][:400]}\n원래 질문: {q}", CTX_SCHEMA, timeout=60)
        nq = out["question"].strip()
        return nq if 10 <= len(nq) <= 160 else q
    except llm.LLMError:
        return q


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


def make_resume_question(cand, angle, snapshot):
    it = cand["item"]
    desc = "\n".join(f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in it.items()
                     if k not in ("page", "needs_check", "origin", "source") and v)
    angle_txt = "역할과 문제 해결 과정" if angle == 0 else "기술 선택 이유와 결과"
    ref = {"section": cand["section"], "index": cand["index"], "label": cand["label"], "quote": (it.get("detail") or it.get("source") or "")[:160]}
    for _ in range(2):  # 1회 재생성 (RSQ-03)
        try:
            out = llm.chat_json(RSQ_SYSTEM, f"질문 관점: {angle_txt}\n이력서 항목:\n{desc}", RSQ_SCHEMA, timeout=90, temperature=0.4)
        except llm.LLMError:
            break
        q = out["question"].strip()
        if q and not BLOCKLIST.search(q) and grounded(q, out.get("cited_terms", []), snapshot):
            q, _ = fit_scope(q, config.QUESTION_SECONDS, "직무 전문성·구체성")
            if not BLOCKLIST.search(q):
                return {"text": q, "resume_ref": ref, "gen_method": "llm"}
    tpl = TEMPLATE if angle == 0 else TEMPLATE_2
    return {"text": tpl.format(label=cand["label"]), "resume_ref": ref, "gen_method": "template"}


def resume_questions(snapshot, use_llm=True):
    cands = resume_candidates(snapshot)
    if not cands:  # RSQ-04: 직무 DB로 대체
        return [{"category": "job_fallback", "reason": "이력서에 경력·프로젝트·학력 내용이 없어 직무 질문으로 대체"}] * 2
    picks = [(cands[0], 0), (cands[1], 0)] if len(cands) >= 2 else [(cands[0], 0), (cands[0], 1)]
    out = []
    for cand, angle in picks:
        if use_llm:
            out.append(make_resume_question(cand, angle, snapshot))
        else:
            ref = {"section": cand["section"], "index": cand["index"], "label": cand["label"], "quote": (cand["item"].get("detail") or cand["item"].get("source") or "")[:160]}
            out.append({"text": (TEMPLATE if angle == 0 else TEMPLATE_2).format(label=cand["label"]), "resume_ref": ref, "gen_method": "template"})
    return out


# ---------- full set ----------
def build_set(application, use_llm=True):
    job = db.one("SELECT * FROM jobs WHERE id=%s", application["job_id"])
    snapshot = application["resume_snapshot"] or {}
    result, used_groups = [], set()
    rq = resume_questions(snapshot, use_llm)
    for cat, n in PLAN:
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
                text = contextualize(text, job)
            if use_llm:
                text, _ = fit_scope(text, config.QUESTION_SECONDS, ", ".join(EVAL_ITEMS[cat]))
            result.append({"category": cat, "eval_items": EVAL_ITEMS[cat], "text": text, "bank_id": b["id"], "gen_method": "db"})
    return result


def pregenerate(application_id):
    """RSQ-05: run during environment check; interview start uses this if ready."""
    db.run("UPDATE applications SET pregen_status='running' WHERE id=%s", application_id)
    try:
        app = db.one("SELECT * FROM applications WHERE id=%s", application_id)
        qs = build_set(app, use_llm=True)
        db.run("UPDATE applications SET pregen_status='ready', pregen_questions=%s WHERE id=%s", db.J(qs), application_id)
    except Exception:
        db.run("UPDATE applications SET pregen_status='failed' WHERE id=%s", application_id)


def start_pregen(application_id):
    threading.Thread(target=pregenerate, args=(application_id,), daemon=True).start()
