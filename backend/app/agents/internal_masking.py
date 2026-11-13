"""사내문서 공개용 사본 마스킹 — A13 적재의 2-B 단계 (RETAG_PROPOSAL_2026-11-13.md 7.8 · 8절 v3, 사용자 승인 2026-11-13).

원본 80개는 그대로 두고(원본 조각은 지금처럼 restricted), 규칙 파일 `publish`의 **비공개 문서 전부(v3 = 68개)**마다
마스킹한 가상 문서(doc_id = `MSK-<파일 번호>`, disclosure = public)를 만들어 문서 목록에 더한다.
그러면 sync 증분 · route · prune · audit이 특별 처리 없이 사본을 다룬다. 대상 목록은 규칙 파일에서만 읽는다.

규칙 파일 `_masking/mask_rules.json`(v3 형식 — 키 = 원본 doc_id, drop_sections = [{idx(1부터), title}],
regex · id_map · incident_map · drop_sentences · table_row_drop · density_guard · placeholders).
적용 순서(본문 + doc_title + sec_title):
  R10 섹션 제외(본문만 비움 → 섹션 순번 유지, idx·title이 안 맞으면 그 문서 사본을 만들지 않음)
  → 단어 단위: R1 고객사 가명 → R7 id_map·incident_map → R8 문서 참조(파일 정규식 + 말뭉치 실제 doc_id) → R6 연락처 삭제
  → 문장 단위: R15 법정 수치 예외 문장이면 R3·R4·R11·R14 건너뜀 / R3 금액 / R4·R11 키워드 문장의 %·점·배 /
    R14 보상 문장의 금액 수치 / R5 공수·규모 / R9·R18 미래 일정만 [일정](문장은 남김)
  → R12 표식뿐인 표 행 삭제(머리글만 남은 표는 표 삭제) → R13 drop_sentences 문장 삭제
  → R16 표식 밀도(조각 단위, build_chunks에서 — mode warn = 건수만, drop = 그 조각 제외)
문장 분리(R13·R16 공통, 제안서 8.3 '결정성'): 줄바꿈에서 자르고, 표 행(`|`로 시작)은 1행 = 1문장,
그 밖의 줄은 마침표·물음표·느낌표 뒤 공백에서 자른다. R17(견적 업체 가명)은 말뭉치에 실명이 없어 지금은 할 일이 없다.

- 대체어(id_map·incident_map·R8 대체어·표식·가명 접두어)에는 숫자를 넣지 않는다 — `load_rules`가 확인
- 고객사 가명 표는 파일에 쓰지 않는다 — 원본 forbid 주석에서 메모리로만 만든다(정렬 순서 = 가명 순서)
- 사본 해시 = SHA-256(원본 해시 + 규칙 파일 해시 + 고객사 용어 목록 해시)
- 순수 함수(모델 호출 없음), 두 번 적용해도 결과가 같다(멱등)
"""
import copy
import datetime as dt
import hashlib
import json
import os
import re

from . import internal_forbid

RULES_REL = os.path.join("_masking", "mask_rules.json")
MSK_PREFIX = "MSK-"
MARKER = {"money": "[금액]", "ratio": "[비율]", "score": "[점수]", "effort": "[공수]", "scale": "[규모]",
          "schedule": "[일정]", "customer": "[고객 정보]"}
ORDER = ["R10_drop_sections", "R1_customer", "R7_id_map", "R7_incident_map", "R8_docref", "R6_delete", "R15_exempt",
         "R3_money", "R4_R11_ratio", "R5", "R9_date", "R12_table_rows", "R13_drop_sentences", "R16_density"]
# R14 보상 문장 키워드(R4_keywords 중 보상 용어) — 이 문장의 만·천만 단위 수치는 '원'이 없어도 [금액]
COMP_WORDS = ("연봉", "기본급", "성과급", "인상률", "급여", "수당", "인센티브")


class MaskRulesError(Exception):
    """규칙 파일이 없거나 깨짐 → 사본을 만들지 않고 prune도 MSK-*를 지우지 않는다(7.8 ⑨)."""


# ---------------- 규칙 파일 ----------------
def rules_path(docs_dir):
    return os.getenv("MASK_RULES_PATH") or os.path.join(docs_dir, RULES_REL)


