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
from .agents import question_rules as QR   # 정규식만 있는 모듈(무거운 의존 없음) — 문장 규칙 공용(2026-11-20)

EVAL_ITEMS = {
    "personality": ["logic", "relevance", "delivery", "fit"],
    "company": ["logic", "relevance", "delivery", "fit"],
    "job": ["expertise", "logic", "relevance", "delivery"],
    "resume": ["expertise", "logic", "relevance", "delivery"],
}
# 2026-11-19 사용자 지시: 이력서 3 → 2, 그만큼 직무를 3으로. 본질문 8개는 그대로.
PLAN = [("personality", 2), ("resume", 2), ("job", 3), ("company", 1)]
RESUME_N = dict(PLAN)["resume"]
BASE_TOTAL = sum(n for _, n in PLAN)

AI_TERMS = ["ai", "ml", "머신러닝", "딥러닝", "llm", "rag", "nlp", "자연어", "비전", "vision", "yolo", "cnn", "transformer",
            "pytorch", "tensorflow", "huggingface", "모델", "추천", "데이터", "python", "파이썬", "mlops", "학습", "분류", "예측",
            "langchain", "fine-tuning", "파인튜닝", "임베딩", "agent", "에이전트", "자동화", "ax"]

# RSQ-06 금지 유형 차단 목록
BLOCKLIST = re.compile(r"(나이|몇 살|연령|공백기|결혼|자녀|가족|부모|종교|출신|고향|성별|병역|군대|키와|외모|"
                       r"학벌|명문|서열|(?<!우선)순위|어느 학교|학교 이름|대학 이름|회사 규모|대기업|중소기업이라)")
# 2026-11-20 문구 교체(초안 — 사용자 확정 대기). 옛 문구는 셋 다 질문 규칙(QST-20)을 어겼다:
#   "맡은 역할과 가장 어려웠던 문제를 어떻게 해결했는지"(두 개 묻기) · "선택한 이유와 그 결과를"(개념 2개 + 지시어)
#   · 라벨을 따옴표로 감쌈(음성으로 읽으면 들리지 않는다). 모델 생성이 실패하면 이 문장이 그대로 나가므로(실측 29%) 한 가지만 묻는다.
# 같은 날 사용자 응시 피드백 F3: 이력서 프로젝트를 그대로 되묻지 말고 **지원 회사 맥락의 가정형**으로 잇는다
#   ("이 검사를 [회사]에서 진행한다고 가정할 때 어떤 방식을 택하시겠습니까? 비슷한 경험이 있다면 …답해 주셔도 좋습니다").
#   회사 이름 뒤에는 반드시 가정형(~한다면·~가정할 때) — 과거형과 이으면 F9(그 회사 경력 전제) 위반이다.
# **2026-11-23 F11 정정**: 가정형이면 안전하다고 본 것이 빈틈이었다 — "{회사}에서 ~를 **다시** 맡는다면"은
#   가정형이어도 **그 회사에서 예전에 해 봤다**는 뜻이 된다(사용자: "여기서 일해 본 적이 없는데 말이 됨?").
#   그래서 템플릿에서 '다시'를 걷어내고, 이전 회사명이 있으면 그 이름을 앞에 두어 어디서 한 일인지 밝힌다.
#   `TEMPLATE_2`는 원래 '다시'가 없어 그대로 둔다.
TEMPLATE = ("{label}에서 겪은 가장 어려운 문제를 {company}에서 같은 문제로 마주한다면 어떤 방식으로 해결하시겠습니까? "
            "비슷한 경험이 있다면 그 경험을 바탕으로 답해 주셔도 좋습니다.")
TEMPLATE_2 = "{label}에서 쓴 기술을 {company}의 비슷한 업무에 적용한다고 가정할 때, 어떤 기술 스택을 택하시겠습니까? 비슷한 경험이 있다면 그 경험을 바탕으로 답해 주셔도 좋습니다."
TEMPLATE_3 = ("{label}의 성과를 {company}에서 같은 기준으로 확인해야 한다면, 어떤 지표로 확인하시겠습니까? "
              "비슷한 경험이 있다면 그 경험을 바탕으로 답해 주셔도 좋습니다.")
# 이전 회사명을 아는 경우(경력 항목 또는 경력이 하나뿐인 지원자)에는 어디서 한 일인지 밝혀 F11을 피한다.
TEMPLATE_PREV = ("{prev}에서 하신 {label}을, {company}에서 같은 문제로 마주한다면 어떤 방식으로 해결하시겠습니까? "
                 "비슷한 경험이 있다면 그 경험을 바탕으로 답해 주셔도 좋습니다.")
TEMPLATES = (TEMPLATE, TEMPLATE_2, TEMPLATE_3)
ANGLES = ("가장 어려웠던 문제를 이 직무에서 같은 문제로 마주한다면 어떻게 해결할지", "쓴 기술을 회사의 비슷한 업무에 어떻게 적용할지",
          "성과를 이 직무에서 같은 기준으로 확인한다면 어떤 지표로 볼지")


