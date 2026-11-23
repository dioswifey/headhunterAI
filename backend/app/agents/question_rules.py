"""질문 문장 품질 규칙 — 정규식만, 모델·DB·Chroma 호출 없음 (PRD v0.16 QST-20 · TRD v0.11 §6.9).

2026-11-20 분리·수리. 같은 규칙을 세 곳이 함께 쓴다:
  · A18 사내문서 질문 검증  `internal_question_agent.validate()`
  · 이력서 질문(면접 중 생성)  `questions.make_resume_question()`
  · 꼬리질문 문장 검사       `followup_agent.readable()`
전에는 규칙이 `internal_question_agent` 안에만 있어 이력서·꼬리질문 경로가 같은 비문을 그대로 냈다.

2026-11-20 재검증(`documents/03_QA_doc/eval_runs/iq_audit_2026-11-20/`)에서 찾은 결함을 여기서 고쳤다:
  ① `ABSTRACT_HEAD`·`TWO_CONCEPTS` 끝에 제어문자 0x08이 들어가 두 검사가 한 번도 걸리지 않았다 → `\\b`
  ② 따옴표 사내 용어 검사에 곡선 따옴표(‘’“”)가 없었다
  ③ "어떤 X를 확인하고, 어떻게 Y할지"처럼 연결 어미로 이은 두 개 묻기를 못 잡았다 → `MULTI_INTERROG`
  ④ '~(으)면' 조건문을 '상황 없음'으로 떨어뜨렸다 → `SITUATION_MARKERS`에 추가
  ⑤ 사내문서 질문에도 음성 조각 같은 "수요예: 예측…"이 있었다 → `FRAGMENT_COLON`
  ⑥ "이 회사에서 …", "헤드헌터랩 …"처럼 회사 내부 사정을 안다고 전제하는 표현 → `HOUSE_PREMISE`
  ⑦ `TWO_CONCEPTS`를 그대로 켜면 절반이 거짓양성(사람 짝 · 구분/비교 관계) → `two_concepts()`로 좁힘
"""
import re

# 허용 덧붙임(사용자 허용 문구) — 검사 전에 떼어낸다. 두 가지이고 순서는 상관없다:
#  ① "관련된/비슷한 경험이 있다면 ~ 답해 주셔도 좋습니다"
#  ② (2026-11-20 사용자 응시 피드백 F1) 본질문의 **답변 형식 예시 1개**:
#     "예를 들어 'RAG를 써서 ~를 자동화했고, 그 결과 ~에 이런 영향을 미쳤다'처럼 설명해 주세요."
#     꼬리질문의 힌트 금지(followup_agent)와는 별개 — 본질문은 '어떤 모양으로 답하면 되는지'를 보여 줘도 된다.
EXPERIENCE_SUFFIX = re.compile(r"[,.]?\s*(?:관련된?|비슷한|유사한)\s*(?:업무\s*)?경험[^.?!]{0,20}있다면[^.?!]*(?:답해|말씀해|설명해)\s*주(?:셔도|시면)\s*좋습니다\.?\s*$")
EXAMPLE_SUFFIX = re.compile(r"[,.]?\s*예를\s*들(?:어|면)[,\s]*['\"‘“][^'\"’”]{2,90}['\"’”]\s*(?:처럼|와\s*같이|같은\s*식으로|식으로)\s*"
                            r"(?:답해|설명해|말씀해)\s*주(?:세요|셔도\s*좋습니다|시면\s*됩니다|시면\s*좋습니다)\.?\s*$")


def core(q):
    """허용 덧붙임(경험 안내 · 답변 형식 예시)을 떼어 낸 본문. 한 문장·길이·문장 규칙은 이 본문으로 본다."""
    t = (q or "").strip()
    for _ in range(2):                                   # 두 덧붙임이 어느 순서로 붙어도 둘 다 뗀다
        t = EXAMPLE_SUFFIX.sub("", EXPERIENCE_SUFFIX.sub("", t)).strip()
    return t

