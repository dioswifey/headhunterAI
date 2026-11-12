"""리포트 문장 검사 (2026-11-12, 리포트 품질 측정 M-38b·M-40·M-41 후속).

AI가 쓴 리포트 문장(evaluation.summarize의 강점·개선점·총평, crew/report_coach의 코치 코멘트·연습 과제)에 공통으로 쓴다.
- 금지어: 합불 언급·인적 특성
- 수치: 두 자리 이상 숫자는 입력에 **숫자 토큰 그대로** 있어야 한다(부분 문자열 금지 — "10"이 "100.0"에 걸리지 않게)
- 따옴표 구절: 답변 원문에 없는 '인용'은 따옴표를 없앤다
- 점수-서술 일치: 강점은 상위 2개 항목, 개선점은 하위 2개 항목(동점 포함)
점수·합불은 만들거나 바꾸지 않는다(evaluation.compute 그대로).
"""
import re

FORBIDDEN = re.compile(r"합격|불합격|합불|탈락|통과\s*여부|나이|성별|외모|학벌|출신")
NUM = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])")
QUOTED = re.compile(r"[\"“'‘]([^\"”'’]{2,200})[\"”'’]")


def _norm_num(s):
    s = s.replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def number_tokens(*sources):
    """입력 글·숫자에서 허용할 수치 집합. 소수는 정수 반올림 형태도 허용(예: 77.5 → 78, 72.0 → 72)."""
    out = set()
    for src in sources:
        for tok in NUM.findall(str(src)):
            n = _norm_num(tok)
            out.add(n)
            try:
                out.add(str(round(float(n))))
            except ValueError:
                pass
    return out


def invented_numbers(text, allowed):
    """입력에 없는 두 자리 이상 숫자 목록."""
    bad = []
    for tok in NUM.findall(text or ""):
        n = _norm_num(tok)
        if len(n.replace(".", "")) >= 2 and n not in allowed:
            bad.append(tok)
    return bad


# 지시문 누출: 프롬프트의 형식 지시("2문장", "1~3개", "중요도 순")가 그대로 나오거나, 문장 대신 '항목: 숫자'만 나열한 경우
LEAK_INSTR = re.compile(r"^\s*\d+\s*(?:~\s*\d+\s*)?(?:문장|개)\s*\.?\s*$|중요도\s*순|\d+\s*~\s*\d+\s*개\s*\(|^\s*(?:총평|강점|개선점)\s*:?\s*$")
LEAK_NUMLIST = re.compile(r"^\s*[^\d:：]{1,20}\s*[:：]\s*\d+(?:\.\d+)?\s*점?\s*\.?\s*$")


def leaked(texts):
    """지시문 누출·숫자만 나열한 문장이 있으면 True. 너무 짧은 문장(공백 빼고 8자 미만)도 누출로 본다."""
    for t in texts:
        t = (t or "").strip()
        if LEAK_INSTR.search(t) or LEAK_NUMLIST.match(t) or len(re.sub(r"\s+", "", t)) < 8:
            return True
    return False


def check_text(texts, allowed):
    """사유 코드 하나(문제 없으면 None): forbidden_word | invented_number | instruction_leak."""
    joined = " ".join(t or "" for t in texts)
    if FORBIDDEN.search(joined):
        return "forbidden_word"
    if invented_numbers(joined, allowed):
        return "invented_number"
    if leaked(texts):
        return "instruction_leak"
    return None


def _ws(s):
    return re.sub(r"\s+", "", s or "")


def strip_bad_quotes(text, source):
    """따옴표 안 구절이 원문(source)에 공백 무시 부분 문자열로 없으면 따옴표만 없앤다(evaluation.quote_ok와 같은 비교)."""
    src = _ws(source)

    def fix(m):
        inner = m.group(1)
        q = _ws(inner)
        return m.group(0) if len(q) >= 4 and q in src else inner
    return QUOTED.sub(fix, text or "")


def top_bottom(item_scores, k=2):
    """상위 k·하위 k 항목 집합(동점 포함). 항목이 k개 이하이면 전부."""
    if not item_scores:
        return set(), set()
    vals = sorted(item_scores.values(), reverse=True)
    if len(vals) <= k:
        return set(item_scores), set(item_scores)
    top_cut, bot_cut = vals[k - 1], vals[-k]
    return ({it for it, v in item_scores.items() if v >= top_cut},
            {it for it, v in item_scores.items() if v <= bot_cut})


def is_tie(item_scores, k=2):
    """'동점': 항목 점수가 전부 같거나, 상위 k·하위 k 집합이 겹쳐 강점·개선점을 항목 비교로 가를 수 없을 때."""
    if not item_scores:
        return True
    vals = list(item_scores.values())
    if max(vals) - min(vals) < 1e-9:
        return True
    top, bottom = top_bottom(item_scores, k)
    return bool(top & bottom)