def template_text(angle, label, company="", prev_company=""):
    """이력서 질문 템플릿(모델 생성이 실패했을 때). 회사 이름이 없으면 '지원하신 회사'.

    `prev_company`(2026-11-23 F11)가 있으면 **어디서 한 일인지 밝히는** 문장을 쓴다
    ("다온팩토리솔루션에서 하신 소비기한 인쇄 불량 검사를, {회사}에서 같은 문제로 마주한다면 …").
    항목 라벨이 곧 그 회사 이름인 경우(경력 항목)에는 같은 이름이 두 번 나오므로 쓰지 않는다.
    """
    comp = (company or "").strip() or "지원하신 회사"
    prev = (prev_company or "").strip()
    if prev and prev != label and angle == 0:
        return TEMPLATE_PREV.format(prev=prev, label=label, company=comp)
    return TEMPLATES[angle].format(label=label, company=comp)


# 라벨 없는 대체 템플릿(2026-11-20 지원 20 점검, F4): 이력서 항목이 2개뿐인데 둘이 같은 프로젝트를 다루면(경력 항목에 그 프로젝트가
# 적힌 경우) 라벨 템플릿도 같은 주제가 된다. 앞 질문과 주제가 겹칠 때만 이 문장을 쓴다(문구 초안 — 사용자 확정 대기).
# 2026-11-23 F11: "그 일을 {회사}에서 **다시** 한다면"도 그 회사 근무 이력을 전제한다 → '다시'를 걷어냈다.
TEMPLATE_GENERIC = "지금까지의 경험 중 {company}의 이 직무와 가장 가까운 일을 하나 골라, 그 일을 이 직무에서 맡게 된다면 무엇을 다르게 하시겠습니까?"


def generic_template_text(company=""):
    return TEMPLATE_GENERIC.format(company=(company or "").strip() or "지원하신 회사")


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
            "너는 면접 질문 편집자다. 평가 목적은 유지하되, 제한 시간 안에 답할 수 있도록 요구 과제를 1개로 줄인 한국어 질문 한 문장을 만든다. "
            "의문사(어떻게·무엇·어떤·왜)는 한 번만 쓰고, 예시·괄호·따옴표를 붙이지 않는다.",   # 2026-11-20: 1~2개 → 1개(QST-20 ①)
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
    user = f"기본 질문: {q}"
    squash = re.sub(r"\s+", "", info)
    # 2026-11-12 RAG 평가(M-32): 모델이 밝힌 company_facts만 대조하면 질문 속 지어낸 수치('정확도 85%')가 통과한다
    # → 직무 질문과 같은 토큰 검사(영문·숫자 토큰은 은행 원문·공고·회사 정보에 있어야 함)
    # 2026-11-13 fix2: 토큰 검사에 걸리면 바로 원문으로 가지 않고, 걸린 토큰을 알려 주고 1회만 다시 만든다
    #   (fix1 재측정에서 기업 질문 9개 중 8개가 원문 '우리 회사의 서비스나 사업…'으로 돌아가 루브릭 3.82→2.84)
    for attempt in range(2):
        try:
            out = llm.chat_json(system, user, CTX_SCHEMA, timeout=60, temperature=0.4)
        except llm.LLMError:
            return q
        nq = (out.get("question") or "").strip()
        facts_ok = all(re.sub(r"\s+", "", f) in squash for f in out.get("company_facts", []) if f and f.strip())
        if not (10 <= len(nq) <= 160) or not one_sentence(nq) or BLOCKLIST.search(nq) or not facts_ok:
            return q
        bad = _bad_tokens(nq, q, job, {})
        if not bad:
            return nq
        if attempt == 0:
            user = (f"기본 질문: {q}\n\n[다시 쓰기] 방금 만든 질문: {nq}\n"
                    f"회사 정보·기본 질문에 없는 숫자·영문 토큰이 들어 있다: {', '.join(bad[:8])}\n"
                    "회사 정보·기본 질문에 없는 숫자(비율·점수·건수 등)와 영문 용어는 쓰지 말고, 같은 평가 목적의 질문 한 문장으로 다시 써라.")
    return q


RAG_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"},
              "company_facts": {"type": "array", "items": {"type": "string"}},
              "doc_refs": {"type": "array", "items": {"type": "string"}}}, "required": ["question", "company_facts", "doc_refs"]}


# 면접 방식 자체를 묻는 질문(2026-11-13 사용자 결정 — 41번 문서 STAR·질문 설계 섹션이 근거로 들어올 때 대비).
# A15·7-B의 사내문서 근거 문항에만 쓴다(은행 원문에는 쓰지 않음).
# 강한 키워드 = 단독으로 차단. 약한 키워드(평가 기준·평가 항목·채점)는 채용 맥락 단어와 같은 문장에 있을 때만
# (데이터 직무 '모델 평가 기준' 같은 정상 질문 오탐 방지, 2026-11-13 조정).
INTERVIEW_META_STRONG = re.compile(r"(루브릭|STAR|질문\s*설계|면접\s*방식)")
INTERVIEW_META_WEAK = re.compile(r"(평가\s*기준|평가\s*항목|채점)")
HIRING_CONTEXT = re.compile(r"(면접|채용|지원자|합격|서류|전형)")


def asks_interview_method(text):
    """면접 방법·평가 기준·질문 설계를 묻는 문장이면 True. 질문(의문·요청형) 문장이 대상이라 키워드만 본다."""
    t = text or ""
    return bool(INTERVIEW_META_STRONG.search(t) or (INTERVIEW_META_WEAK.search(t) and HIRING_CONTEXT.search(t)))