# ── 비문 ───────────────────────────────────────────────────────────────
# ① 물음표·종결 요청이 2개 이상. "…하시겠습니까?"처럼 같이 붙는 경우를 두 번 세지 않게 "?"가 뒤따르지 않는 어미만 센다.
ASK_END = re.compile(r"\?|(?:하시겠습니까|할까요|해\s*주세요|해주세요|주시겠습니까|바랍니다|부탁드립니다|인가요|나요|입니까)(?!\s*\?)")
# ①-2 연결 어미(…하고 · …하며 · …는지/할지)를 사이에 두고 의문사가 앞뒤로 하나씩 = 두 개 묻기
INTERROG = r"(?:어떻게|무엇|어떤|왜|얼마나|어느|몇)"
_CONNECT = r"(?:[가-힣](?:고|며)|(?:는|은|인|할|될|을|일|볼|줄)지)(?:\s*,\s*|\s+)"
MULTI_INTERROG = re.compile(INTERROG + r"[^?]*?" + _CONNECT + r"[^?]*?" + INTERROG)
# ② 선행사 없이 등장하는 지시어
DANGLING_REF = re.compile(r"그\s*결과물|이\s*과정|해당\s*부분|그것을|그\s*부분|이\s*결과(?!물)|그\s*결과(?!물)|해당\s*내용|이\s*내용|그\s*내용")
# ③ 따옴표로 감싼, 설명 없는 사내 단계·절차 명사구(예: '적용 가능성 검토'). 곧은·곡선·낫표 따옴표 모두
QUOTED_PHRASE = re.compile(r"['\"‘“「『]([^'\"‘’“”「」『』]{2,16})['\"’”」』]")
QUOTED_SENTENCE_END = re.compile(r"(다|요|까|라|네|죠)[.!?]?$")
# ④ 음성 조각·잘린 낱말 흔적: 머리에 짧은 조각 + 콜론("수요예: …") · 한글 낱말 사이 콜론("데이터베이:스")
FRAGMENT_COLON = re.compile(r"^\s*[^,.?!]{1,12}\s*[:：]\s*|[가-힣]\s*[:：]\s*[가-힣]")
# ⑤ 회사 내부 규정·기준을 안다고 전제(지원자는 이 회사의 규정을 모른다 — QST-20 ③ · 생성 지시문 6-6)
#    2026-11-20 정정(사용자 응시 피드백 F9): **회사 이름 자체는 막지 않는다** — "{회사}에서 ~를 맡는다면 / 진행한다고
#    가정할 때"(가정형 미래)는 사용자가 원한 형태다(F1·F3). 막는 것은 ⓐ 내부 규정·기준을 안다고 전제 ⓑ 아래 ⑥.
HOUSE_PREMISE = re.compile(r"이\s*회사의\s*(?:규정|기준|절차|지침|정책|원칙|방침|행동\s*기준)|당사|우리\s*회사|저희\s*회사|"
                           r"중요하게\s*(?:보는|여기는)\s*(?:행동\s*)?기준|회사\s*내부\s*(?:규정|기준|절차)|"
                           r"이\s*회사에서\s*(?:어떤|무슨)\s*(?:기준|절차|규정|방식)으로")
# ⑥ [매우 심각, F9] 지원자가 **그 회사에서 일했다고** 전제 — 회사 이름 + 과거형 경험 서술
#    예: "헤드헌터랩_경기에서 … 비전 모델 개발 업무를 맡아, … 어떤 접근을 취했는지 설명해 주세요."
#    회사 언급과 과거형 맺음 사이에 가정 표지(~다면·~라면·가정·~한다고)가 있으면 가정형이므로 통과.
COMPANY_MENTION = r"(?:헤드헌터랩[^\s,]*|\{회사\}|이\s*회사|귀사|지원(?:하신|한)\s*회사)"
# 2026-11-20 지원 20 점검: "~해결해 본 적이 있나요"·"(회사)의 … 경험을 바탕으로"도 그 회사 경력 전제다
_PAST_END = r"(?:(?:었|았|했|였|셨|됐|겪었|거쳤)(?:는지|나요|습니까|던|을\s*때)|본\s*적이?\s*있|해\s*봤|경험(?:을|이|과|에|으로|은))"
_HYPO = r"(?!다면|라면|가정|한다고|맡는다면)"
COMPANY_PAST = re.compile(COMPANY_MENTION + r"(?:" + _HYPO + r"[^?])*?" + _PAST_END)


