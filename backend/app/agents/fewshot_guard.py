"""few-shot 예문 검증 장치 1층 — 규칙·지표만(LLM 호출 0회). 2026-11-20 사용자 지시(리드 전달).

사용자가 실제 응시 피드백으로 준 문장들이 질문 생성 지시문의 좋은 예/나쁜 예(few-shot)가 됐다(HANDOFF 28-5).
few-shot은 품질을 올리지만 네 가지 위험이 있다 — 이 모듈이 그것을 잡는다:
  ① 예문 베끼기        — 생성 질문이 예문과 단어·의미가 거의 같다            → `copy_score()` (도구에서 임베딩 유사도와 함께)
  ② 예문 내용 새기     — 예문에만 있는 고유 명사(회계팀·소비기한…)가 근거에 없는데 질문에 들어감 → `leak_terms()` (A18·이력서 검사에 연결)
  ③ 같은 예문 반복     — 풀 전체에서 예문 핵심어가 몰림                      → `backend/tools/fewshot_audit.py pool`
  ④ 새 예문이 다른 품질을 떨어뜨림 — 예문을 바꾸기 전/후 같은 조각으로 시범 생성해 비교 → `fewshot_audit.py trial`

예문을 지시문에 새로 넣거나 바꾸면 **반드시 아래 REGISTRY에도 등록**한다. `tools/test_question_rules.py`가 등록된 예문이 실제로
지시문 파일에 있는지 확인한다(등록부와 지시문이 어긋나면 시험이 실패한다).
"""
import re

from . import question_rules as QR