def contextualize_rag(q, job, intent, context, refs, bodies, forbid_terms=()):
    """A15 (ADLC v0.7 6.13.6 ⑩, 2026-11-13): 공개판 사내문서 컨텍스트로 기업 문항을 다시 쓴다.
    반환 {text, company_facts, doc_refs} | None. None이면 호출 쪽이 기존 contextualize(공고 정보만)로 내려간다.
    ⑪ 재생성하지 않는다 — 규칙에 한 번 걸리면 None. 검사:
      길이 10~160 · 한 문장 · BLOCKLIST · 금지 용어 사전 · company_facts ⊂ (공고 + 주입 조각 본문) ·
      doc_refs가 비지 않고 실제 주입한 조각 ID · 영문·숫자 토큰이 (은행 원문 + 공고 + 조각 본문)에 있음.
    bodies = 조각 본문(출처 머리글 제외). 머리글의 조각 ID가 '허용 토큰'이 되지 않게 본문만 근거로 쓴다."""
    from .agents import internal_forbid
    if not forbid_terms:                       # 사전 없음 = 검사 불가 → 사내문서 경로 미사용(6.13.8)
        return None
    system = prompts.load("generate/company_question_rag", company_name=job.get("company_name") or "우리 회사",
                          role_name=job.get("role_name") or "", company_intro=(job.get("company_intro") or "")[:400],
                          duties=(job.get("duties") or "")[:400], requirements=(job.get("requirements") or "")[:300],
                          internal_context=context, intent=intent or "원래 질문과 같음", seconds=config.QUESTION_SECONDS)
    try:
        out = llm.chat_json(system, f"기본 질문: {q}", RAG_SCHEMA, timeout=90, temperature=0.4)
    except llm.LLMError:
        return None
    nq = (out.get("question") or "").strip()
    facts = [f.strip() for f in out.get("company_facts") or [] if f and f.strip()]
    drefs = [r.strip() for r in out.get("doc_refs") or [] if r and r.strip()]
    if not (10 <= len(nq) <= 160) or not one_sentence(nq) or BLOCKLIST.search(nq):
        return None
    if internal_forbid.find_terms(nq, forbid_terms) or any(internal_forbid.find_terms(f, forbid_terms) for f in facts):
        return None
    if internal_forbid.masked_hits(nq) or any(internal_forbid.masked_hits(f) for f in facts):      # 마스킹 표식([금액] 등)·가명(고객사A…)이 든 질문은 쓰지 않는다(2026-11-13 사용자 결정)
        return None
    if asks_interview_method(nq):            # 면접 방식·평가 기준을 묻는 질문은 쓰지 않는다(2026-11-13 사용자 결정)
        return None
    allowed = re.sub(r"\s+", "", " ".join([job.get("company_intro") or "", job.get("duties") or "",
                                            job.get("requirements") or ""] + list(bodies)))
    if not all(re.sub(r"\s+", "", f) in allowed for f in facts):
        return None
    if not drefs or not set(drefs) <= set(refs):
        return None
    if _bad_tokens(nq, " ".join([q] + list(bodies)), job, {}):
        return None
    return {"text": nq, "company_facts": facts, "doc_refs": drefs}


def _info_squash(job, snapshot):
    info = " ".join([job.get("company_intro") or "", job.get("duties") or "", job.get("requirements") or "",
                     job.get("role_name") or "", job.get("company_name") or ""])
    return re.sub(r"\s+", "", (info + " " + _snapshot_text(snapshot or {})).lower())


def _tokens_ok(question, base, job, snapshot, used_terms=()):
    """다시 쓴 질문의 영문·숫자 토큰과 used_terms가 (원문 문항 + 공고 + 이력서) 안에 있어야 한다(지어내기 차단)."""
    return not _bad_tokens(question, base, job, snapshot, used_terms)


def _bad_tokens(question, base, job, snapshot, used_terms=()):
    """_tokens_ok에서 걸린 토큰 목록(정렬). 비어 있으면 통과."""
    allowed = _info_squash(job, snapshot) + re.sub(r"\s+", "", (base or "").lower())
    toks = set(re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|\d+(?:[:/]\d+)+|\d+(?:\.\d+)?%?", question)) | {t for t in used_terms if t}
    return sorted(t for t in toks if re.sub(r"\s+", "", t.lower().strip(".")) not in allowed)


SENT_END = re.compile(r"(?:[다요까]|니까)[.?!]\s*(?=\S)")


def one_sentence(q):
    """질문은 한 문장('…설명해 주세요.' 뒤에 서술문이 또 붙으면 실패).
    2026-11-20: 사용자가 허용한 덧붙임(경험 안내 · 본질문 답변 형식 예시)은 떼고 센다(QR.core)."""
    return not SENT_END.search(QR.core(q))


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


# ---------- 직무 이해도 Dual RAG: Redis 은행 문항 + Chroma 사내문서 + 공고 + 이력서 (2026-11-16, ADLC 6.18.3) ----------
JOB_RAG_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"},
                  "used_terms": {"type": "array", "items": {"type": "string"}},
                  "company_facts": {"type": "array", "items": {"type": "string"}},
                  "doc_refs": {"type": "array", "items": {"type": "string"}}},
                  "required": ["question", "used_terms", "company_facts", "doc_refs"]}