def company_past(q, company=None):
    """F9: 회사 이름(일반 표현 + 실제 지원 회사 이름) 뒤에 가정 표지 없이 과거 경험이 이어지는가. 허용 덧붙임은 떼고 본다."""
    c = core(q)
    if COMPANY_PAST.search(c):
        return True
    if company and company.strip():
        pat = re.escape(company.strip()) + r"[^\s,]*(?:" + _HYPO + r"[^?])*?" + _PAST_END
        return bool(re.search(pat, c))
    return False


def company_project_mix(q, company=None):
    """이력서 질문의 이중 장소 — '(회사)에서 (이력서 프로젝트)에서 … 다시 맡는다면'처럼 이력서 일을 그 회사에서 한 것처럼 읽힌다."""
    names = [r"헤드헌터랩\S*?", r"\{회사\}"] + ([re.escape(company.strip())] if company and company.strip() else [])
    return bool(re.search(r"(?:" + "|".join(names) + r")에서\s+[^,?]{2,40}?에서(?=[\s,])", core(q)))
# ⑦ 깨진 낱말(F8): 받침 없는 음절 + '을' / 받침 있는 음절 + '를' ("AI/AX 기술스을" — 낱말이 잘리고 조사만 남은 흔적)
_JOSA_WORD = re.compile(r"([가-힣]+)(을|를)(?=[\s,.?!]|$)")
_NOT_BROKEN = {"가을", "마을", "고을", "이을", "지을", "나을", "부을", "그을", "저을", "다을", "보을"}


def _has_batchim(ch):
    return (ord(ch) - 0xAC00) % 28 != 0


def broken_word(q):
    """조사 결합이 깨진 낱말. 없으면 ''. 예: '기술스을'(스 + 을)."""
    for m in _JOSA_WORD.finditer(q or ""):
        stem, josa = m.group(1), m.group(2)
        if (stem + josa) in _NOT_BROKEN or len(stem) < 1:
            continue
        last = stem[-1]
        if (josa == "을" and not _has_batchim(last)) or (josa == "를" and _has_batchim(last)):
            return stem + josa
    return ""

# ── 상황 표현 ─────────────────────────────────────────────────────────
# "다면"은 '~라면/한다면/있다면' 등을 통째로 잡는다. 2026-11-20: '~(으)면' 조건("차이가 나면,", "발생하면 ")도 인정한다
# (화면·측면·장면 같은 명사는 제외).
SITUATION_MARKERS = re.compile(r"(다면|상황|경우|때|진행\s*중|직면|마주|(?:(?![화측장단서대전후내외국방표정])[가-힣])면(?=[\s,]))")

# ── 문장 틀(규칙 6-3 · 6-4 · 6-5) ─────────────────────────────────────
# 추상 범주 이름만 있는 상황("LLM 응용에서", "운영 업무에서")
ABSTRACT_HEAD = re.compile(
    r"^\s*(?:[가-힣A-Za-z0-9·/_{}\s]{0,24})?"
    r"(?:LLM|AI|RAG|ML|데이터|모델|서비스|시스템|운영|업무|기술|분석|개발|프로젝트)\s*"
    r"(?:응용|활용|처리|관리|구축|운영|업무|과정|환경|영역|분야)\s*(?:에서|에서는|에선)\b")
# '와/과'로 묶인 두 명사구 + 바로 뒤 조사. 그대로 쓰면 거짓양성이 절반이라(2026-11-20 실측) two_concepts()가 거른다.
TWO_CONCEPTS = re.compile(r"([가-힣]{2,}(?:\s*(?:여부|성|도|율|량|감))?)\s*(?:와|과)\s+([가-힣]{2,}(?:\s*(?:여부|성|도|율|량|감))?)\s*(?:을|를|의|이|가)\b")
# 사람·조직 짝("기안자와 팀장이", "직속상사와 구성원이")은 두 개념이 아니다
_PERSON_END = re.compile(r"(?:자|원|장|님|팀|객|인|상사|동료|벤더|파트너|업체|기관|부서|고객사)$")
_NOT_PERSON = {"원인", "요인", "확인", "승인", "자원", "시장", "주장", "성장", "확장", "보장", "현장", "인장"}
# 둘의 관계·대비를 묻는 한 과제("사실과 의견을 구분", "예산과 원가를 비교", "A와 B의 차이")
_RELATION = re.compile(r"구분|비교|차이|일치|연결|관계|충돌|혼동|중복|사이|대비|균형|동시에|모두|함께|간의|상충|조율|위해|위한|"
                       r"맞추|맞춰|대조|격차|어긋|다를|다른|달라|달랐|연관|상관|교환|트레이드오프")