def load_rules(docs_dir=None, path=None):
    """→ (rules dict, 규칙 파일 SHA-256). 형식·정규식·대체어 숫자를 검사하고 실패하면 MaskRulesError."""
    path = path or rules_path(docs_dir)
    try:
        with open(path, "rb") as f:
            raw = f.read().replace(b"\r\n", b"\n")
        rules = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as e:
        raise MaskRulesError(f"{type(e).__name__}: {os.path.basename(path)}") from e
    for k in ("rules_version", "as_of", "publish", "regex", "id_map", "incident_map", "R8_replacement", "placeholders"):
        if k not in rules:
            raise MaskRulesError(f"missing key {k}")
    try:
        dt.date.fromisoformat(rules["as_of"])
    except ValueError as e:
        raise MaskRulesError("as_of") from e
    if rules.get("order") and [o for o in rules["order"] if o not in ORDER]:
        raise MaskRulesError("unknown order step")
    if set(rules["placeholders"]) - set(internal_forbid.MASK_MARKERS):
        raise MaskRulesError("placeholders differ from internal_forbid.MASK_MARKERS")
    alias = rules.get("alias") or {}
    values = list(rules["id_map"].values()) + list(rules["incident_map"].values()) + [rules["R8_replacement"]] \
        + list(rules["placeholders"]) + [alias.get("customer_prefix", "고객사"), alias.get("vendor_prefix", "업체")]
    if [v for v in values if re.search(r"\d", str(v))]:
        raise MaskRulesError("replacement has digits")
    for did, spec in rules["publish"].items():
        for ds in (spec or {}).get("drop_sections", []):
            if not isinstance(ds, dict) or not isinstance(ds.get("idx"), int) or not ds.get("title"):
                raise MaskRulesError(f"drop_sections of {did}")
    try:
        Compiled(rules, {}, [])            # 정규식 전부 컴파일되는지
    except re.error as e:
        raise MaskRulesError(f"regex: {e}") from e
    return rules, hashlib.sha256(raw).hexdigest()


def file_num(path):
    m = re.match(r"(\d{2})_", os.path.basename(path or ""))
    return m.group(1) if m else None


def msk_id(num):
    return f"{MSK_PREFIX}{num}"


# ---------------- R1 고객사 가명 ----------------
_ID_SHAPE = re.compile(r"[A-Za-z0-9\-_]+")


def customer_terms(docs):
    """원본 forbid 주석에서 고객사(고유명사) 용어만: 범위 both + ID 모양 아님. 정렬 = 가명 순서."""
    terms = internal_forbid.collect(docs)
    return sorted(t for t in terms if internal_forbid.term_scope(t) == "both" and not _ID_SHAPE.fullmatch(t))


def _letters(i):
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def aliases(terms, prefix="고객사"):
    return {t: f"{prefix}{_letters(i)}" for i, t in enumerate(terms)}


def list_hash(terms):
    return hashlib.sha256("\n".join(terms).encode("utf-8")).hexdigest()


# ---------------- 문장 분리 (R13·R16 공통) ----------------
def sentences(text):
    """[(문장, 뒤 구분자)] — 이어 붙이면 원문과 같다. 표 행은 1행 = 1문장."""
    out = []
    for line, nl in re.findall(r"([^\n]*)(\n|$)", text or "")[:-1] if text else []:
        if line.lstrip().startswith("|"):
            out.append((line, nl))
            continue
        parts = re.split(r"((?<=[.!?])[ \t]+)", line)
        for i in range(0, len(parts), 2):
            sep = parts[i + 1] if i + 1 < len(parts) else nl
            out.append((parts[i], sep))
    return out


# ---------------- 조사 보정 ----------------
_JOSA = r"(?:(?P<j>으로|를|을|는|은|가|이|와|과|로)(?![가-힣]))?"
_JOSA_PAIR = {"를": ("을", "를"), "을": ("을", "를"), "는": ("은", "는"), "은": ("은", "는"), "가": ("이", "가"),
              "이": ("이", "가"), "와": ("과", "와"), "과": ("과", "와")}


def _batchim(word):
    """마지막 글자 받침: None = 한글 아님, 0 = 없음, 8 = ㄹ, 그 밖 = 있음."""
    if not word or not ("가" <= word[-1] <= "힣"):
        return None
    return (ord(word[-1]) - 0xAC00) % 28