# 지원자가 이미 한 일처럼 전제하는 과거형 높임 표현 — 사내문서 사실과 같은 문장에 있으면 '허위 경험 전제'로 본다
PAST_PREMISE = re.compile(r"(하셨던|하셨는|하신\s*경험|진행하신|진행하셨|담당하신|담당하셨|맡으셨|맡았던|참여하신|참여하셨|수행하신|수행하셨|경험하신|구축하신|구축하셨|개발하신|개발하셨)")


def _premise_violation(nq, facts, job, snapshot):
    """사내문서 사실(공고·이력서에 없는 문구)을 쓰면서 과거 경험을 전제하면 True. 지원 직무를 경력처럼 전제해도 True."""
    role = (job.get("role_name") or "").strip()
    if role and re.search(re.escape(role) + r"\s*(?:이)?(?:로|으로|으로서|로서)\s*(?:근무|일하|일했|재직|활동)", nq):
        return True
    base = _info_squash(job, snapshot)
    doc_only = [f for f in facts if re.sub(r"\s+", "", f.lower()) not in base]
    return bool(doc_only and PAST_PREMISE.search(nq))


def contextualize_job_rag(q, job, snapshot, intent, context, refs, bodies, forbid_terms=()):
    """직무 문항 Dual RAG(2026-11-16): 질문 은행 문항(Redis) + 공개판 사내문서 맥락(Chroma) + 공고 + 이력서로 다시 쓴다.
    반환 {text, company_facts, doc_refs} | None. None이면 호출 쪽이 기존 contextualize_job(공고·이력서만)으로 내려간다.
    재생성하지 않는다(규칙에 한 번 걸리면 None). 검사 = contextualize_rag와 같은 사내문서 규칙 + 이력서 근거:
      길이 10~160 · 한 문장 · BLOCKLIST · 금지 용어 · 마스킹 표식/가명 · 면접 방식 질문 ·
      company_facts ⊂ (공고 + 주입 조각 본문) · doc_refs ⊂ 주입 조각 ID(빈 목록이면 사내문서 미사용 → None) ·
      영문·숫자 토큰과 used_terms ⊂ (은행 원문 + 조각 본문 + 공고 + 이력서) · 허위 경험 전제(_premise_violation) 없음.
    사내문서는 질문의 맥락·근거로만 쓴다 — 이력서 근거 검사(_tokens_ok의 snapshot)는 지원자 경험의 유일한 근거다."""
    from .agents import internal_forbid
    if not forbid_terms or not context:
        return None
    system = prompts.load("generate/job_question_rag", company_name=job.get("company_name") or "우리 회사",
                          role_name=job.get("role_name") or "", company_intro=(job.get("company_intro") or "")[:400],
                          duties=(job.get("duties") or "")[:400], requirements=(job.get("requirements") or "")[:300],
                          internal_context=context, intent=intent or "원래 질문과 같음",
                          resume_keywords=", ".join(resume_keywords(snapshot)) or "(없음)", seconds=config.QUESTION_SECONDS)
    try:
        out = llm.chat_json(system, f"질문 은행 문항: {q}", JOB_RAG_SCHEMA, timeout=90, temperature=0.4)
    except llm.LLMError:
        return None
    nq = (out.get("question") or "").strip()
    facts = [f.strip() for f in out.get("company_facts") or [] if f and f.strip()]
    drefs = [r.strip() for r in out.get("doc_refs") or [] if r and r.strip()]
    used = [t for t in out.get("used_terms") or [] if t and t.strip()]
    if not (10 <= len(nq) <= 160) or not one_sentence(nq) or BLOCKLIST.search(nq):
        return None
    if internal_forbid.find_terms(nq, forbid_terms) or any(internal_forbid.find_terms(f, forbid_terms) for f in facts):
        return None
    if internal_forbid.masked_hits(nq) or any(internal_forbid.masked_hits(f) for f in facts) or asks_interview_method(nq):
        return None
    allowed = re.sub(r"\s+", "", " ".join([job.get("company_intro") or "", job.get("duties") or "",
                                            job.get("requirements") or ""] + list(bodies)))
    if not all(re.sub(r"\s+", "", f) in allowed for f in facts):
        return None
    if not drefs or not set(drefs) <= set(refs):
        return None
    if _bad_tokens(nq, " ".join([q] + list(bodies)), job, snapshot, used):
        return None
    if _premise_violation(nq, facts, job, snapshot):
        return None
    return {"text": nq, "company_facts": facts, "doc_refs": drefs}


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
    # F11(2026-11-23 사용자 지적): 이력서 질문에 "{지원 회사}에서 ~를 **다시** 맡는다면"이라고 쓰면 그 회사에서
    # 예전에 해 봤다는 뜻이 된다. '다시'를 쓸 수 있는 건 **이전 회사 이름이 같은 문장에 있을 때**뿐이라, 항목마다
    # 이전 회사명을 같이 넘긴다. 다만 **없는 연결을 만들지 않는다**:
    #   · 경력 항목 → 그 항목의 회사명이 곧 이전 회사다(확실).
    #   · 프로젝트 항목 → 스냅샷에 '이 프로젝트가 어느 회사 것인지'가 없다. 경력이 **정확히 하나**일 때만
    #     그 회사로 잇고, 둘 이상이면 비워 둔다(회사명 없는 형태로 질문이 만들어진다).
    careers = [(it.get("company") or "").strip() for it in snapshot.get("career", []) if (it.get("company") or "").strip()]
    only_career = careers[0] if len(careers) == 1 else ""
    for c in cands:
        c["prev_company"] = (c["label"] if c["section"] == "career" else only_career) or ""
    cands.sort(key=lambda c: (-c["ai"], -c["year"]))
    if not cands:  # RSQ-04: 학력으로 대체
        for i, it in enumerate(snapshot.get("education", [])):
            if (it.get("major") or it.get("detail")):
                label = " ".join(x for x in [it.get("school", ""), it.get("major", "")] if x).strip() or "학력"
                cands.append({"section": "education", "index": i, "label": label, "item": it, "ai": 0,
                              "year": _year(it.get("period")), "prev_company": ""})
    return cands