# 목적어 없이 맨 '어떻게 하시겠습니까'로 끝나는 맺음
BARE_ASK = re.compile(r"(?:,|\s)\s*어떻게\s*(?:하|대응하|처리하)시겠습니까\s*\?*\s*$")

# ⑨ 문장 호응 깨짐(2026-11-23 사용자 승인) — 의문사로 묻기 시작해 **명령형·청유형으로 끝나는** 문장.
# 실제로 나간 문장(Redis 은행 원문): "현장에서 불량 기준이 모호할 때, **어떻게** 데이터 수집과 라벨링 기준을
# 정의하고 모델 성능을 **개선해 주세요.**" — 묻는 말과 맺는 말이 어긋난다.
#
# 오탐을 막는 것이 이 규칙의 핵심이다. 아래 둘은 **정상**이고 반드시 통과해야 한다:
#   · "…무엇을 확인하시는지 **설명해 주세요**"      — 의문사 + '~는지/~은지'로 안긴 문장 + 설명 요청
#   · "예를 들어 '…'처럼 **설명해 주세요**"          — 인성 질문의 답변 형식 예시(8문항의 뼈대)
# 그래서 ⓐ 맺는 동사를 **설명·답변·기술 요청 동사가 아닌 것**으로 한정하고(설명/답변/말씀/기술/작성 제외),
# ⓑ 의문사와 맺음 사이에 `~는지·~은지·~한지·~ㄹ지`(안긴 의문문 표지)가 **없을** 때만 걸린다.
_WH = r"(?:어떻게|무엇을|무엇이|왜|어떤\s*방법으로)"
_EMBEDDED_Q = r"(?:지|나요|습니까|가요)(?=\s|,|$)"      # '~는지·~할지·~을지' 등 안긴 의문문 표지
# 맺는 동사는 **낱말 시작**에서만 본다(앞이 공백·쉼표·가운뎃점). 이 경계가 없으면 '설명해 주세요'의 '명해 주세요'가
# 걸려 정상 문장이 탈락한다(2026-11-23 실측 오탐 2건: "어떻게 감지하고 대응할지 설명해 주세요" 등).
_IMPERATIVE_TAIL = (r"(?:^|[\s,·])(?!설명|답변|말씀|기술|작성|소개|공유)[가-힣]{1,10}"
                    r"(?:해\s*주세요|해\s*주시기\s*바랍니다|하십시오|하세요|해\s*보세요|해\s*주십시오)")
MALFORMED_ASK = re.compile(_WH + r"(?:(?!" + _EMBEDDED_Q + r")[\s\S])*?" + _IMPERATIVE_TAIL + r"\s*\.?\s*$")


def malformed_ask(text):
    """⑨ 의문사로 묻고 명령형으로 맺는 문장인가(bool). 허용 덧붙임은 떼고 본다."""
    return bool(MALFORMED_ASK.search(core(text or "")))