def _with_josa(value):
    """ID 바로 뒤에 붙은 조사를 대체어 받침에 맞게('DSC-01를' → '관련 사내 기준을')."""
    def repl(m):
        j = m.groupdict().get("j")
        if not j:
            return value
        b = _batchim(value)
        if b is None:
            return value + j
        if j in ("로", "으로"):
            return value + ("로" if b in (0, 8) else "으로")
        has, none = _JOSA_PAIR[j]
        return value + (has if b else none)
    return repl


def _spaced(term):
    return r"\s*".join(re.escape(c) for c in term if not c.isspace())


_SEC_SUFFIX = r"(?:\s?[#·]\s?s\d+|\s+s\d+)?(?:(?:·|, ?)s\d+)*"
_B_L, _B_R = r"(?<![A-Za-z0-9\-])", r"(?![A-Za-z0-9\-])"
_MARKER_ONLY = re.compile(r"[\s~\-·/()+,.:%]*")


class Compiled:
    def __init__(self, rules, alias_map, doc_ids=()):
        self.rules = rules
        rx = rules.get("regex") or {}
        self.as_of = dt.date.fromisoformat(rules["as_of"])
        self.alias = [(re.compile(_spaced(t)), a) for t, a in sorted(alias_map.items(), key=lambda x: -len(x[0]))]
        table = {**rules.get("id_map", {}), **rules.get("incident_map", {})}
        self.ids = [(re.compile(_B_L + re.escape(k) + _SEC_SUFFIX + _B_R + _JOSA), _with_josa(v))
                    for k, v in sorted(table.items(), key=lambda x: -len(x[0]))]
        self.ref_repl = _with_josa(rules.get("R8_replacement", "관련 사내 기준"))
        self.r8 = re.compile(rx["R8_docref"] + _JOSA) if rx.get("R8_docref") else None
        refs = sorted({d for d in doc_ids if d and d not in table}, key=len, reverse=True)
        self.refs = re.compile(_B_L + "(?:" + "|".join(map(re.escape, refs)) + ")" + _SEC_SUFFIX + _B_R + _JOSA) \
            if refs else None
        self.delete = [re.compile(p) for p in rx.get("R6_delete", [])]
        self.exempt = re.compile("|".join(map(re.escape, rx["R15_exempt_keywords"]))) if rx.get("R15_exempt_keywords") else None
        # 파일의 R3 정규식들을 **한 덩어리 alternation**으로 — 따로 돌리면 '1억5,000만 원'에서 뒤쪽 '5,000만 원'이
        # 먼저 바뀌어 '1억[금액]'이 남는다(가장 왼쪽에서 시작하는 금액 전체를 한 번에 바꿔야 함)
        self.money = [re.compile(r"(?<![\d.,])(?:" + "|".join(f"(?:{p})" for p in rx["R3_money"]) + ")")]             if rx.get("R3_money") else []
        # 감사 안전망: 파일 정규식의 뒤따름 제한 때문에 놓친 '억' 금액('1억~', '2억/')을 잔존으로 잡는다(바꾸지는 않음)
        self.money_strict = re.compile(r"(?<![\d.,A-Za-z])\d[\d,.]*\s?억(?![가-힣])")
        self.kw = re.compile("|".join(map(re.escape, rx["R4_keywords"]))) if rx.get("R4_keywords") else None
        self.nums = [(re.compile(r"(?<![\d.])" + p), MARKER["score"] if "점" in p else MARKER["ratio"])
                     for p in rx.get("R4_number", [])]
        self.comp = re.compile("|".join(COMP_WORDS))
        self.comp_num = re.compile(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?\s?(?:천만|백만|만|천)\s?원?"
                                   r"(?=$|[\s,.)·|~/:;\]\-]|으로|로|이|을|은|의|에|과|와|도|씩|대|정도|이하|이상|미만|초과|내외)")
        self.mm = re.compile(r"(?<![\d.])" + rx["R5_mm"]) if rx.get("R5_mm") else None
        self.scale = [re.compile(p) for p in rx.get("R5_scale", [])]
        dates = rx.get("R9_date", [])
        dates = [p for p in dates if "반기" in p or "분기" in p] + [p for p in dates if "반기" not in p and "분기" not in p]
        dates.append(r"20\d\d-\d{2}-\d{2}(?!\d)")         # ISO 날짜(엔진 기본 추가 — 말뭉치에 2027-03-.. 꼴이 있음)
        self.date = re.compile(r"(?<!\d)(?:" + "|".join(f"(?:{p})" for p in dates) + ")")
        self.drop_sent = [re.compile(d["pattern"]) for d in rules.get("drop_sentences", []) if d.get("pattern")]
        self.table_row_drop = bool(rules.get("table_row_drop"))
        dg = rules.get("density_guard") or {}
        self.density_max = float(dg.get("max_marked_sentence_ratio", 0) or 0)
        self.density_mode = dg.get("mode", "warn")
        self.markers = tuple(rules.get("placeholders") or internal_forbid.MASK_MARKERS)

    # --- 공통 ---
    @staticmethod
    def _sub(pat, repl, text, counts, key):
        new, n = pat.subn(repl, text)
        if n:
            counts[key] = counts.get(key, 0) + n
        return new

    def _future(self, s):
        m = re.match(r"(20\d\d)-(\d{2})-(\d{2})", s)
        if m:
            try:
                return dt.date(*map(int, m.groups())) > self.as_of
            except ValueError:
                return False
        y = int(s[:4])
        h = re.search(r"(상|하)반기", s)
        q = re.search(r"(\d)분기", s)
        mo = re.search(r"(\d{1,2})월", s)
        d = re.search(r"(\d{1,2})일", s)
        if h:
            return dt.date(y, 1 if h.group(1) == "상" else 7, 1) > self.as_of
        if q and 1 <= int(q.group(1)) <= 4:
            return dt.date(y, (int(q.group(1)) - 1) * 3 + 1, 1) > self.as_of
        if mo and d:
            try:
                return dt.date(y, int(mo.group(1)), int(d.group(1))) > self.as_of
            except ValueError:
                return False
        if mo:
            return (y, int(mo.group(1))) > (self.as_of.year, self.as_of.month)
        return y > self.as_of.year

    def _words(self, s, counts):
        for pat, a in self.alias:
            s = self._sub(pat, a, s, counts, "R1")
        for pat, v in self.ids:
            s = self._sub(pat, v, s, counts, "R7")
        if self.r8:
            s = self._sub(self.r8, self.ref_repl, s, counts, "R8")
        if self.refs:
            s = self._sub(self.refs, self.ref_repl, s, counts, "R8")
        for pat in self.delete:
            s = self._sub(pat, "", s, counts, "R6")
        return s

    def _sentence(self, s, counts):
        if not (self.exempt and self.exempt.search(s)):
            for pat in self.money:
                s = self._sub(pat, MARKER["money"], s, counts, "R3")
            if self.kw and self.kw.search(s):
                for pat, mk in self.nums:
                    s = self._sub(pat, mk, s, counts, "R4")
            if self.comp.search(s):
                s = self._sub(self.comp_num, MARKER["money"], s, counts, "R14")
        elif any(p.search(s) for p in self.money):
            counts["R15"] = counts.get("R15", 0) + 1
        if self.mm:
            s = self._sub(self.mm, MARKER["effort"], s, counts, "R5")
        for pat in self.scale:
            s = self._sub(pat, lambda m: re.sub(r"\d[\d,]*\s?\S*$", MARKER["scale"], m.group(0)), s, counts, "R5")

        def date_repl(m):
            if self._future(m.group(0)):
                counts["R9"] = counts.get("R9", 0) + 1
                return MARKER["schedule"]
            return m.group(0)
        return self.date.sub(date_repl, s)

    def _marker_only(self, cell):
        if not any(mk in cell for mk in self.markers):
            return False
        rest = cell
        for mk in self.markers:
            rest = rest.replace(mk, "")
        return _MARKER_ONLY.fullmatch(rest) is not None

    def _is_marker_row(self, line):
        cells = [c for c in line.strip().strip("|").split("|")]
        return len(cells) >= 2 and all(self._marker_only(c) for c in cells[1:])

    def _tables(self, s, counts):
        """R12: 첫 칸을 뺀 모든 칸이 표식뿐인 행 삭제 · 머리글·구분선만 남은 표는 표 삭제."""
        lines = s.split("\n")
        out, i = [], 0
        while i < len(lines):
            if not lines[i].lstrip().startswith("|"):
                out.append(lines[i]); i += 1
                continue
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            block = lines[i:j]
            sep = 1 if len(block) > 1 and re.fullmatch(r"\s*\|[\s:\-|]+\|\s*", block[1]) else None
            head = block[:2] if sep else block[:1]
            body = block[2:] if sep else block[1:]
            kept = [r for r in body if not self._is_marker_row(r)]
            counts["R12"] = counts.get("R12", 0) + (len(body) - len(kept))
            if body and not kept:
                counts["R12"] += 1                     # 표 전체 삭제
            else:
                out.extend(head + kept)
            i = j
        return "\n".join(out)

    def _drop_sentences(self, s, counts, extra=()):
        pats = self.drop_sent + list(extra)
        if not pats:
            return s
        out = []
        for sent, sep in sentences(s):
            if sent.strip() and any(p.search(sent) for p in pats):
                counts["R13"] = counts.get("R13", 0) + 1
                out.append("" if sep != "\n" else "\n")
            else:
                out.append(sent + sep)
        return "".join(out)

    def title(self, s, counts):
        return self._sentence(self._words(s or "", counts), counts) if s else s

    def text(self, s, counts, extra_drop=()):
        if not s:
            return s
        s = self._words(s, counts)
        s = "".join(self._sentence(sent, counts) + sep for sent, sep in sentences(s))
        if self.table_row_drop:
            s = self._tables(s, counts)
        s = self._drop_sentences(s, counts, extra_drop)
        return re.sub(r"\n{3,}", "\n\n", s)

    # --- 감사용 ---
    def marked_ratio(self, chunk_text):
        """R16 — (표식이 든 문장 수) / (문장 수). 조각 머리글 첫 줄은 빼고 센다."""
        body = chunk_text.split("\n", 1)[1] if chunk_text.startswith("# ") and "\n" in chunk_text else chunk_text
        sents = [x for x, _ in sentences(body) if x.strip() and not re.fullmatch(r"\s*\|[\s:\-|]+\|\s*", x)]
        if not sents:
            return 0.0
        return sum(1 for x in sents if any(mk in x for mk in self.markers)) / len(sents)

    def residual(self, s):
        """마스킹 뒤에도 남은 규칙 위반 종류(audit용): money · id · ref · contact · r12 · r13."""
        s = s or ""
        out = set()
        if any(p.search(s) for p, _ in self.ids):
            out.add("id")
        if self.refs and self.refs.search(s):
            out.add("ref")
        if any(p.search(s) for p in self.delete):
            out.add("contact")
        for sent, _ in sentences(s):
            if not (self.exempt and self.exempt.search(sent)) and                     (any(p.search(sent) for p in self.money) or self.money_strict.search(sent)):
                out.add("money")
            if self.drop_sent and any(p.search(sent) for p in self.drop_sent):
                out.add("r13")
            if self.table_row_drop and sent.lstrip().startswith("|") and self._is_marker_row(sent) \
                    and not re.fullmatch(r"\s*\|[\s:\-|]+\|\s*", sent):
                out.add("r12")
        return sorted(out)