def _snapshot_text(snapshot):
    parts = []
    for sec in ("education", "career", "projects", "certificates"):
        for it in snapshot.get(sec, []):
            parts.extend(str(v) for k, v in it.items() if k not in ("page", "needs_check", "origin") and not isinstance(v, list))
            parts.extend(it.get("tech", []) or [])
    parts.extend(snapshot.get("skills", []))
    return " ".join(parts).lower()


_CITED_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|\d+(?:\.\d+)?%?")
_CITED_HANGUL = re.compile(r"[가-힣]{2,}")
# 인용 어구를 쪼갤 때 **빼고 보는 일반 낱말**(2026-11-23 ③). 어떤 기술·사실도 가리키지 않는 구조어라
# 이력서에 그 낱말이 있는지는 근거와 무관하다. 실측: `C++ 코드`가 '코드' 때문에, `로트 정보`가 '정보'
# 때문에 떨어졌다(이력서에는 'C++ 추론 모듈'·'로트 번호'가 있다). 고유한 쪽(C++·로트)은 그대로 검사한다.
_CITED_GENERIC = frozenset((
    "코드", "정보", "방식", "결과", "내용", "작업", "업무", "시스템", "기능", "환경", "처리", "구성",
    "설계", "개선", "구현", "적용", "관리", "운영", "확인", "기록", "기준", "과정", "단계", "부분",
    "문제", "상황", "판정", "사용", "진행", "변경", "요청", "제공", "지원", "대응",
))


def _in_snapshot(term, text, squash):
    """이력서 본문에 있는가 — 공백은 무시하고 본다(기존 `grounded`와 같은 판정)."""
    tl = (term or "").lower().strip(".")
    return bool(tl) and (tl in text or re.sub(r"\s+", "", tl) in squash)


def _cited_ok(term, text, squash):
    """인용 어구 1개가 이력서에 근거하는가 — 2026-11-23 사용자 승인(③).

    전에는 어구 **전체**가 글자 그대로 있어야 했다. 그래서 의미는 맞고 표현만 합친 인용이 전부 떨어졌다
    (실측: `상담 티켓 목록 API` — 이력서에 '상담 티켓 플랫폼'·'티켓 목록·검색 화면'·'상담 이력 목록 API'가
    다 있는데도 탈락. 같은 유형으로 `C++ 코드`(이력서는 'C++ 추론 모듈') · `로트 정보`('로트 번호') ·
    `결과 판정`('판정 결과')가 떨어져 이력서 질문 8개 중 7개가 템플릿으로 추락했다).

    새 기준 — 어구를 쪼개서 본다:
      · 영문·숫자 토큰(`API`·`C++`·`6.2%`)은 **그대로 유지**해 완전일치를 요구한다. 지어낸 기술명·수치를
        막는 것이 이 검사의 핵심이고, 거기를 느슨하게 하면 RSQ-03이 무너진다.
      · 한글 낱말(2자 이상)은 **낱말마다** 이력서에 있으면 통과한다. 어구 전체가 그대로 있을 필요는 없다.
      · 단 `_CITED_GENERIC`(코드·정보·방식 같은 구조어)은 어떤 사실도 가리키지 않으므로 빼고 본다.
    즉 `상담 티켓 목록 API` → `API` + `상담`·`티켓`·`목록` → 통과. `C++ 코드` → `C++`만 보고 통과.
    `로트 정보` → `로트`만 보고 통과. 반대로 `Kubernetes 오토스케일링`·`블록체인 정산`은 고유한 쪽이
    이력서에 없으므로 그대로 탈락한다(지어내기 차단은 유지된다).
    """
    if _in_snapshot(term, text, squash):          # 어구 전체가 그대로 있으면 바로 통과(기존 경로)
        return True
    parts = _CITED_LATIN.findall(term or "") + _CITED_HANGUL.findall(term or "")
    specific = [p for p in parts if p not in _CITED_GENERIC]
    if not parts:
        return False                              # 쪼갤 것이 없으면(기호뿐) 통과시키지 않는다
    if not specific:
        return True                               # 구조어뿐인 어구는 가리키는 사실이 없다 → 막을 이유가 없다
    return all(_in_snapshot(p, text, squash) for p in specific)


