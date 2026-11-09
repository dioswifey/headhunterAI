"""LangGraph State (ADLC v0.2 6장). 그래프 1 '질문 생성 + 면접 루프'는 QuestionGenerator 하나를 State로 쓴다.

- State는 한 작업(지원 1건) 안에서만 유지한다. 저장 기준은 계속 DB(applications.pregen_questions, attempt_questions)다.
- 면접 루프 동안은 checkpointer에 임시 보관(thread_id = attempt_id), 깨지면 DB로 다시 만든다 [승인 필요].
- LLM 노드는 이 State의 필드만 읽고 쓴다. 점수·합불·응시 상태는 여기 없다(코드 전용).
- 아직 그래프 코드(question_graph.py)는 없다 [제안]. 이 파일이 노드 간 계약이다.
"""
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["personality", "resume", "job", "company"]


class Keyword(BaseModel):
    term: str                                   # 이력서 표기 그대로
    section: Literal["career", "projects", "education", "certificates", "skills"]
    source: str = ""                            # 근거 원문 문장 (OCR 구조화 source)
    origin: Literal["ocr", "trust_added"] = "ocr"


class KeywordReport(BaseModel):
    """노드 2 evaluate_trust 결과 (prompts/keyword_trust.md)"""
    quality: int = 0                            # 1~5
    quantity_ok: bool = False
    removed: list[dict] = Field(default_factory=list)   # [{term, reason}]
    added: list[dict] = Field(default_factory=list)     # [{term, source}]
    rounds: int = 0                             # 고친 횟수 (최대 1회 재검수)


class BankHit(BaseModel):
    """노드 3 retrieve(RAG) 결과: 직무(role_code)에 맞는 질문 은행 문항"""
    bank_id: int
    category: Category
    subcategory: str = ""                       # 평가 의도로 사용
    text: str
    score: float = 0.0                          # 검색 유사도 (키워드·공고 대비)


class QuestionDraft(BaseModel):
    seq: int
    category: Category
    eval_items: list[str]
    text: str
    bank_id: int | None = None
    resume_ref: dict | None = None              # {section, index, label, quote}
    gen_method: Literal["db", "llm", "template", "fallback_job"] = "db"
    time_limit: int = 90                        # 기본 질문 90초 (꼬리질문 60초는 면접 중 별도 생성)
    company_facts: list[str] = Field(default_factory=list)
    philosophy: dict = Field(default_factory=dict)      # 노드 5 결과 {scores, pass, problems}
    answerability: dict = Field(default_factory=dict)   # 노드 6 결과 {answerable, asks}
    checks: dict = Field(default_factory=dict)          # 규칙 검사 {blocklist, grounded, facts_ok, length_ok}
    retries: int = 0


class QAPair(BaseModel):
    """면접 루프의 AI 질문–지원자 답변 쌍 (노드 10 record_answer)"""
    seq: int                                    # 기본 질문 번호 1~10 (꼬리질문은 같은 번호)
    question_id: int | None = None              # attempt_questions.id
    question: str
    answer: str = ""
    is_followup: bool = False
    time_limit: int = 90                        # 기본 90초, 꼬리질문 60초
    keyword_hits: list[str] = Field(default_factory=list)   # 답변에 나온 expected_keywords (코드 대조)
    specificity: int | None = None              # 모범 답변 대비 구체성 1~5 (LLM)
    followup_reason: str | None = None          # keyword_zero | abstract | off_topic | resume_mismatch


class QuestionGenerator(BaseModel):
    """그래프 1 State: start → extract_keywords → evaluate_trust → retrieve → generate → evaluate_philosophy
    → evaluate_answerability → finalize → [면접 루프] ask_question → record_answer → judge_followup ⟲ make_followup / next_question"""
    # 입력 (start)
    application_id: int
    job_id: int
    role_code: Literal["ai_ax", "backend", "data_sci"]  # 직무 추가 시 확장
    role_name: str = ""
    company_name: str = ""
    company_intro: str = ""
    duties: str = ""
    requirements: str = ""
    resume_snapshot: dict = Field(default_factory=dict)  # 지원자 '내용 확인 완료' 스냅샷만

    # 노드 1~2
    keywords: list[Keyword] = Field(default_factory=list)
    keyword_report: KeywordReport = Field(default_factory=KeywordReport)

    # 노드 3~6
    retrieved: list[BankHit] = Field(default_factory=list)
    drafts: list[QuestionDraft] = Field(default_factory=list)

    # 출력 (finalize) — 8문항: 인성 2 → 이력서 3 → 직무 2 → 기업 1 (questions.PLAN, 2026-11-09)
    final_questions: list[QuestionDraft] = Field(default_factory=list)

    # 면접 루프 (노드 9~13) — PRD INT-06: 질문당 1개, 전체 최대 4개
    attempt_id: int | None = None
    cursor: int = 0                             # final_questions 안의 현재 위치
    qa_pairs: list[QAPair] = Field(default_factory=list)
    followups_used: int = 0
    max_followups: int = 4
    errors: list[str] = Field(default_factory=list)     # 오류 종류만 (원문 금지, ADLC B-08)
    prompt_version: str = ""