# F11(2026-11-23 사용자 지적) — 지원 회사 이름 + '다시'. 가정형이어도 **그 회사에서 예전에 해 봤다**는 뜻이 된다.
#   ✗ "헤드헌터랩_경기에서 소비기한 인쇄 불량 검사를 **다시 맡는다면**"  (그 검사는 이전 회사에서 한 일이다)
#   ○ "다온팩토리솔루션에서 하신 소비기한 인쇄 불량 검사를, 헤드헌터랩_경기에서 같은 문제로 마주한다면"
# `company_past`(명시적 과거형)는 이걸 못 잡는다 — 가정형이기 때문이다.
# 뒤따르는 동사는 **업무를 떠맡는 동작**만 본다. 단독 '하|해'를 넣었다가 "고객이 **다시 묻는다면** … **이해**를
# 돕기 위해"의 '해'가 걸려 정상 질문이 탈락했다(2026-11-23 6차 재측정 오탐 1건). '다시 묻는다·다시 설명한다'처럼
# 지원자의 근무 이력과 무관한 '다시'는 걸리지 않아야 한다.
AGAIN = re.compile(r"(?:다시|재차|또다시)\s*[^.?!]{0,16}?(?:맡|진행|검증|담당|적용|수행|구축|개발|운영)")

# F13(2026-11-23 사용자 지적) — **추상 명사구를 겹쳐 쓴 문장**. 사용자가 "무슨 문장이 이렇게 어려워? 가독성이?"라고
# 지적한 Q8이 그 예다: "검증 결과가 **누락되지 않도록 기록하는 업무**를 맡는다면, 검증 증거를 **남기는 업무 방식**의
# 어떤 점이…" — `~하는 + 일반 명사`(업무·방식·과정·작업·활동·방안) 꼴을 두 번 이어 붙여 무엇을 묻는지 흐려진다.
# 한 번 쓰는 것은 정상이다(실측: 기업 질문 251개 중 '~하는 업무를 맡는다면'이 44개 = 18%, 그 자체로는 문제가 아니다).
# **두 번 이상** 겹칠 때만 걸러 오탐을 피한다.
_ABS_NOUN = r"(?:업무|방식|방법|과정|작업|활동|방안|체계|절차)"
NESTED_ABSTRACT = re.compile(r"[가-힣]{2,}는\s*" + _ABS_NOUN)


def nested_abstract(text, limit=2):
    """F13: `~하는 업무/방식/과정` 꼴이 `limit`번 이상 겹쳤는가(bool). 허용 덧붙임은 떼고 본다."""
    return len(NESTED_ABSTRACT.findall(core(text or ""))) >= limit


def company_again(text, company="", prev_names=()):
    """F11: 회사 이름과 '다시'가 같은 문장에 있으면 사유, 아니면 "".

    **허용**: 같은 문장에 이전 회사·프로젝트 이름(`prev_names`)이 함께 있으면 어디서 한 일인지 밝힌 것이므로 통과.
    회사 이름을 모르면(`company`가 비면) 판정하지 않는다 — 템플릿의 '지원하신 회사'처럼 이름이 없는 경우까지
    걸면 정상 문장이 떨어진다.
    """
    t = (text or "")
    comp = (company or "").strip()
    if not comp or comp not in t:
        return ""
    for sent in re.split(r"(?<=[.?!])\s+", t):
        if comp in sent and AGAIN.search(sent):
            if any(n and n.strip() and n.strip() != comp and n.strip() in sent for n in prev_names):
                continue                      # 이전 회사·프로젝트를 밝혔다 → 허용
            return "company_again"
    return ""

# ── 이력서 질문 ───────────────────────────────────────────────────────
# 이력서의 수치·성과를 질문 앞에서 결론처럼 확정("…60% 줄인 경험을 설명해 주세요", "30% 개선하신 경험을…")
NUMERIC_CONCLUSION = re.compile(r"\d[\d,.]*\s*(?:%|배|건|명|개|초|ms|분|시간|일|주|개월|년|만|천|억|원|점|%p|p)?"
                                r"[^?]{0,60}?(?:한|하신|했던|된|낸|린|춘|인)\s*(?:경험|사례|과정|성과|결과)(?:을|를|에|이|가)")


def _person(noun):
    n = re.sub(r"\s+", "", noun)
    return n not in _NOT_PERSON and bool(_PERSON_END.search(n))


def two_concepts(q):
    """서로 다른 개념 두 개를 '와/과'로 묶어 묻는 곳을 찾는다. 없으면 ''.
    제외: 사람·조직 짝 · 둘의 관계·대비를 묻는 한 과제(구분·비교·차이·일치·충돌…)."""
    for m in TWO_CONCEPTS.finditer(q):
        a, b = m.group(1), m.group(2)
        if _person(a) or _person(b):
            continue
        tail = q[m.end(): m.end() + 16]
        if _RELATION.search(m.group(0)) or _RELATION.search(tail.split(",")[0].split("?")[0]):
            continue
        return m.group(0)
    return ""