def grounded(question, cited, snapshot):
    """RSQ-03: 질문 속 영문·숫자 토큰은 이력서에 **그대로** 있어야 하고, 인용 어구는 `_cited_ok` 기준을 따른다."""
    text = _snapshot_text(snapshot)
    squash = re.sub(r"\s+", "", text)
    for t in set(_CITED_LATIN.findall(question)):             # 질문 본문의 영문·숫자 = 완전일치(그대로)
        if not _in_snapshot(t, text, squash):
            return False
    for c in {c for c in (cited or []) if c}:                 # 인용 어구 = 쪼개서 검사(2026-11-23 ③)
        if not _cited_ok(c, text, squash):
            return False
    return True


def ungrounded_terms(question, cited, snapshot):
    """`grounded`가 거절할 때 **어떤 토큰이 걸렸는지** — 관측 전용(2026-11-18).
    판정은 `grounded`가 그대로 하고 이 함수는 사유 기록에만 쓴다(RSQ-03 로직은 건드리지 않는다).
    규칙은 `grounded`와 같다(2026-11-23 ③ 포함): 질문의 영문·숫자 토큰은 완전일치, 인용 어구는 쪼개서 본다."""
    text = _snapshot_text(snapshot)
    squash = re.sub(r"\s+", "", text)
    bad = [t for t in set(_CITED_LATIN.findall(question)) if not _in_snapshot(t, text, squash)]
    bad += [c for c in {c for c in (cited or []) if c} if not _cited_ok(c, text, squash)]
    return sorted(set(bad))


def _reject_reason(q, cited, snapshot):
    """이력서 질문이 왜 템플릿으로 떨어졌는지 — **관측 전용**(판정 경로가 아니다).
    `make_resume_question`의 검사 순서를 그대로 다시 본다. 토큰은 이력서에서 온 문자열일 수 있으므로
    측정 산출물(그래프 State의 draft)에만 남는다 — `finalize`가 걸러서 DB·로그·외부로는 나가지 않는다."""
    if not q:
        return {"stage": "empty"}
    if not one_sentence(q):
        return {"stage": "one_sentence", "chars": len(q)}
    hit = BLOCKLIST.search(q)
    if hit:
        return {"stage": "blocklist", "hit": hit.group(0)}
    bad = ungrounded_terms(q, cited, snapshot)
    if bad:
        return {"stage": "grounded", "terms": bad[:8], "n_terms": len(bad), "cited": list(cited or [])[:8]}
    return {"stage": "fit_scope"}      # 위 검사는 통과했는데 fit_scope 뒤 재검사에서 걸린 경우


def _note_reject(why, q, out, snapshot):
    """사유 한 건 기록. **실패해도 흐름을 멈추지 않는다** — 관측이 면접을 깨면 안 된다."""
    try:
        why.append(_reject_reason(q, (out or {}).get("cited_terms") or [], snapshot))
    except Exception:  # noqa: BLE001
        pass


RSQ_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}, "cited_terms": {"type": "array", "items": {"type": "string"}}},
              "required": ["question", "cited_terms"]}
RSQ_SYSTEM = """너는 AI/AX 엔지니어 1차 면접 질문을 만드는 면접관이다.
규칙:
1. 주어진 이력서 항목 하나에 근거해 한국어 질문 한 문장만 만든다.
2. 항목에 실제로 적힌 역할·기술·성과를 짚어 묻는다. 항목에 없는 프로젝트명·기술명·수치를 만들어 넣지 않는다.
3. 90초 안에 답할 수 있게 요구 과제는 1개만 묻는다(의문사는 한 번만, 수치·성과를 결론처럼 앞에 두지 않는다).
4. 학교·회사의 이름·서열·규모, 나이·공백기·가족 등 인적 사정은 묻지 않는다. 수행한 일과 역량만 묻는다.
5. cited_terms에는 질문에 쓴 고유 명칭(프로젝트명·기술명·수치)을 이력서 표기 그대로 넣는다."""


MERGE_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}, "cited_terms": {"type": "array", "items": {"type": "string"}},
                "used_bank": {"type": "boolean"}}, "required": ["question", "cited_terms", "used_bank"]}


def resume_pairs(cands, n=RESUME_N):
    """이력서 질문 n개의 (후보 항목, 관점) 짝. 항목이 모자라면 같은 항목을 다른 관점으로 묻는다."""
    if not cands:
        return []
    # 2026-11-20: 항목이 달라도 관점을 0으로 같게 주면, 둘 다 템플릿으로 떨어질 때 같은 문장 틀이 두 번 나왔다
    # (attempt 14 재현: "~에서 겪은 가장 어려운 문제를 … 다시 맡는다면" ×2). 항목마다 관점을 돌린다.
    pairs = [(c, i % len(ANGLES)) for i, c in enumerate(cands[:n])]
    angle = len(pairs)
    while len(pairs) < n:
        pairs.append((cands[(len(pairs)) % len(cands)], angle % len(ANGLES)))
        angle += 1
    return pairs