# id · 지시문 파일 · 좋은 예(good)/나쁜 예(bad) · 지시문 속 원문 ·
#   key_terms   = 그 예문에만 있는 **다른 장면의 고유한 말**(회계팀·생산 라인·소비기한…). 근거에 없는데 질문에 나오면 새어 든 것 → 탈락
#   style_terms = 예문이 쓴 **일반 묘사어**(답변 도우미·상담 기록·불확실성…). 근거 내용과 맞게 빌려 쓸 수 있어 탈락시키지 않고
#                 풀 전체 빈도만 본다(2026-11-20 첫 감사: '답변 도우미' 9건 — 조각은 상담사용 AI 문서인데 표현을 예문에서 빌림)
REGISTRY = [
    {"id": "IQ-6-bad-quoted-term", "prompt": "generate/internal_question", "kind": "bad",
     "text": "AI/AX 프로젝트에서 '적용 가능성 검토' 단계에서 규칙 기반 처리, 전통 ML, LLM, RAG 중",
     "key_terms": ["적용 가능성 검토"], "style_terms": []},
    {"id": "IQ-6-good-choose-method", "prompt": "generate/internal_question", "kind": "good",
     "text": "AI/AX 프로젝트를 새로 맡는다면 규칙 기반 처리·전통 ML·LLM·RAG 중 무엇을 어떤 기준으로 고르시겠습니까?",
     "key_terms": ["규칙 기반 처리", "전통 ML"], "style_terms": []},
    {"id": "IQ-6-3-bad-abstract", "prompt": "generate/internal_question", "kind": "bad",
     "text": "LLM 응용에서** 모델 답변의 근거 여부와 불확실성을 점검하는 상황이라면, 어떻게 하시겠습니까?",
     "key_terms": [], "style_terms": ["불확실성", "근거 여부"]},
    {"id": "IQ-6-3-good-chatbot", "prompt": "generate/internal_question", "kind": "good",
     "text": "사내 문서를 근거로 답하는 상담 챗봇이 **문서에 없는 내용을 사실처럼 답한 것을 발견했다면**",
     "key_terms": [], "style_terms": ["상담 챗봇"]},
    {"id": "IQ-6-3-good-F2-accounting", "prompt": "generate/internal_question", "kind": "good", "feedback": "F2",
     "text": "생산 라인 오검출 문제를 해결하는 중에 개발팀이 아닌 회계팀에 팀 업무를 브리핑해야 한다면, 무엇을 먼저 설명하시겠습니까?",
     "key_terms": ["회계팀", "브리핑", "오검출", "생산 라인"], "style_terms": []},
    {"id": "IQ-6-3-good-F1-classify", "prompt": "generate/internal_question", "kind": "good", "feedback": "F1",
     "text": "상담 기록을 자동으로 분류하는 프로젝트를 진행한다고 가정할 때, 어떤 기술과 방법으로 구현하시겠습니까?",
     "key_terms": [], "style_terms": ["상담 기록", "문의 유형", "처리 시간"]},
    {"id": "IQ-6-6-bad-internal-layer", "prompt": "generate/internal_question", "kind": "bad",
     "text": "콜센터 AI 상담 시스템에서 AI 처리 계층이** 답변을 생성할 때, 출처 정보가 부족할 경우 상담사에게 어떻게 유도하는지 설명해 주세요.",
     "key_terms": ["처리 계층", "출처 정보"], "style_terms": []},
    {"id": "IQ-6-6-good-assistant", "prompt": "generate/internal_question", "kind": "good",
     "text": "상담사가 쓰는 답변 도우미를 만든다고 할 때**, 사내 문서에서 근거를 찾지 못한 질문에 대해 시스템이 무엇을 하도록 설계하시겠습니까?",
     "key_terms": [], "style_terms": ["답변 도우미"]},
    {"id": "RM-7-good-F3-inspection", "prompt": "generate/resume_question_merger", "kind": "good", "feedback": "F3",
     "text": "[이력서에 적힌 검사 업무]를 {company_name}의 생산 라인에서 다시 진행한다고 가정할 때, 어떤 검사 방식과 기술 스택을 택하시겠습니까?",
     "key_terms": ["생산 라인"], "style_terms": ["검사 방식"]},
    {"id": "RM-bad-unrelated-merge", "prompt": "generate/resume_question_merger", "kind": "bad",
     "text": "이력서에 없는 '신상품 출시 후 경과 일수'를 끌어옴",
     "key_terms": ["신상품 출시", "경과 일수", "중고 상품"], "style_terms": []},
    {"id": "FU-bad-term-explain", "prompt": "evaluate/followup_judge", "kind": "bad", "feedback": "F5·F6",
     "text": "답변에 '골든셋'이 등장했다고 해서 → \"'골든셋'을 구체적으로 설명해 주세요\"",
     "key_terms": ["골든셋"], "style_terms": []},
]
COPY_JACCARD = 0.5     # 내용어 겹침이 이 이상이면 '베끼기 의심'(도구는 임베딩 코사인 ≥ 0.90도 함께 본다)


def _squash(s):
    return re.sub(r"\s+", "", (s or "").lower())


def leak_terms(question, *sources):
    """예문 고유 핵심어 중 질문에는 있는데 근거(조각 본문·이력서·공고)에는 없는 것. 없으면 [].
    예: 근거 조각에 '회계'가 없는데 질문에 '회계팀 브리핑'이 들어감 → ['회계팀', '브리핑']."""
    q, src = _squash(question), _squash(" ".join(s or "" for s in sources))
    out = []
    for e in REGISTRY:
        for t in e["key_terms"]:
            k = _squash(t)
            if k and k in q and k not in src:
                out.append(t)
    return sorted(set(out))


def copy_score(question):
    """질문과 가장 많이 겹치는 예문 (예문 id, 내용어 Jaccard). 허용 덧붙임·회사 자리는 떼고 본다."""
    qt = QR.content_terms(QR.core(question).replace("{회사}", ""))
    best = (None, 0.0)
    for e in REGISTRY:
        et = QR.content_terms(QR.core(e["text"]).replace("{company_name}", "").replace("{회사}", ""))
        if not qt or not et:
            continue
        j = len(qt & et) / len(qt | et)
        if j > best[1]:
            best = (e["id"], round(j, 3))
    return best