def end_punct(t):
    """질문 끝 문장부호를 맞춘다(2026-11-20 지원 20의 3번이 마침표 없이 끝남). 이미 . ? ! 로 끝나면 그대로,
    '~니까/~까요/~나요'처럼 묻는 어미면 '?', 그 밖('~주세요/~좋습니다')은 '.'."""
    t = (t or "").rstrip()
    if not t or t[-1] in ".?!":
        return t
    return t + ("?" if re.search(r"(?:까|까요|나요|니까|인가요|습니까)$", t) else ".")


def has_malformed_phrasing(q, quotes=True, company=None):
    """(ok, reason). 비문 — 두 개 묻기 · 지시어 · 음성 조각 · 깨진 낱말 · 회사 내부 규정 전제 · 회사 경력 전제 · 따옴표 사내 용어.
    허용 덧붙임(경험 안내 · 답변 형식 예시)은 떼고 본다(core).
    quotes=False: 이력서 질문처럼 지원자 자신의 프로젝트 이름을 따옴표로 짚는 경우 따옴표 검사를 건너뛴다."""
    stripped = core(q)
    if len(ASK_END.findall(stripped)) >= 2:
        return False, "multi_ask"
    if MULTI_INTERROG.search(stripped):
        return False, "multi_interrog"
    if DANGLING_REF.search(stripped):
        return False, "dangling_ref"
    if FRAGMENT_COLON.search(stripped):
        return False, "fragment_colon"
    bw = broken_word(q or "")
    if bw:
        return False, "broken_word:" + bw
    if company_past(q, company):
        return False, "company_past"                     # [매우 심각] 그 회사에서 일했다고 전제(F9)
    if HOUSE_PREMISE.search(stripped):
        return False, "house_premise"
    if quotes:
        for m in QUOTED_PHRASE.finditer(stripped):
            phrase = m.group(1).strip()
            if phrase and not QUOTED_SENTENCE_END.search(phrase):
                return False, "quoted_internal_term:" + phrase[:20]
    return True, ""


def sentence_shape(q):
    """(ok, reason). 문장 틀 — 추상 범주 상황 · 개념 두 개 묶기 · 목적어 없는 맺음. 허용 덧붙임은 떼고 본다."""
    c = core(q)
    if ABSTRACT_HEAD.search(c):
        return False, "abstract_head"
    if two_concepts(c):
        return False, "two_concepts"
    if BARE_ASK.search(c):
        return False, "bare_ask"
    return True, ""


# F2(사용자 응시 피드백): "비전문가에게 기술적인 내용을 설명해야 했던 경험과 그때 쓴 방법을 설명해 주세요"처럼
# **장면 없이 '~했던 경험'만** 묻는 질문. 사내문서 질문은 구체 장면을 주는 가정형이어야 한다(누가·어떤 상황·누구에게).
ABSTRACT_EXPERIENCE = re.compile(r"(?:했던|하셨던|해\s*본|해\s*봤던|겪었던|겪은|있었던)\s*경험(?:과|을|이|에\s*대해|은)")


def interview_issue(q, company=None):
    """면접 중 다시 쓴 질문(A6 수정 · 은행 문항 회사 맞춤 등)의 문장 문제. 없으면 ''. 따옴표 검사는 하지 않는다."""
    ok, why = has_malformed_phrasing(q, quotes=False, company=company)
    if not ok:
        return why
    ok, why = sentence_shape(q)
    if not ok:
        return "shape:" + why
    return ""