# ---------------- 사본 만들기 ----------------
def mask_doc(doc, comp, num, rules_hash, cust_hash, spec=None, counts=None):
    """원본 문서 1개 → 마스킹 가상 문서(새 dict). 원본은 바꾸지 않는다.
    drop_sections의 idx(1부터)·title이 원본과 안 맞으면 m['drop_mismatch']에 남긴다(호출 쪽이 사본을 버림)."""
    counts = {} if counts is None else counts
    spec = spec or {}
    extra = [re.compile(d["pattern"]) for d in spec.get("drop_sentences", []) if d.get("pattern")]
    m = copy.deepcopy(doc)
    m.update({"doc_id": msk_id(num), "disclosure": "public", "masked": True, "source_doc_id": doc["doc_id"],
              "doc_title": comp.title(doc["doc_title"], counts),
              "doc_hash": hashlib.sha256(f"{doc['doc_hash']}|{rules_hash}|{cust_hash}".encode()).hexdigest(),
              "extra_meta": {"masked": True, "disclosure_src": doc["disclosure"],
                             "mask_rules_version": comp.rules["rules_version"]},
              "dropped_titles": [], "drop_mismatch": [],
              "density": {"max": comp.density_max, "mode": comp.density_mode}})
    drop_idx = {}
    for ds in spec.get("drop_sections", []):
        i = ds["idx"] - 1
        if 0 <= i < len(doc["sections"]) and doc["sections"][i]["sec_title"] == ds["title"]:
            drop_idx[i] = ds["title"]
        else:
            m["drop_mismatch"].append(ds["idx"])
    for si, sec in enumerate(m["sections"]):
        sec["forbid"] = []
        sec["meta"] = {k: v for k, v in (sec.get("meta") or {}).items() if k != "related"}
        title = comp.title(sec["sec_title"], counts)
        if si in drop_idx:
            sec["body"] = []                          # 본문만 비움 → 섹션 순번 유지
            counts["R10"] = counts.get("R10", 0) + 1
            m["dropped_titles"].append(title)
        else:
            sec["body"] = comp.text("\n".join(sec["body"]), counts, extra).split("\n")
        sec["sec_title"] = title
    return m


