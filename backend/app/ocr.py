"""Resume OCR + structuring pipeline (PRD 4.4 OCR-01~07, TRD §5).

page decision: text layer >= 50 chars -> direct text, else 300dpi render -> Tesseract kor+eng.
structuring: LLM JSON schema; every item must carry a `source` sentence found in the raw text.
"""
import io
import re
import threading

import pymupdf
import pytesseract
from PIL import Image

from . import config, db, llm

MIN_PAGE_CHARS = 50
MIN_TOTAL_CHARS = 100
LOW_CONF = 60
MAX_PAGES = 5

SECTIONS = ["education", "career", "projects", "certificates"]

_item = lambda props: {"type": "array", "items": {"type": "object", "properties": {**props, "page": {"type": "integer"}, "source": {"type": "string"}}, "required": list(props) + ["page", "source"]}}
SCHEMA = {
    "type": "object",
    "properties": {
        "basic": {"type": "object", "properties": {"name": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"}}, "required": ["name", "email", "phone"]},
        "education": _item({"school": {"type": "string"}, "major": {"type": "string"}, "degree": {"type": "string"}, "period": {"type": "string"}, "detail": {"type": "string"}}),
        "career": _item({"company": {"type": "string"}, "role": {"type": "string"}, "period": {"type": "string"}, "detail": {"type": "string"}, "tech": {"type": "array", "items": {"type": "string"}}}),
        "projects": _item({"name": {"type": "string"}, "role": {"type": "string"}, "period": {"type": "string"}, "detail": {"type": "string"}, "tech": {"type": "array", "items": {"type": "string"}}}),
        "skills": {"type": "array", "items": {"type": "string"}},
        "certificates": _item({"name": {"type": "string"}, "date": {"type": "string"}}),
    },
    "required": ["basic", "education", "career", "projects", "skills", "certificates"],
}

SYSTEM = """너는 이력서 원문을 표준 항목으로 옮기는 정리 도구다.
규칙:
1. 원문에 실제로 적힌 내용만 옮긴다. 추측·보완·요약 창작 금지. 없으면 빈 문자열 또는 빈 배열.
2. 각 항목의 source에는 그 항목의 근거가 되는 원문 문장을 글자 그대로 한 줄 복사한다.
3. page에는 그 원문이 있는 쪽 번호([PAGE n] 표시)를 넣는다.
4. 생년월일, 나이, 성별, 주소, 사진, 주민등록번호, 가족, 종교, 병역 정보는 어디에도 넣지 않는다.
5. basic에는 이름·이메일·전화번호만 넣는다.
6. detail은 원문 표현을 최대한 유지해 1~2문장으로 옮긴다.
7. 항목 종류는 원문의 절 제목이 아니라 내용으로 정한다. 한 절(예: '학력 · 캡스톤')에 여러 종류가 섞여 있으면 항목마다 나눠 담는다.
   - education: 학교·전공·학위 줄만. period는 입학·졸업(재학) 시기만 넣고, 원문에 없으면 빈 문자열. 학기 단위 과제·활동 기간(예: '2023년 2학기')은 넣지 않는다. detail에는 그 학교 줄의 학점·관련 과목 같은 학력 내용만 넣는다.
   - 캡스톤·졸업 작품·학부·팀 과제·공모전·동아리처럼 무엇을 만들거나 한 활동은 education이 아니라 projects에 넣는다(name = 원문의 과제 이름, period = 그 활동 기간).
   - certificates에는 자격증·면허·어학 시험·어학 능력만 넣는다. 과제·과목·활동은 넣지 않는다.
   - career에는 회사·기관에서 일한 경력만 넣는다(인턴 포함).
8. source는 그 항목의 이름(학교명·회사명·과제명·자격증명)이 적힌 원문 줄을 복사한다. 다른 항목의 문장을 source로 쓰지 않는다."""


def _norm(s):
    return re.sub(r"\s+", "", s or "").lower()


def _ocr_image(img):
    data = pytesseract.image_to_data(img, lang=config.OCR_LANG, config="--psm 6", output_type=pytesseract.Output.DICT)
    words, confs, lines, last = [], [], [], None
    for i, w in enumerate(data["text"]):
        if not w.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key != last and words:
            lines.append(" ".join(words))
            words = []
        last = key
        words.append(w)
        c = float(data["conf"][i])
        if c >= 0:
            confs.append(c)
    if words:
        lines.append(" ".join(words))
    conf = round(sum(confs) / len(confs), 1) if confs else 0.0
    return "\n".join(fix_korean_spacing(l) for l in lines), conf


def fix_korean_spacing(line):
    """Tesseract kor often splits syllables ("한 국 대학교" → "한국 대학교"): glue a lone Hangul syllable to the next Hangul token."""
    out, prev_single = [], False
    for t in line.split(" "):
        single = bool(re.fullmatch(r"[가-힣]", t)) and t not in KEEP_ALONE
        if out and prev_single and re.match(r"[가-힣]", t) and t not in KEEP_ALONE:
            out[-1] += t
        else:
            out.append(t)
        prev_single = single
    return " ".join(out)


KEEP_ALONE = {"및", "등", "외", "약", "총", "각", "또"}


def extract_pages(path, mime, progress=lambda *_: None):
    pages = []
    if mime == "application/pdf":
        doc = pymupdf.open(path)
        n = min(doc.page_count, MAX_PAGES)
        for i in range(n):
            page = doc[i]
            text = page.get_text().strip()
            if len(text) >= MIN_PAGE_CHARS:
                pages.append({"page": i + 1, "method": "text", "confidence": 100.0, "text": text})
            else:
                progress("ocr", 10 + int(60 * i / n))
                pix = page.get_pixmap(dpi=300)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                text, conf = _ocr_image(img)
                pages.append({"page": i + 1, "method": "ocr", "confidence": conf, "text": text})
    else:
        progress("ocr", 20)
        img = Image.open(path)
        img = img.convert("RGB")
        if max(img.size) < 1800:  # small phone photos: upscale helps Tesseract
            scale = 1800 / max(img.size)
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        text, conf = _ocr_image(img)
        pages.append({"page": 1, "method": "ocr", "confidence": conf, "text": text})
    return pages


def verify_structure(data, raw_text, pages):
    """Drop values not grounded in raw text; flag items from low-confidence pages (OCR-02/03)."""
    raw = _norm(raw_text)
    low_pages = {p["page"] for p in pages if p["confidence"] < LOW_CONF}
    for sec in SECTIONS:
        kept = []
        for item in data.get(sec, []):
            src = item.get("source", "")
            # grounded if the source line (or most of it) exists in the original text
            ok = bool(src) and (_norm(src) in raw or _overlap(src, raw_text) >= 0.7)
            if not ok:
                continue
            item["needs_check"] = item.get("page") in low_pages
            item["origin"] = "auto"
            kept.append(item)
        data[sec] = kept
    _drop_nameless(data)
    _fit_education(data, raw_text)       # 남의 문장을 근거로 단 칸을 먼저 비우고
    _fit_certificates(data, raw_text)
    _recover_missed(data, raw_text, low_pages)   # 그래도 아무도 쓰지 않은 '제목 · 기간' 블록을 항목으로
    _fit_education(data, raw_text)       # 새 항목이 쓴 줄은 학력 근거 창에서 빠지도록 한 번 더
    data["skills"] = [s for s in data.get("skills", []) if _norm(s) and _norm(s) in raw]
    basic = data.get("basic", {})
    for k in ("name", "email", "phone"):
        if basic.get(k) and _norm(basic[k]) not in raw:
            basic[k] = ""
    data["basic"] = basic
    return data


# ---- 칸-근거 맞춤 (2026-11-20, 한도윤 이력서: '학력 · 캡스톤' 절의 캡스톤 학기·내용이 학력 카드로 들어간 버그) ----
# 위 검사는 source가 원문 어딘가에 있는지만 봐서, 다른 항목(캡스톤)의 문장을 근거로 단 학력 항목도 통과했다.
# 학력·자격증은 한두 줄짜리 항목이므로, 이름(학교명·자격증명)이 적힌 원문 줄과 바로 옆 줄을 근거 창으로 삼아
# 그 창에 없는 기간·설명은 비운다(지어내지 않음). 다른 항목이 근거로 쓴 줄은 창에서 뺀다.
PAGE_RE = re.compile(r"^\[PAGE \d+\]$")
TERM_RE = re.compile(r"학기|semester", re.I)   # 학력 기간은 입학·졸업 시기만 — 학기 단위 활동 기간은 다른 항목의 것


def _lines(raw_text):
    return [l.strip() for l in (raw_text or "").split("\n") if l.strip()]


SCHOOL_RE = re.compile(r"대학교|대학원|학교|부트캠프|아카데미|university|college|bootcamp", re.I)
WIN_DOWN = 5   # 학교 줄 아래로 최대 몇 줄까지 그 학력의 근거로 볼지


def _claimed(data, skip):
    """다른 항목들이 source·detail로 쓴 원문(정규화해 이어 붙인 문자열)."""
    return "\n".join(_norm(it.get(k)) for sec in SECTIONS for it in data.get(sec, []) if it is not skip
                     for k in ("source", "detail") if it.get(k))


def _is_title(line):
    """'제목 · 역할 · 기간'처럼 구분점과 연월·학기가 같이 있는 줄 = 다른 항목의 시작."""
    return bool(PERIOD_RE.search(line)) and len(re.split(r"\s[·|]\s", line)) >= 2


def _window(lines, key, also=(), claimed=""):
    """key가 적힌 줄(also 값도 같이 적힌 줄 우선, 같으면 짧은 줄)과 그 항목 블록.
    위로 한 줄, 아래로 WIN_DOWN줄까지 — 쪽 경계·소제목·다른 항목이 쓴 줄·다른 항목 제목 줄·다른 학교 줄에서 멈춘다."""
    k = _norm(key)
    hits = [i for i, l in enumerate(lines) if k and k in _norm(l)]
    if not hits:
        return None, []
    extra = [_norm(a) for a in also if _norm(a)]
    i = max(hits, key=lambda i: (sum(a in _norm(lines[i]) for a in extra), -len(lines[i])))

    def stop(l):
        n = _norm(l)
        return (PAGE_RE.match(l) or _is_heading(l) or _is_title(l) or SCHOOL_RE.search(l)
                or (len(n) >= 8 and n in claimed))
    win = [lines[i]]
    if i > 0 and not stop(lines[i - 1]):
        win.insert(0, lines[i - 1])
    for j in range(i + 1, min(len(lines), i + 1 + WIN_DOWN)):
        if stop(lines[j]):
            break
        win.append(lines[j])
    return lines[i], win


def _keep_in(text, window_norm):
    """text를 줄·문장 조각으로 나눠 근거 창에 글자 그대로 있는 조각만 남긴다."""
    parts = [p.strip(" -·•/") for p in re.split(r"\n|(?<=[.!?])\s+| / ", text or "")]
    return " ".join(p for p in parts if _norm(p) and _norm(p) in window_norm)


def _fit_education(data, raw_text):
    lines = _lines(raw_text)
    for it in data.get("education", []):
        anchor, win = _window(lines, it.get("school"), (it.get("major"), it.get("degree")), _claimed(data, it))
        if not anchor:
            continue   # 학교명이 원문 줄에서 안 보이면(OCR 띄어쓰기 등) 기존 검사 결과를 그대로 둔다
        wn = _norm("\n".join(win))
        for k in ("major", "degree", "period"):
            if it.get(k) and _norm(it[k]) not in wn:
                it[k] = ""
        if it.get("period") and TERM_RE.search(it["period"]):
            it["period"] = ""
        it["detail"] = _keep_in(it.get("detail"), wn)
        if _norm(it.get("school")) not in _norm(it.get("source")):
            it["source"] = anchor   # 원문 표시는 학교가 적힌 줄로


def _drop_nameless(data):
    """이름 칸이 빈 항목은 버린다(자기소개 문단이 학력·경력 카드로 들어가던 것)."""
    data["education"] = [it for it in data.get("education", []) if _norm(it.get("school"))]
    data["career"] = [it for it in data.get("career", []) if _norm(it.get("company")) or _norm(it.get("role"))]
    data["projects"] = [it for it in data.get("projects", []) if _norm(it.get("name"))]


def _fit_certificates(data, raw_text):
    """자격증 항목은 source에 자격증 이름이 있어야 한다(다른 항목 문장을 근거로 단 것은 버림). 날짜는 그 줄 근처에 있어야 한다.
    학교·부트캠프 이름이 자격증 칸에 들어갔으면 학력으로 옮긴다(같은 학교가 이미 학력에 있으면 버림)."""
    lines, kept = _lines(raw_text), []
    for it in data.get("certificates", []):
        if not _norm(it.get("name")) or _norm(it["name"]) not in _norm(it.get("source")):
            continue
        if SCHOOL_RE.search(it["name"]):
            if not any(_norm(e.get("school")) == _norm(it["name"]) for e in data.get("education", [])):
                data.setdefault("education", []).append({"school": it["name"], "major": "", "degree": "", "period": it.get("date", ""),
                                                         "detail": "", "page": it.get("page"), "source": it.get("source"),
                                                         "needs_check": True, "origin": "auto"})
            continue
        _, win = _window(lines, it["name"], (it.get("date"),), _claimed(data, it))
        if it.get("date") and _norm(it["date"]) not in _norm("\n".join(win)):
            it["date"] = ""
        kept.append(it)
    data["certificates"] = kept


# ---- 빠진 항목 되살리기 (2026-11-20) ----
# 소형 모델이 '제목 · 기간' 줄로 시작하는 인턴·캡스톤 항목을 통째로 빠뜨리거나 학력에 섞는다.
# 어느 항목도 근거로 쓰지 않은 '제목 줄(구분점 + 연월·학기)'과 그 아래 본문 줄을 원문 그대로 항목으로 만든다.
# 종류가 분명할 때만(인턴·정규직 → 경력, 캡스톤·프로젝트·팀 과제 → 프로젝트) 만들고, 지원자가 확인하도록 '확인 필요'를 붙인다.
PERIOD_RE = re.compile(r"(?:19|20)\d{2}\s*(?:[.\-/]\s*\d{1,2}|년\s*\d{1,2}\s*(?:월|학기))")
CAREER_HINT = re.compile(r"인턴|정규직|계약직|아르바이트|intern", re.I)
PROJECT_HINT = re.compile(r"캡스톤|프로젝트|졸업\s*작품|공모전|해커톤|동아리|\d인\s*팀|팀\s*과제", re.I)


def _loose(s):
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


def _is_heading(line):
    return len(_loose(line)) <= 15 and not re.search(r"\d", line) and not re.search(r"[.!?다음함됨]$", line.strip())


def _recover_missed(data, raw_text, low_pages):
    lines, page = [], 1
    for l in _lines(raw_text):
        m = PAGE_RE.match(l)
        if m:
            page = int(re.search(r"\d+", l).group())
        lines.append((l, page, bool(m)))
    covered = _loose(" ".join(str(it.get(k) or "") for sec in SECTIONS for it in data.get(sec, [])
                              for k in ("source", "detail", "name", "school", "company", "role")))
    i = 0
    while i < len(lines):
        title, pg, marker = lines[i]
        i += 1
        segs = [s.strip() for s in re.split(r"\s[·|]\s", title) if s.strip()]
        if marker or len(segs) < 2 or not PERIOD_RE.search(title) or _loose(title) in covered:
            continue
        kind = "career" if CAREER_HINT.search(title) else "projects" if PROJECT_HINT.search(title) else None
        words = [s for s in segs if not PERIOD_RE.search(s) and not re.fullmatch(r"\d+\s*(?:년|개월|주)", s)]
        if not kind or not words or any(_loose(w) in covered for w in words):
            continue
        period = next((s for s in segs if PERIOD_RE.search(s)), "")
        body = []
        while i < len(lines):
            l, _, mk = lines[i]
            if mk:
                i += 1
                continue
            if _is_heading(l) or (PERIOD_RE.search(l) and len(re.split(r"\s[·|]\s", l)) >= 2) or _loose(l) in covered:
                break
            body.append(l)
            i += 1
        detail = "\n".join(body)
        if kind == "career":
            item = {"company": words[0], "role": " · ".join(words[1:]), "period": period, "detail": detail, "tech": []}
        else:
            role = next((w for w in words if PROJECT_HINT.search(w)), "")
            rest = [w for w in words if w != role] or [role]
            item = {"name": max(rest, key=len), "role": role if rest != [role] else "", "period": period, "detail": detail, "tech": []}
        item.update(page=pg, source=title, needs_check=True, origin="auto")
        data.setdefault(kind, []).append(item)
        covered += _loose(title + detail)


def _overlap(src, raw_text):
    toks = [t for t in re.split(r"\W+", src) if len(t) > 1]
    if not toks:
        return 0
    raw = _norm(raw_text)
    return sum(1 for t in toks if t.lower() in raw) / len(toks)


def empty_structure():
    return {"basic": {"name": "", "email": "", "phone": ""}, "education": [], "career": [], "projects": [], "skills": [], "certificates": []}


def process(extraction_id, path, mime):
    def progress(stage, pct):
        db.run("UPDATE resume_extractions SET stage=%s, progress=%s WHERE id=%s", stage, pct, extraction_id)

    try:
        progress("text", 5)
        pages = extract_pages(path, mime, progress)
        raw = "\n\n".join(f"[PAGE {p['page']}]\n{p['text']}" for p in pages)
        total = sum(len(p["text"].strip()) for p in pages)
        db.run("UPDATE resume_extractions SET pages=%s, raw_text=%s, ocr_engine=%s WHERE id=%s",
               db.J(pages), raw, config.VERSIONS["ocr"], extraction_id)
        if total < MIN_TOTAL_CHARS:  # OCR-04: 인식 실패 → 직접 작성 전환
            db.run("UPDATE resume_extractions SET status='failed', stage='done', progress=100, structured=%s, failure_reason=%s WHERE id=%s",
                   db.J(empty_structure()), "low_text", extraction_id)
            return
        progress("structure", 75)
        try:
            data = llm.chat_json(SYSTEM, f"다음 이력서 원문을 표준 항목으로 정리하라.\n\n{raw[:12000]}", SCHEMA, temperature=0)
            data = verify_structure(data, raw, pages)
        except llm.LLMError:
            data = empty_structure()  # 구조화 실패해도 원문은 보여 주고 지원자가 직접 채운다
            db.run("UPDATE resume_extractions SET failure_reason='structure_failed' WHERE id=%s", extraction_id)
        db.run("UPDATE resume_extractions SET status='review', stage='done', progress=100, structured=%s WHERE id=%s",
               db.J(data), extraction_id)
    except Exception as e:
        db.run("UPDATE resume_extractions SET status='failed', stage='done', progress=100, structured=%s, failure_reason=%s WHERE id=%s",
               db.J(empty_structure()), f"error:{type(e).__name__}", extraction_id)


def start(extraction_id, path, mime):
    threading.Thread(target=process, args=(extraction_id, path, mime), daemon=True).start()


def sniff_mime(head: bytes):
    """Validate by file signature, not extension (6.2 보안)."""
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return None


def pdf_is_safe(path):
    doc = pymupdf.open(path)
    if doc.embfile_count() > 0:
        return False
    for xref in range(1, doc.xref_length()):
        obj = doc.xref_object(xref, compressed=False) or ""
        if "/JavaScript" in obj or "/JS " in obj or "/Launch" in obj:
            return False
    return doc.page_count <= MAX_PAGES