def resume_issue(q, company=None, prev_names=()):
    """이력서 질문(면접 중 생성)의 문장 문제. 없으면 ''.
    면접 중 문장 규칙 + 수치 결론형. 지원자 자신의 프로젝트 이름은 따옴표로 짚어도 된다.
    `prev_names`(2026-11-23 F11) = 이전 회사·프로젝트 이름 — 이 이름이 같은 문장에 있으면 '다시'를 허용한다."""
    why = interview_issue(q, company)
    if why:
        return why
    if company_project_mix(q, company):
        return "company_past:project_mix"              # F9 계열 — 이력서 프로젝트를 그 회사에서 한 것처럼 읽힘
    again = company_again(q, company, prev_names)      # F11 — 회사 이름 + '다시'(가정형이어도 근무 이력 전제)
    if again:
        return again
    if NUMERIC_CONCLUSION.search(core(q)):
        return "numeric_conclusion"
    return ""


# ── 주제·전제 비교용 내용어(F4 같은 주제 반복 · F6 꼬리질문 전제 · F7 되풀이) ─────────────
_JOSA_TAIL = sorted(["으로서", "으로써", "에서는", "에게서", "이라면", "한다면", "했다면", "으로", "에서", "에게", "까지", "부터",
                     "처럼", "보다", "이나", "이란", "라는", "이라는", "하고", "하며", "하는", "했던", "하신", "한", "할", "된", "인",
                     "을", "를", "이", "가", "은", "는", "의", "에", "로", "과", "와", "도", "만", "나"], key=len, reverse=True)
STOP_TERMS = {
    "어떻게", "무엇", "어떤", "어느", "얼마나", "구체적", "구체적으로", "설명", "말씀", "방법", "방식", "과정", "경우", "상황",
    "결과", "수치", "사례", "예시", "부분", "내용", "문제", "해결", "경험", "생각", "판단", "기준", "이유", "답변", "질문",
    "지원자", "본인", "당시", "실제", "실제로", "정확히", "주로", "가장", "먼저", "다시", "추가", "관련", "해당", "이번",
    "알려", "주세요", "주실", "있나요", "인가요", "하시겠습니까", "했나요", "있습니까", "업무", "프로젝트", "진행",
    "적용", "사용", "활용", "확인", "처리", "수행", "개선", "효과", "영향", "중요", "필요", "가능", "정도", "어떤가요",
    "구체적", "수치적", "정량적", "정성적", "일반적", "효율적", "효과적", "실질적", "전반적", "기술적", "조치", "접근",
}
# 동사·어미로 끝나는 말은 주제어가 아니다("취했는지", "설명해", "적용했을")
_VERBISH = re.compile(r"(?:는지|나요|습니까|세요|까요|겠|했|였|하며|하고|해서|하기|위해|위한|되는|하는|있는|없는|하셨|했을|했던|할지|한지|해|하여|"
                      r"인지|이며|이고|이나|라면|다면|으면|려면|면서|어서|아서|다|요|죠|까|니다|세요|시겠|겠어요)$")


def _stem(w):
    for _ in range(2):                                   # 조사가 겹친 말("환경에서도")도 뗀다
        for suf in _JOSA_TAIL:
            if len(w) > len(suf) + 1 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        else:
            break
    return w


def content_terms(text):
    """내용어 집합 — 한글 2음절 이상(조사 뗀 어간) + 영문 토큰(소문자). 흔한 질문 말(STOP_TERMS)은 뺀다."""
    out = set()
    for w in re.findall(r"[가-힣]{2,}", text or ""):
        if _VERBISH.search(w):
            continue
        s = _stem(w)
        if len(s) >= 2 and s not in STOP_TERMS and w not in STOP_TERMS:
            out.add(s)
    for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}", text or ""):
        out.add(w.lower().strip("."))
    return out


def repeats_question(followup, question, ratio=0.7):
    """F7: 꼬리질문이 원 질문을 되풀이하는가 — 꼬리질문 내용어의 ratio 이상이 원 질문에 이미 있다(내용어 2개 이상일 때)."""
    f = content_terms(followup)
    if len(f) < 2:
        return False
    return len(f & content_terms(question)) / len(f) >= ratio