def make_resume_question(cand, angle, snapshot, bank=None, role_name="", company="", avoid=(), ignore_text=""):
    """이력서 항목(근거) + 질문 은행 참고 문항(관점)을 합치는 생성 에이전트(2026-11-09).
    근거 검사(RSQ-03)·차단 목록·1회 재생성 → 실패하면 템플릿.
    2026-11-20(사용자 응시 피드백):
      F3 이력서 프로젝트를 그대로 되묻지 않고 지원 회사(`company`) 맥락의 가정형으로 잇는다(지시문 + 템플릿)
      F4 앞서 낸 질문(`avoid`)과 같은 핵심 주제(예: '반사광')를 다시 묻지 않는다 → 다시 만들고, 그래도 같으면 템플릿
      QST-20 문장 규칙 + 수치 결론형 금지 + F9 회사 경력 전제 금지(QR.resume_issue)"""
    it = cand["item"]
    desc = "\n".join(f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in it.items()
                     if k not in ("page", "needs_check", "origin", "source") and v)
    ref = {"section": cand["section"], "index": cand["index"], "label": cand["label"], "quote": (it.get("detail") or it.get("source") or "")[:160]}
    if bank:
        ref["bank_doc"] = bank.get("doc_id") or (f"db:{bank['id']}" if bank.get("id") else "")
    bank_txt = f"\n질문 은행 참고 문항: {bank['text']}\n참고 문항의 평가 의도: {bank.get('subcategory') or '-'}" if bank else "\n질문 은행 참고 문항: (없음)"
    company = (company or "").strip() or "지원하신 회사"
    system = prompts.load("generate/resume_question_merger", role_name=role_name or "AI/AX 엔지니어", company_name=company)
    avoid_txt = ""
    if avoid:
        avoid_txt = "\n\n[앞에서 이미 낸 질문 — 같은 주제·같은 핵심어를 다시 묻지 않는다]\n" + "\n".join(f"- {a}" for a in avoid)
    why = []                  # 거절 사유 관측(2026-11-18) — 아래 판정 조건은 한 글자도 바꾸지 않았다
    hint = ""
    for _ in range(2):  # 1회 재생성 (RSQ-03)
        try:
            # F11(2026-11-23): 이전 회사를 알면 그 이름을 알려 줘 "[이전 회사]에서 하신 ~를 {회사}에서 같은 문제로
            # 마주한다면"으로 쓰게 한다. 모르면 그 줄을 비워 회사명 없는 형태("이 직무에서 마주한다면")로 가게 둔다.
            prev = (cand.get("prev_company") or "").strip()
            prev_txt = (f"\n이력서 항목의 이전 회사: {prev}" if prev and prev != cand["label"]
                        else "\n이력서 항목의 이전 회사: (모름 — 회사 이름 없이 '이 직무에서 마주한다면'으로 쓴다)")
            out = llm.chat_json(system, f"질문 관점: {ANGLES[angle]}{bank_txt}\n이력서 항목:\n{desc}{prev_txt}{avoid_txt}{hint}",
                                MERGE_SCHEMA, timeout=90, temperature=0.4)
        except llm.LLMError:
            why.append({"stage": "llm_error"})
            break
        q = out["question"].strip()
        cited = [c for c in (out.get("cited_terms") or []) if c and c.strip() != company]   # 회사 이름은 이력서 근거가 아니다
        # 2026-11-12 RAG 평가: 한 문장 검사 추가(두 문장 질문이 그대로 나가던 경로)
        if q and one_sentence(q) and not BLOCKLIST.search(q) and grounded(q.replace(company, ""), cited, snapshot):
            q, _ = fit_scope(q, config.QUESTION_SECONDS, "직무 전문성·구체성")
            if not BLOCKLIST.search(q) and one_sentence(q):
                # 2026-11-20: 사내문서 질문과 같은 문장 규칙(QST-20) + 수치 결론형 금지 + 회사 경력 전제 금지.
                # 전에는 이 경로에 문장 검사가 없어 "…DB 용량을 60% 줄인 경험을 설명해 주세요"가 그대로 나갔다.
                # F11(2026-11-23): 이전 회사·프로젝트 이름을 같이 넘겨 "그 회사에서 하신 ~를 {회사}에서"는 허용한다.
                issue = QR.resume_issue(q, company, (cand.get("prev_company") or "", cand["label"]))
                if not issue:
                    from .agents import fewshot_guard
                    leak = fewshot_guard.leak_terms(q, _snapshot_text(snapshot), desc, bank_txt, ignore_text, company)
                    if leak:
                        issue = "fewshot_leak:" + ",".join(leak[:3])   # 지시문 예문의 말이 이력서·공고에 없이 들어옴
                if not issue and company not in q and not re.search(r"다면|가정", q):
                    issue = "no_company_context"         # F3: 이력서 항목을 그대로 되묻는 질문(회사 맥락 가정형이 아님)
                same = next((QR.shared_topic(q, a, ignore=[company, ignore_text]) for a in avoid if QR.shared_topic(q, a, ignore=[company, ignore_text])), [])
                if not issue and not same:
                    if bank:
                        ref["bank_used"] = bool(out.get("used_bank"))
                    return {"text": q, "resume_ref": ref, "gen_method": "llm"}
                why.append({"stage": "style", "reason": issue or ("same_topic:" + ",".join(same[:3]))})
                hint = ("\n\n(직전 질문은 쓰지 않았다 — "
                        + (f"앞 질문과 같은 주제({', '.join(same[:3])})다. 이 항목의 **다른 기술·측면**을 물어라. " if same else "")
                        + f"한 가지만 묻고, 이력서의 수치·성과를 결론처럼 앞에 두지 말고, {company}에서 다시 맡는다고 **가정**하는 형태로 써라.)")
                continue
            _note_reject(why, q, out, snapshot)      # fit_scope 뒤 재검사에서 걸림
        else:
            _note_reject(why, q, out, snapshot)
    tpl = template_text(angle, cand["label"], company, cand.get("prev_company"))
    if any(QR.shared_topic(tpl, a, ignore=[company, ignore_text]) for a in avoid):
        tpl = generic_template_text(company)            # F4: 라벨 템플릿도 앞 질문과 같은 주제 → 라벨 없는 문장
    r = {"text": tpl, "resume_ref": ref, "gen_method": "template"}
    if why:
        r["reject"] = why
    return r


