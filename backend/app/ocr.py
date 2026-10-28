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
6. detail은 원문 표현을 최대한 유지해 1~2문장으로 옮긴다."""


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
    data["skills"] = [s for s in data.get("skills", []) if _norm(s) and _norm(s) in raw]
    basic = data.get("basic", {})
    for k in ("name", "email", "phone"):
        if basic.get(k) and _norm(basic[k]) not in raw:
            basic[k] = ""
    data["basic"] = basic
    return data


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