def ungrounded_premise(followup, answer, sources):
    """F6: 꼬리질문이 전제로 삼는 **특정한 말**(3자 이상 내용어) 중 답변·원 질문·이력서 근거·모범답안 어디에도 없는 것.
    예: 답변은 '인터넷 환경을 다시 조명한 바'인데 꼬리질문이 '인터넷 환경을 재조명한 방법'을 묻는다 → ['재조명']."""
    pool = re.sub(r"\s+", "", " ".join([answer or ""] + list(sources or [])))
    # 한글 말만 본다 — 영문·숫자 토큰은 QST-18 근거 범위 검사(_bad_tokens)가 따로 맡는다(답변 원문을 일부러 뺀 규칙과 겹치지 않게)
    return sorted(t for t in content_terms(followup) if len(t) >= 3 and re.fullmatch(r"[가-힣]+", t) and t not in pool)


# F5(사용자 응시 피드백, HIGHLY PROHIBITED): 답변이 '모른다'·'이해가 안 된다'·'무슨 답변을 원하는지 모르겠다'류면
# 꼬리질문으로 파고들지 않는다. 음성 인식 결과라 문장이 흐트러져도 잡히게 넓게 둔다.
NON_ANSWER = re.compile(r"모르겠|모릅니다|모르는\s*(?:부분|내용|질문)|잘\s*모르|이해가\s*(?:잘\s*)?안|이해를\s*(?:잘\s*)?못|무슨\s*(?:말|뜻|답변|질문|의미)|"
                        r"어떤\s*답(?:변)?을\s*원|뭘\s*(?:말|원|물)|글쎄|패스|넘어가|기억이\s*(?:잘\s*)?안|생각이\s*(?:잘\s*)?안\s*나|"
                        r"안\s*보던|답하기\s*(?:가\s*)?어렵|해\s*본\s*적이\s*없|경험이\s*없|(?:거의|별로|딱히)\s*없|없던데|없었던\s*것\s*같|"
                        # 2026-11-23 사용자 승인(⑤) — **되묻기·음성 잡음**. 사용자 F6 사고("인터넷 환경을 재조명한
                        # 방법")가 바로 이 유형인데, 11-23 측정에서 잡음 답변("어... 네 그게 음... 아 잠시만요
                        # 인터넷이 좀... 네 뭐라고 하셨죠.")이 이 1층 검사를 통과해 모델이 불렸다(꼬리질문 문장
                        # 검사라는 2층에서만 막혔다). 되묻는 답변은 내용이 없는 답변이므로 모델을 부르기 전에 막는다.
                        r"뭐라고\s*(?:하셨|했|말)|다시\s*(?:한\s*번\s*)?(?:말씀|질문|설명)|잘\s*안\s*들|안\s*들렸|"
                        r"잠시\s*만|잠깐\s*만|끊긴\s*것\s*같|소리가\s*안")
NON_ANSWER_MIN_HANGUL = 8      # 한글 8자 미만 답변("아.", "이거 안 보던지")은 내용이 없다고 본다
NON_ANSWER_LONG = 50           # 이보다 긴 답변은 '모르지만 ~하겠다'처럼 실제 내용이 있을 수 있어 키워드만으로 막지 않는다


def non_answer(answer):
    """F5: 꼬리질문을 만들면 안 되는 답변인가. (bool, 사유)."""
    a = answer or ""
    n = len(re.findall(r"[가-힣]", a))
    if n < NON_ANSWER_MIN_HANGUL:
        return True, f"too_short({n})"
    if NON_ANSWER.search(a) and n < NON_ANSWER_LONG:
        return True, "non_answer_phrase"
    return False, ""


# 질문 틀에 늘 들어가는 말(회사 맥락 가정형 · 경험 안내)은 주제가 아니다
_FRAME_TERMS = {"가정", "다시", "비슷", "바탕", "기술", "스택", "지원", "회사", "택하", "업무", "프로젝트", "진행", "담당"}


def shared_topic(a, b, ignore=()):
    """F4: 두 질문이 같은 핵심 주제를 공유하면 그 말들(정렬), 아니면 []. 3자 이상 내용어 1개 또는 2자 내용어 2개 이상.
    허용 덧붙임은 떼고 보고, 회사 이름(ignore)·질문 틀 말은 빼고 센다."""
    drop = _FRAME_TERMS | content_terms(" ".join(ignore or ()))
    common = (content_terms(core(a)) & content_terms(core(b))) - drop
    long_ = [t for t in common if len(t) >= 3]
    return sorted(common) if (long_ or len(common) >= 2) else []