def resume_questions(snapshot, use_llm=True, banks=None, role_name="", company=""):
    cands = resume_candidates(snapshot)
    if not cands:  # RSQ-04: 직무 DB로 대체
        return [{"category": "job_fallback", "reason": "이력서에 경력·프로젝트·학력 내용이 없어 직무 질문으로 대체"}] * RESUME_N
    out = []
    for i, (cand, angle) in enumerate(resume_pairs(cands)):
        bank = (banks or [None] * RESUME_N)[i] if banks and i < len(banks) else None
        if use_llm:
            out.append(make_resume_question(cand, angle, snapshot, bank, role_name, company, avoid=[o["text"] for o in out]))
        else:
            ref = {"section": cand["section"], "index": cand["index"], "label": cand["label"], "quote": (cand["item"].get("detail") or cand["item"].get("source") or "")[:160]}
            out.append({"text": template_text(angle, cand["label"], company, cand.get("prev_company")),
                        "resume_ref": ref, "gen_method": "template"})
    return out


# ---------- full set ----------
def build_set(application, use_llm=True):
    job = db.one("""SELECT j.*, c.name company_name, r.name role_name FROM jobs j JOIN companies c ON c.id=j.company_id
                    JOIN job_roles r ON r.code=j.role_code WHERE j.id=%s""", application["job_id"])
    snapshot = application["resume_snapshot"] or {}
    result, used_groups = [], set()
    rq = resume_questions(snapshot, use_llm, role_name=job.get("role_name") or "", company=job.get("company_name") or "")
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


# ---------- applications.pregen_questions(JSONB) 형식 (2026-11-13, ADLC v0.7 6.14.3 — 스키마 변경 없음) ----------
# v2 = {"v": 2, "questions": [...], "guard_report": {...}, "internal_refs": [...]}. 옛 형식 = 질문 목록(list) 그대로.
# guard_report·internal_refs에는 사유 종류·건수·조각 ID만 — 질문 원문 밖의 문장·조각 본문·금지 용어 원문은 넣지 않는다(B-08).
GUARD_REASONS = ("forbid_unavailable", "forbid_term", "mask_marker", "interview_meta","facts_unmatched", "doc_ref_invalid", "rule", "model_flag")
_REF_RE = re.compile(r"^[A-Za-z0-9_\-]+#[A-Za-z0-9_\-]*$")


def _safe_guard_report(rep):
    rep = rep or {}
    return {"checked": int(rep.get("checked") or 0), "blocked": int(rep.get("blocked") or 0),
            "reasons": {k: int(v) for k, v in (rep.get("reasons") or {}).items() if k in GUARD_REASONS},
            "model": rep.get("model") if rep.get("model") in ("skipped", "ok", "error") else "skipped"}


def pack_pregen(qs, info=None):
    info = info or {}
    refs = [r for r in (info.get("internal_refs") or []) if isinstance(r, str) and _REF_RE.match(r)]
    return {"v": 2, "questions": qs, "guard_report": _safe_guard_report(info.get("guard_report")), "internal_refs": refs}


def pregen_list(value):
    """pregen_questions → 질문 목록. 옛 형식(list)·v2(dict) 둘 다 읽는다. 없거나 모르는 형식이면 빈 목록."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return value.get("questions") or []
    return []


def pregenerate(application_id):
    """RSQ-05: run during environment check; interview start uses this if ready."""
    db.run("UPDATE applications SET pregen_status='running' WHERE id=%s", application_id)
    try:
        app = db.one("SELECT * FROM applications WHERE id=%s", application_id)
        try:   # 그래프 1-A 질문 생성(LangGraph, ADLC v0.5 6.1). 그래프가 실패하면 기존 함수 경로
            from .agents import question_graph
            qs, info = question_graph.generate_set(app, use_llm=True)
        except Exception:
            qs, info = build_set(app, use_llm=True), {}
        db.run("UPDATE applications SET pregen_status='ready', pregen_questions=%s WHERE id=%s",
               db.J(pack_pregen(qs, info)), application_id)
        from . import speech   # v0.9 INT-01: 환경 점검 중에 질문 음성(Qwen3-TTS)도 미리 만든다(TTS가 실시간보다 느림)
        speech.prewarm([q["text"] for q in qs])
    except Exception:
        db.run("UPDATE applications SET pregen_status='failed' WHERE id=%s", application_id)


def start_pregen(application_id):
    threading.Thread(target=pregenerate, args=(application_id,), daemon=True).start()