def build(docs, docs_dir, rules=None, rules_hash=None):
    """원본 문서 목록 → (사본 목록, 상태 dict). 규칙 파일 오류는 MaskRulesError로 올린다.
    상태: missing(규칙에 있는데 원본에 없는 doc_id) · unlisted(규칙에 없는 비공개 문서 — 사본 안 만듦) ·
          drop_mismatch(idx·title 불일치로 사본 안 만듦) · counts(규칙별 건수) · publish(사본이 기대하는 파일 번호)"""
    if rules is None:
        rules, rules_hash = load_rules(docs_dir)
    originals = [d for d in docs if not d.get("masked")]
    cust = customer_terms(originals)
    prefix = (rules.get("alias") or {}).get("customer_prefix", "고객사")
    comp = Compiled(rules, aliases(cust, prefix), [d["doc_id"] for d in originals])
    by_id = {d["doc_id"]: d for d in originals}
    out, counts, missing, mismatch = [], {}, [], []
    for did in sorted(rules["publish"]):
        d = by_id.get(did)
        num = file_num(d["path"]) if d else None
        if d is None or num is None:
            missing.append(did)
            continue
        c = {}
        m = mask_doc(d, comp, num, rules_hash, list_hash(cust), rules["publish"][did] or {}, c)
        if m["drop_mismatch"]:
            # 원본이 고쳐져 순번이 밀렸을 수 있다 → 엉뚱한 섹션을 공개하지 않게 이 사본은 만들지 않는다(8.4)
            mismatch.append(did)
            continue
        m["mask_counts"] = c
        out.append(m)
        for k, v in c.items():
            counts[k] = counts.get(k, 0) + v
    unlisted = sorted(d["doc_id"] for d in originals if d["disclosure"] != "public" and d["doc_id"] not in rules["publish"])
    return out, {"ok": True, "rules_version": rules["rules_version"], "counts": counts, "missing": missing,
                 "unlisted": unlisted, "drop_mismatch": mismatch,
                 "publish": {m["doc_id"][len(MSK_PREFIX):] for m in out} | {file_num(by_id[k]["path"]) for k in rules["publish"]
                                                                             if k in by_id},
                 "customers": len(cust), "compiled": comp}


REGION_RE = re.compile(r"서울|경기|인천|부산|대구|광주|대전|울산|수도권|지방|전국|업계|순위|최대|대형|선두")


def reid_candidates(mdoc, comp):
    """R2 사람 검토 후보 — 가명과 지역·업계 순위·규모 단서가 한 문장에 같이 있는 곳의 **위치만**
    [(섹션 순번, 문장 순번)]. 문장 원문은 돌려주지 않는다(B-08)."""
    out = []
    for si, sec in enumerate(mdoc["sections"]):
        for ji, (sent, _) in enumerate(sentences("\n".join(sec["body"]))):
            if internal_forbid.PSEUDONYM_RE.search(sent) and (REGION_RE.search(sent) or MARKER["scale"] in sent):
                out.append((si, ji))
    return out
