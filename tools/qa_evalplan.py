"""QA 에이전트 확장 — PRD v0.13 평가 플랜(6.3.8 EVP-01~08) · 운영·관측 지표(6.3.9 M-45~54) 반영 (2026-11-12).

`tools/qa_agent.py record()`가 부른다. 여기서 정하는 것:
  - 계획서 4_AI품질지표 M-31~M-54 (유형 · 판정 주체 · 측정 시점 · CI95 하한 목표 열 추가, 세 겹 판정 수식)
  - 계획서 1_범위·전략 '평가 플랜' 블록, 3_시나리오 S-14~20, 5_리스크 R-16~22
  - 테스트케이스 AI평가 EV-29~63 (그래프 1-A 노드별 · 1-B 꼬리질문 A3 · 2 평가 그래프 · 운영 지표)
  - 추적표 하단 'AC ↔ EV ↔ M' 블록, 결함 QA-27~30 (qa_agent.DEFECTS에 이어 붙임)
  - documents/03_QA_doc/eval_runs/*.json 이 있으면 지표 현재값 · EV 실제 결과 · 실행기록 RUN을 채움(없으면 비워 둠)

사람이 채우는 노란 칸(현재값·표본 수·측정일·근거·CI 하한 측정값 / EV 측정값·표본 수·상태·실행일·비고)은
평가 결과 파일이 없으면 건드리지 않는다. 값의 기준은 PRD v0.13(6.3.5·6.3.6·6.3.8·6.3.9), 측정값은 여기(PSP-10).
"""
import datetime as dt
import glob
import json
import os
import re
from copy import copy

BASELINE_DATE = "2026-11-12"
TIMING = "변경 시 + 주 1회(EVP-06)"
JUDGE = "로컬 qwen3:8b(온도 0)"
GEN = "qwen3:4b(개발) / 운영 기준 Qwen3-8B"

# ---------------- 4_AI품질지표: M-31~M-54 ----------------
# (지표 ID, AI 역할, 지표, 단위, 목표, 방향, 정의·계산, 데이터셋·표본, 유형, 판정 주체, 측정 시점, CI95 하한 목표, 비고, 판정 방식)
# 판정 방식: num = 목표 비교(+CI 하한) / rec = 목표 없음, 기록만 / alarm = 경보 기준(값 > 목표면 '경보')
METRICS = [
    ("M-31", "검색(RAG) 질문 은행", "검색 관련도 평균(1~5)", "점(1~5)", 4.0, "이상", "질문마다 검색된 은행 문항의 공고·이력서 관련도 판정 평균. 보조: P(점수≥3) ≥ 0.90",
     "가상 응시 ≥5건 × 8문항(n ≥ 40)", "확률적(평균형·분포형)", "판정 모델", TIMING, None, "PRD 6.3.5 · AC-72", "num"),
    ("M-32", "A2 생성", "질문 근거성 비율", "비율", 0.95, "이상", "최종 질문이 은행 문항·공고·이력서 중 하나 이상에 근거하는 비율(규칙 토큰 대조 ∧ 판정)",
     "같은 표본(n ≥ 40)", "확률적(비율형)", "판정 모델 + 규칙", TIMING, 0.85, "Wilson 하한 ≥ 목표 − 0.10(PSP-05) · AC-73", "num"),
    ("M-33", "A2 생성", "질문 품질 루브릭 평균(5항목)", "점(1~5)", 4.0, "이상", "명확성·직무 관련성·답변 가능성·한 문장·비편향 평균, 세트 단위 부트스트랩 CI",
     "같은 표본(n ≥ 40)", "확률적(평균형)", "판정 모델", TIMING, 3.7, "AC-73", "num"),
    ("M-34", "A2 생성", "세트 내 의미 중복 쌍 비율", "비율", 0.05, "이하", "세트 8문항 28쌍 중 임베딩 코사인 ≥ 0.85인 쌍 비율",
     "세트 ≥ 5", "확률적(비율형)", "규칙(임베딩)", TIMING, None, "AC-74", "num"),
    ("M-35", "검색(RAG) 질문 은행", "대체 경로 출제 비율", "비율", 0.20, "이하", "Redis·임베딩 실패·문항 부족으로 DB 은행·템플릿에서 나간 질문 비율(은행 적재 직무로 판정)",
     "n ≥ 40", "확률적(비율형)", "규칙(출처 기록)", TIMING, None, "backend·data_sci는 기록만 · AC-74 · QA-27", "num"),
    ("M-36", "검색(RAG) 사내문서 A14", "사내문서 대외비 누출 건수(하드) · 관련도", "건", 0, "이하", "질문·컨텍스트에 대외비 내용이 들어간 건수(전수). 보조: 검색 조각 관련도 평균 ≥ 3.5(n ≥ 20)",
     "대외비 유도 질의 포함 검색 n ≥ 20", "하드 + 확률적", "규칙(등급·금지 용어) + 판정 모델", TIMING, None, "AC-75 · R-22", "num"),
    ("M-37", "그래프 1-A finalize", "카테고리 분포 준수율(2·3·2·1)", "비율", 1.0, "이상", "인성 2·이력서 3·직무 2·기업 1을 지킨 세트 비율(전수)",
     "모든 표본 세트", "결정적", "규칙", TIMING, None, "AC-76 · UT-40", "num"),
    ("M-38", "리포트(그래프 2·CrewAI)", "리포트 인용 충실도", "비율", 1.0, "이상", "리포트 인용이 답변 원문 부분 문자열인 비율(전수)",
     "리포트 전수", "하드", "규칙", TIMING, None, "AC-77", "num"),
    ("M-39", "리포트(그래프 2·CrewAI)", "리포트 루브릭 평균(5항목)", "점(1~5)", 4.0, "이상", "사실 정확성·구체성·실행 가능성·어조·점수 일관성 평균(템플릿 포함·제외 함께 기록)",
     "블록 n ≥ 40(응시 ≥ 14 × 3블록)", "확률적(평균형)", "판정 모델", TIMING, 3.7, "AC-78", "num"),
    ("M-40", "리포트(그래프 2·CrewAI)", "점수-서술 일관성", "비율", 0.90, "이상", "강점 항목 ≥ 중앙값 · 개선점 항목 ≤ 중앙값 비율(동점 일치)",
     "서술 n ≥ 40", "확률적(비율형)", "규칙 + 판정 모델", TIMING, 0.80, "AC-78", "num"),
    ("M-41", "리포트(그래프 2·CrewAI)", "리포트 금지 내용 건수(하드)", "건", 0, "이하", "가드 통과 후 합불 언급·입력에 없는 두 자리 이상 수치·차별 요소 건수(전수)",
     "리포트 전수", "하드", "규칙 + 판정 모델 보조", TIMING, None, "AC-79 · UT-42", "num"),
    ("M-42", "리포트(그래프 2·CrewAI)", "리포트 템플릿 대체율", "비율", 0.20, "이하", "모델 실패·가드 위반으로 정해진 문구로 바뀐 블록 비율",
     "블록 n ≥ 40", "확률적(비율형)", "규칙(대체 기록)", TIMING, None, "AC-80", "num"),
    ("M-43", "리포트(그래프 2·CrewAI)", "리포트 생성 성공률", "비율", 0.95, "이상", "형식 오류·시간 초과 없이 응답한 비율",
     "시도 n ≥ 40", "확률적(비율형)", "규칙", TIMING, 0.85, "AC-80", "num"),
    ("M-44", "판정 모델 보정", "판정 모델 vs 사람 일치도(Spearman)", "ρ", 0.6, "이상", "같은 표본의 판정 점수와 사람 점수 순위 상관(질문 M-33 · 리포트 M-39 각각)",
     "사람 검토 10~20개(EVP-08)", "확률적(상관형)", "사람 + 판정 모델", "판정 모델·프롬프트 변경 시 + 첫 측정", None,
     "미측정 — 사람 평가 전(EVP-08) · AC-81·89", "num"),
    ("M-45", "검색(RAG) 사내문서 A14", "G-검색 Recall@4", "비율", 0.80, "이상", "골든셋 G-검색 질의마다 정답 조각이 상위 4개 안에 든 비율",
     "G-검색 질의 n ≥ 20(골든셋 버전 기록)", "확률적(비율형)", "규칙", TIMING, 0.70, "첫 기준선 뒤 1회 조정 가능 · AC-90 · R-18", "num"),
    ("M-46", "검색(RAG) 사내문서 A14", "G-검색 MRR", "점(0~1)", 0.60, "이상", "정답 조각 순위 역수 평균(상위 4 밖 = 0)",
     "G-검색 질의 n ≥ 20", "확률적(평균형)", "규칙", TIMING, None, "AC-90", "num"),
    ("M-47", "A2 생성(기업 문항)", "G-질문 의미 유사도", "코사인", None, "이상", "같은 문서·조건 생성 질문과 G-질문 기준 질문(판정 ≥ 4.5만)의 임베딩 코사인 평균",
     "G-질문(골든셋 버전 기록)", "확률적(평균형)", "규칙(임베딩)", TIMING, None, "목표는 첫 기준선 뒤 설정 · AC-90", "rec"),
    ("M-48", "A2 생성 · 리포트", "근거 충실도(faithfulness)", "비율", 0.90, "이상", "생성 결과를 주장 단위로 나눠 RAG 출처(은행 문항·사내문서 조각·공고·이력서·답변)가 뒷받침하는 주장 비율의 응답 평균",
     "응답 n ≥ 40(질문·리포트 각각)", "확률적(평균형)", "판정 모델", TIMING, 0.85, "AC-91", "num"),
    ("M-49", "A2 생성 · 리포트", "환각률", "비율", 0.05, "이하", "뒷받침 안 되는 주장이 1개 이상인 응답 비율. 입력에 없는 수치·고유명사·인용 = 0건(하드)",
     "응답 n ≥ 40", "확률적(비율형) + 하드", "판정 모델 + 규칙(토큰 대조)", TIMING, None, "AC-91 · QA-28 · R-19", "num"),
    ("M-50", "A3 꼬리질문 · 그래프 전체", "응답 지연 P95(A3 판단)", "초", 5, "이하", "LangSmith run latency P50·P95(노드·그래프별). A3는 운영 P95 ≤ 5초, 개발 PC(FOLLOWUP_TIMEOUT 12초)는 참고값",
     "답변 ≥ 30(eval_followup) · LangSmith run", "분포(백분위형)", "규칙(LangSmith)", TIMING, None, "세트 생성·평가 그래프·리포트는 첫 기준선 뒤 목표 · AC-92", "num"),
    ("M-51", "운영", "응시당 토큰 · 비용", "토큰", None, "이하", "usage_metadata 입력+출력 토큰 합. 비용 = GPU 시간 × $0.75/시간 + 외부 예외(TTS) 실제 요금(따로)",
     "응시 단위", "모니터링", "규칙(LangSmith)", TIMING, None, "목표 없음 · 기준선 대비 +20% 초과 경보 · AC-93", "rec"),
    ("M-52", "운영(사람 확정)", "인간 개입률(semi 판정 변경)", "비율", 0.20, "이하", "semi 공고에서 final_result ≠ result 비율. 보조: 이력서 구조화 지원자 수정 비율(M-13)",
     "실사용 semi 공고 응시", "모니터링", "규칙", "실사용 데이터 생긴 뒤 + 주 1회", None, "미측정 — 실사용 전 · 0.20 초과 = 경보 · AC-94", "alarm"),
    ("M-53", "운영(사람 확정)", "증강 vs 자동화 비율(auto : semi)", "비율", None, "이상", "결정 방식 auto : semi 공고 수 · 응시 수 비율",
     "실사용 공고", "모니터링", "규칙", "실사용 데이터 생긴 뒤 + 주 1회", None, "미측정 — 실사용 전 · 목표 없음 · AC-94", "rec"),
    ("M-54", "그래프 1-A · 2", "태스크 완료율", "비율", 0.95, "이상", "① 응시 시작→평가 완료(시스템 원인만) ② 1-A 대체 경로 없이 완료 ③ 평가 그래프 성공률 — 각각. M-35·M-43 참조",
     "각 n ≥ 40", "확률적(비율형)", "규칙(LangSmith·DB)", TIMING, 0.85, "AC-95", "num"),
]
EXTRA_HDR = ["유형", "판정 주체", "측정 시점", "CI95 하한 목표", "CI95 하한(측정)"]   # 4_AI품질지표 O~S열(머리글 4행 기존 이름은 그대로)

# ---------------- AI평가 EV-29~63 ----------------
# (EV, 지표 ID, AI 역할, 대상, 모델, 데이터셋·표본, 절차, 단위, 목표, 방향, 방식, 관련 AC, 비고)
_Q = "eval_rag_questions(generate→judge→report)"
_R = "eval_reports(run→judge→report)"
_G = "build_golden_sets(retrieval·questions·metrics)"
_F = "eval_followup(가상 답변 → question_graph.judge, DB 없음)"
EVS = [
    ("EV-29", "M-31", "검색(RAG) 질문 은행", "question_graph.retrieve → vectorstore.knn", GEN + " · 판정 " + JUDGE, "테스트 기업 3곳×3직무 × 가상 이력서 3~4", _Q, "점(1~5)", 4.0, "이상", "num", "AC-72", "P(≥3) ≥ 0.90 함께"),
    ("EV-30", "M-32", "A2 생성", "그래프 1-A generate", GEN + " · 판정 " + JUDGE, "같은 표본 n ≥ 40", _Q, "비율", 0.95, "이상", "num", "AC-73", ""),
    ("EV-31", "M-33", "A2 생성", "그래프 1-A 최종 질문", GEN + " · 판정 " + JUDGE, "같은 표본 n ≥ 40", _Q, "점(1~5)", 4.0, "이상", "num", "AC-73", "CI95 하한 ≥ 3.7"),
    ("EV-32", "M-34", "A2 생성", "세트 내 중복", "qwen3-embedding:0.6b", "세트 ≥ 5", _Q, "비율", 0.05, "이하", "num", "AC-74", ""),
    ("EV-33", "M-35", "검색(RAG) 질문 은행", "대체 경로(pick_bank·템플릿)", GEN, "n ≥ 40", _Q, "비율", 0.20, "이하", "num", "AC-74", "QA-27"),
    ("EV-34", "M-36", "검색(RAG) 사내문서 A14", "internal_search.search(public만)", "qwen3-embedding:0.6b · 판정 " + JUDGE, "대외비 유도 질의 포함 n ≥ 20", _Q + " internal", "건", 0, "이하", "num", "AC-75, AC-85", "관련도 평균 ≥ 3.5 함께"),
    ("EV-35", "M-37", "그래프 1-A finalize", "카테고리 분포", "—", "모든 표본 세트", _Q, "비율", 1.0, "이상", "num", "AC-76", "UT-40 정적 확인과 같이 봄"),
    ("EV-36", "M-45", "검색(RAG) 사내문서 A14", "G-검색 Recall@4", "qwen3-embedding:0.6b", "G-검색(골든셋 v1)", _G, "비율", 0.80, "이상", "num", "AC-85, AC-90", "MIN_SIM 0.45~0.70 표 함께(QA-30)"),
    ("EV-37", "M-46", "검색(RAG) 사내문서 A14", "G-검색 MRR", "qwen3-embedding:0.6b", "G-검색(골든셋 v1)", _G, "점(0~1)", 0.60, "이상", "num", "AC-85, AC-90", ""),
    ("EV-38", "M-47", "A2 생성(기업 문항)", "G-질문 의미 유사도", "qwen3-embedding:0.6b", "G-질문(판정 ≥ 4.5만)", _G, "코사인", None, "이상", "rec", "AC-85, AC-90", "목표 = 첫 기준선 뒤"),
    ("EV-39", "M-48", "A2 생성", "질문 근거 충실도", "판정 " + JUDGE, "질문 n ≥ 40", _Q, "비율", 0.90, "이상", "num", "AC-91", "CI95 하한 ≥ 0.85"),
    ("EV-40", "M-49", "A2 생성", "질문 환각률(기업 문항 지어낸 수치 포함)", "판정 " + JUDGE + " + 토큰 대조", "질문 n ≥ 40", _Q, "비율", 0.05, "이하", "num", "AC-91", "QA-28: 기업 질문 9개 중 4개 수치 창작(rag-eval)"),
    ("EV-41", "M-38", "리포트(그래프 2·CrewAI)", "인용 충실도", "—(규칙)", "리포트 전수", _R, "비율", 1.0, "이상", "num", "AC-77", ""),
    ("EV-42", "M-39", "리포트(그래프 2·CrewAI)", "리포트 루브릭", GEN + " · 판정 " + JUDGE, "블록 n ≥ 40", _R, "점(1~5)", 4.0, "이상", "num", "AC-78", "CI95 하한 ≥ 3.7"),
    ("EV-43", "M-40", "리포트(그래프 2·CrewAI)", "점수-서술 일관성", "규칙 + 판정", "서술 n ≥ 40", _R, "비율", 0.90, "이상", "num", "AC-78", ""),
    ("EV-44", "M-41", "리포트(그래프 2·CrewAI)", "금지 내용", "규칙", "리포트 전수", _R, "건", 0, "이하", "num", "AC-79", "UT-42 규칙 확인"),
    ("EV-45", "M-42", "리포트(그래프 2·CrewAI)", "템플릿 대체율", "규칙", "블록 n ≥ 40", _R, "비율", 0.20, "이하", "num", "AC-80", ""),
    ("EV-46", "M-43", "리포트(그래프 2·CrewAI)", "생성 성공률", "규칙", "시도 n ≥ 40", _R, "비율", 0.95, "이상", "num", "AC-80", ""),
    ("EV-47", "M-48", "리포트(그래프 2·CrewAI)", "리포트 근거 충실도", "판정 " + JUDGE, "리포트 응답 n ≥ 40", _R, "비율", 0.90, "이상", "num", "AC-91", ""),
    ("EV-48", "M-49", "리포트(그래프 2·CrewAI)", "리포트 환각률", "판정 " + JUDGE + " + 토큰 대조", "리포트 응답 n ≥ 40", _R, "비율", 0.05, "이하", "num", "AC-91", ""),
    ("EV-49", "M-44", "판정 모델 보정", "판정 vs 사람(질문·리포트)", JUDGE + " vs 사용자", "사람 검토 10~20개(EVP-08)", "사용자 먼저 채점(AI 점수 가림) → Spearman", "ρ", 0.6, "이상", "num", "AC-81, AC-89", "미측정 — 사람 평가 전"),
    # 그래프 1-A 노드별 (EVP-01)
    ("EV-50", "M-33", "그래프 1-A A2 generate", "카테고리별 루브릭·근거성(인성 다듬기·이력서 합치기·직무 섞기·기업 맞춤)", GEN + " · 판정 " + JUDGE, "같은 표본, 카테고리별", _Q, "점(1~5)", 4.0, "이상", "num", "AC-84", "노드별 분포 — 카테고리별 최저값"),
    ("EV-51", "—", "그래프 1-A A5 evaluate_trust", "키워드 신뢰 검수 통과율·제거 키워드 수", GEN, "같은 표본", _Q + " (log)", "비율", None, "이상", "rec", "AC-84", "목표는 기준선 뒤(EVP-01)"),
    ("EV-52", "—", "그래프 1-A A6 evaluate_philosophy", "철학 적합 검수 판정 분포(통과·수정·원문 유지)", GEN, "같은 표본", _Q + " (log)", "비율", None, "이상", "rec", "AC-84", "목표는 기준선 뒤(EVP-01)"),
    ("EV-53", "—", "그래프 1-A A7 evaluate_answerability", "답변 가능성 검수 판정 분포", GEN, "같은 표본", _Q + " (log)", "비율", None, "이상", "rec", "AC-84", "목표는 기준선 뒤(EVP-01)"),
    ("EV-54", "M-54", "그래프 1-A 전체", "② 대체 경로(build_set·템플릿) 없이 끝까지 간 비율", GEN, "가상 응시 n ≥ 40", _Q, "비율", 0.95, "이상", "num", "AC-95", ""),
    # 그래프 1-B 꼬리질문 A3
    ("EV-55", "—", "그래프 1-B A3 judge_followup", "꼬리질문 필요 판단 정확도(라벨 대비)", "qwen3:4b(동시 호출 1)", "가상 답변 ≥ 30(구체적·모호·짧음·주제 이탈)", _F, "비율", None, "이상", "rec", "AC-84", "판단 적합도 목표는 꼬리질문 에이전트 정리 때(PRD 6.3.2)"),
    ("EV-56", "—", "그래프 1-B A3 make_followup", "꼬리질문 품질 루브릭(구체화 유도·정답 유도 없음·한 문장)", "qwen3:4b · 판정 qwen3:4b(6GB GPU — 8b 동시 적재 불가)", "꼬리질문이 생긴 답변", _F, "점(1~5)", None, "이상", "rec", "AC-84", "목표는 기준선 뒤 · 생성=판정 모델(자기 평가 편향, R-16) → 참고값"),
    ("EV-57", "M-50", "그래프 1-B A3 judge_followup", "판단 지연 P95(P50 함께)", "qwen3:4b(개발 — 참고값)", "가상 답변 ≥ 30", _F, "초", 5, "이하", "num", "AC-92", "운영 5초 기준, 개발 PC FOLLOWUP_TIMEOUT 12초 — 참고값"),
    # 그래프 2
    ("EV-58", "M-54", "그래프 2 evaluation_graph", "③ 평가 그래프 성공률", GEN, "가상 응시 n ≥ 40", _R, "비율", 0.95, "이상", "num", "AC-95", ""),
    ("EV-59", "M-54", "응시 전체", "① 응시 시작 → 평가 완료(시스템 원인만)", GEN, "가상 응시 n ≥ 40", _R, "비율", 0.95, "이상", "num", "AC-95", "지원자 이탈 제외"),
    # 운영·관측
    ("EV-60", "M-50", "그래프 1-A · 2 · 리포트", "세트 생성·평가 그래프·리포트 지연 P50·P95", GEN, "LangSmith run", "LangSmith run latency 집계", "초", None, "이하", "rec", "AC-92", "첫 기준선 기록 뒤 목표"),
    ("EV-61", "M-51", "운영", "응시당 토큰·비용", GEN, "LangSmith usage_metadata", "토큰 합 · GPU 시간 × $0.75/h · 외부 TTS 요금(따로)", "토큰", None, "이하", "rec", "AC-93", "+20% 초과 경보"),
    ("EV-62", "M-52", "운영(사람 확정)", "인간 개입률", "—", "실사용 semi 공고", "final_result ≠ result 비율", "비율", 0.20, "이하", "alarm", "AC-94", "미측정 — 실사용 전"),
    ("EV-63", "M-53", "운영(사람 확정)", "auto : semi 비율", "—", "실사용 공고", "결정 방식별 공고·응시 수", "비율", None, "이상", "rec", "AC-94", "미측정 — 실사용 전"),
]

# ---------------- 3_시나리오 S-14~20 ----------------
SCENARIOS = [
    ("S-14", "그래프 1-A 질문 생성: start → 키워드 → A5 → retrieve → A2 → A6 → A7 → finalize", "노드별 품질 분포(EVP-01)·카테고리 2·3·2·1·근거성·환각·대체 경로",
     "QST-01·06·10·11, EVP-01", "AC-72~74, AC-76, AC-84, AC-91, AC-95", "UT-35·39·40·41, EV-29~35, EV-39·40, EV-50~54", "P1"),
    ("S-15", "그래프 1-B 면접 한 걸음: record_answer → A3 판단 → (꼬리질문) → next_question", "판단 정확도·꼬리질문 품질·지연 P95 ≤ 5초(운영)",
     "INT-06, EVP-01", "AC-84, AC-92", "UT-39, IT-38, EV-55~57", "P1"),
    ("S-16", "그래프 2 평가·리포트: load → evaluate_blocks → compute → summarize → decide → save → CrewAI 코치", "인용 1.00·금지 내용 0건·루브릭·일관성·대체율·성공률·완료율",
     "EVL-01~08, RPT-03, EML-02·03", "AC-77~80, AC-91, AC-95", "UT-39·42, IT-38, EV-41~48, EV-58·59", "P1"),
    ("S-17", "RAG 갈래 ① 질문 은행: 공고+이력서 키워드 → 임베딩 → Redis qb_idx → 기본 질문 7개", "직무 필터·번역본만·관련도·대체 경로 비율(DB 은행 직무 필터 — QA-27)",
     "QST-07·10, LNG-01", "AC-72, AC-74", "UT-36, EV-29·33", "P1"),
    ("S-18", "RAG 갈래 ② 사내문서: Chroma public 컬렉션 → A14 검색(MIN_SIM·재순위) → 기업 문항 1개", "등급 분리(혼입 0)·대외비 누출 0건·Recall@4·MRR·MIN_SIM 실측",
     "QST-11, EVP-03", "AC-75, AC-85, AC-90", "IT-39, EV-34, EV-36~38", "P1"),
    ("S-19", "평가 플랜 운영: 골든셋(버전) + 가상 데이터 → 세 겹 판정 → 회귀 비교 → 미달 시 수정 루프 → 사람 검토", "하드 0건·평균+CI 하한·기준선 대비 0.2점/5%p, M-44 미측정 표시",
     "PSP-01~10, EVP-02~08", "AC-71, AC-81~83, AC-86~89", "UT-43, EV-49, 실행기록", "P1"),
    ("S-20", "운영·관측(LangSmith): 로컬 판정 점수 → feedback(key = 지표 ID) → 대시보드", "온라인 평가기 0건·DEV_MODE + 가상 데이터만·지연·토큰·개입·완료율",
     "6.3.9, PSP-06·09", "AC-93~96", "UT-44, EV-60~63", "P2"),
]

# ---------------- 5_리스크 R-16~22 ----------------
RISKS = [
    ("R-16", "자기 평가 편향 — 생성(qwen3 계열)과 판정(qwen3:8b)이 같은 계열이라 판정 점수가 후하게 나옴", 3, 3, None,
     "사람 검토 일치도 M-44(ρ ≥ 0.6) 전까지 판정 지표는 참고값, 판정 2회(온도 차) 불일치 항목을 사람 검토 우선(EVP-08)", "M-44", "EV-49", "Claude"),
    ("R-17", "GPU 6GB 경합 — 평가 생성·판정·QA 자동 검사·면접이 같은 GPU를 쓰면 지연·시간 초과로 지표가 왜곡", 3, 3, None,
     "평가는 순차 실행(동시 호출 1), QA 검사는 QA_NO_GPU=1로 GPU 검사 생략, 개발 값은 참고값(운영 Qwen3-8B 기준)", "M-50", "EV-57, EV-60", "Claude"),
    ("R-18", "사내문서 MIN_SIM 0.55가 정답 조각을 잘라냄 — 정답 유사도가 0.45~0.55인 질의 실측", 2, 3, None,
     "G-검색 MIN_SIM 0.45~0.70 표로 다시 정함(EVP-07, 전후 Recall@4·노이즈 보고)", "M-45", "EV-36", "Claude"),
    ("R-19", "기업(회사 맞춤) 질문에 공고에 없는 수치를 지어냄 — 9개 중 4개(rag-eval)", 3, 3, None,
     "contextualize에 숫자·영문 토큰 원문 대조 추가 제안(QA-28), 환각률 M-49 하드 0건으로 감시", "M-49", "EV-40", "Claude"),
    ("R-20", "DB 질문 은행 대체 경로 pick_bank에 직무 필터 없음 — 다른 직무 문항 출제 가능(AC-37 위반)", 3, 2, None,
     "Redis 문항 0인 직무(backend·data_sci) 적재 우선, pick_bank 직무 조건 추가 제안(QA-27)", "M-35", "EV-33", "Claude"),
    ("R-21", "사내문서 원본 해시가 CRLF/LF에 따라 달라 PC마다 sync가 80개 전부 재임베딩", 1, 3, None,
     "해시 전 CRLF → LF 정규화(2026-11-12 수정, QA-29), sync --dry-run 변경 0 확인", "—", "IT-39", "Claude"),
    ("R-22", "사내문서 금지 용어 사전 idoc:forbid 미구현 — 조각 메타 forbid_terms만 검사", 3, 2, None,
     "대외비 유도 질의 누출 검사(M-36 하드) 전수, 금지 용어 사전 구현 전까지 public 컬렉션 물리 분리에 의존", "M-36", "EV-34, IT-39", "Claude"),
]

# ---------------- 결함 QA-27~30 (qa_agent.DEFECTS 형식) ----------------
DEFECTS = [
    ["QA-27", "높음", "P1", "질문 생성", "rag-eval(EV-33·M-35) 2026-11-12", "DB 질문 은행 대체 경로(pick_bank)가 지원 직무가 아닌 직무 문항을 낼 수 있음(Redis 문항 부족 직무 backend·data_sci가 이 경로를 탐)",
     "question_bank 테이블에 직무 칸이 없고 pick_bank(category, n)가 카테고리로만 고름",
     "결정 (C)(2026-11-12 조정자): 이번에는 코드 미수정. 질문 은행에 직무 문항 추가 + question_bank.role_code 열 추가는 별도 작업으로 분리",
     "backend/app/questions.py pick_bank, agents/question_graph.py retrieve, db/schema.sql question_bank", "보류", "2026-11-12", None,
     "별도 작업 때 대체 경로 직무 문항 = 지원 직무만 확인하는 검사 추가", "R-20, AC-37 · 결정 (C)"],
    ["QA-28", "높음", "P1", "질문 생성", "rag-eval(EV-40·M-49) 2026-11-12", "기업(회사 맞춤) 질문 9개 중 4개에 공고·회사 정보에 없는 수치가 들어감",
     "questions.contextualize는 모델이 적어 낸 company_facts만 원문과 대조하고, 다시 쓴 질문 문장의 숫자·영문 토큰은 검사하지 않음(_tokens_ok 미적용)",
     "조치 중 — 재측정 대기: rag-eval이 questions.py 수정(contextualize에 _tokens_ok 추가 · make_resume_question 한 문장 검사 2곳 · Redis 두 문장 후보 제외). 같은 표본으로 재측정 후 판정(EVP-07)",
     "backend/app/questions.py contextualize · make_resume_question", "진행 중", "2026-11-12", None, "M-49 하드(입력에 없는 수치 0건) 전수 감시 · 재측정 전후 수치 보고", "R-19, AC-91 · 상태: 조치 중 — 재측정 대기"],
    ["QA-29", "보통", "P2", "사내문서 RAG", "vector-check 2026-11-12", "git core.autocrlf PC(CRLF)와 LF PC에서 사내문서 원본 해시가 달라 sync가 80개 전부 다시 임베딩",
     "parse_doc이 원본 바이트를 줄바꿈 정규화 없이 SHA-256", "해시 전에 CRLF → LF 정규화(2026-11-12 조정자 수정)",
     "backend/app/agents/internal_docs_agent.py parse_doc", "통과", "2026-11-12", "2026-11-12", "IT-39(문서 80·조각 2,270·혼입 0) + sync --dry-run 변경 0", "R-21"],
    ["QA-30", "보통", "P2", "사내문서 RAG", "vector-check · G-검색 2026-11-12", "정답 조각 유사도가 0.45~0.55인 질의가 있어 MIN_SIM=0.55에서 정답이 잘림(Recall@4 저하·빈 결과)",
     "MIN_SIM이 실측 없이 정한 초기값", "미조치 — G-검색 MIN_SIM 0.45~0.70 표로 다시 정함(EVP-07, 전후 수치 보고)",
     "backend/app/agents/internal_search.py MIN_SIM", "보류", "2026-11-12", None, "M-45·46 회귀 비교(같은 골든셋 버전)", "R-18, EV-36"],
    ["QA-31", "낮음", "P3", "QA 도구", "QA 에이전트 RUN-008(UT-26 보류) 2026-11-12", "UT-26이 FileNotFoundError로 보류 — /questionbank/coding_taxonomy/programmers_taxonomy.json 없음",
     "questionbank 구조 변경(coding_sql_practice/ 아래로 이동, CLAUDE.md 디렉토리 구조)에 QA 검사 코드의 하드코딩 경로가 안 따라감(제품 coding_specs.TAXONOMY는 새 경로)",
     "tools/qa_checks.py UT-26이 제품 상수 coding_specs.TAXONOMY를 쓰도록 수정 → 재실행 통과(Junior 6 / Senior 4)",
     "tools/qa_checks.py UT-26", "통과", "2026-11-12", "2026-11-12", "검사 코드에 경로를 하드코딩하지 않고 제품 상수를 import", "UT-26"],
]

# ---------------- 1_범위·전략: 평가 플랜 블록 ----------------
STRATEGY_TITLE = "평가 플랜 (PRD v0.13 6.3.8 · EVP-01~08, 6.3.9 운영·관측 지표)"
STRATEGY_HDR = ["ID", "항목", "내용", None, None, None, "관련 지표", "관련 AC"]
STRATEGY = [
    ("EVP-01", "평가 목표", "단계별 품질 분포 — 검색(retrieve·A14)·생성(A2)·검수(A5~A7)·꼬리질문(A3)·리포트를 노드마다 따로 재서 분포와 95% CI로 판단. 목표 없는 노드는 기록부터", "M-31~54", "AC-84"),
    ("EVP-02", "평가 데이터셋", "버전 붙인 고정 골든셋 + 가상 데이터(직무 3 × 가상 이력서 3~4, 가끔 새 이력서 — 회귀 비교에서 뺌). 실제 지원자 데이터 금지(PSP-09)", "—", "AC-83, AC-84"),
    ("EVP-03", "사내문서 골든셋(자동)", "G-검색(public 조각 → 역질의 → 정답 조각, Recall@4·MRR·누출 0, MIN_SIM 실측) · G-질문(판정 ≥ 4.5만). 쉬운·모호 질의 자동 필터, 사람 확인 없음", "M-36, M-45~47", "AC-85, AC-90"),
    ("EVP-04", "통과 기준(세 겹)", "① 하드 0건 ② 평균 목표 + CI95 하한(평균 부트스트랩·비율 Wilson) ③ 기준선 대비 0.2점/5%p 넘게 하락 = 회귀(미달). 첫 기준선 2026-11-12, 목표 1회 조정 가능", "전체", "AC-86"),
    ("EVP-05", "평가 방법", "코드(인용 부분 문자열·차단어·중복 코사인·카테고리 분포) + LLM 판정(로컬 qwen3:8b) + 사람 검토", "—", "AC-96"),
    ("EVP-06", "평가 주기", "변경 시(프롬프트·모델·그래프 노드·질문 은행·사내문서) + 주 1회 = QA 에이전트 실행 시점. 결과는 실행기록에 누적", "—", "AC-87"),
    ("EVP-07", "담당 · 수정 루프", "Claude가 실행·기록·판정·수정. 미달 → 프롬프트·검색 파라미터 수정(prompt 버전↑) → 재측정 → 나아질 때만 유지, 전후 수치 보고. 정책 변경은 사용자 승인", "—", "AC-88"),
    ("EVP-08", "사람 검토", "필요한 항목만 10~20개(판정 불일치 → 기준 근처 → 무작위 소량), 문서 근거·질문·채점 기준과 함께, 사용자가 먼저 채점 → M-44. 그 전 M-44 = 미측정", "M-44", "AC-81, AC-89"),
]

# 추적표 하단 블록: AC → (테스트·EV, 지표)
AC_TRACE = {
    "AC-71": ("UT-43, EV-29~63(표본 수·CI·측정일 기록)", "—"), "AC-72": ("EV-29", "M-31"), "AC-73": ("EV-30, EV-31", "M-32, M-33"),
    "AC-74": ("EV-32, EV-33", "M-34, M-35"), "AC-75": ("EV-34, IT-39", "M-36"), "AC-76": ("EV-35, UT-40, UT-41", "M-37, M-18"),
    "AC-77": ("EV-41", "M-38"), "AC-78": ("EV-42, EV-43", "M-39, M-40"), "AC-79": ("EV-44, UT-42", "M-41"), "AC-80": ("EV-45, EV-46", "M-42, M-43"),
    "AC-81": ("EV-49", "M-44"), "AC-82": ("실행기록(기준선 비교), UT-43", "M-31~54"), "AC-83": ("UT-35, UT-39, UT-40, IT-38", "M-37"),
    "AC-84": ("EV-50~53, EV-55~57, EV-58", "M-33, M-50, M-54"), "AC-85": ("EV-34, EV-36~38", "M-36, M-45~47"), "AC-86": ("UT-43", "전체"),
    "AC-87": ("실행기록 RUN", "—"), "AC-88": ("결함관리 QA-27~30(수정 루프 기록)", "—"), "AC-89": ("EV-49", "M-44"),
    "AC-90": ("EV-36, EV-37, EV-38", "M-45, M-46, M-47"), "AC-91": ("EV-39, EV-40, EV-47, EV-48", "M-48, M-49"), "AC-92": ("EV-57, EV-60", "M-50"),
    "AC-93": ("EV-61", "M-51"), "AC-94": ("EV-62, EV-63", "M-52, M-53"), "AC-95": ("EV-54, EV-58, EV-59", "M-54"), "AC-96": ("UT-44", "—"),
}

# ---------------- REGISTRY 추가분 (qa_agent.REGISTRY 열 형식) ----------------
R = lambda *a: list(a)  # noqa: E731
REG_UT = [
    R("UT-39", "LangGraph", "agents/question_graph.py GENERATION·INTERVIEW_STEP · agents/evaluation_graph.py GRAPH", "그래프 3개 노드·엣지 구조 = 문서(ADLC v0.7 6.1·6.1-B·6.3)",
      "EVP-01", "AC-83, AC-84", "—", "langgraph 설치(모델 호출 없음)", "compile 후 get_graph() 노드·엣지(조건 엣지 표시) 비교",
      "1-A 노드 8·엣지 9 / 1-B 4·6(조건 2) / 2 7·10(조건 3)이 문서와 같음", "결정적", "스크립트", "로컬", "P1"),
    R("UT-40", "질문 생성", "backend/app/questions.py PLAN · attempt_questions", "질문 세트 카테고리 분포 2·3·2·1", "QST-01, M-37", "AC-76, AC-83", "attempt_questions",
      "—(GPU 없음)", "PLAN 확인 + DB의 8문항 응시 카테고리 순서 대조", "PLAN = 인성 2·이력서 3·직무 2·기업 1, 순서 위반 0", "결정적", "스크립트", "로컬", "P1"),
    R("UT-41", "질문 생성", "backend/app/questions.py BLOCKLIST · agents/question_graph.py PERSONAL", "차단 목록 단어(나이·결혼·가족·종교·고향·병역·출신·자녀)", "RSQ-03, M-18", "AC-76", "—",
      "—", "금지 질문 8개 / 허용 질문 3개('우선순위' 포함)", "금지 8/8 차단, 허용 오탐 0", "결정적", "스크립트", "로컬", "P1"),
    R("UT-42", "리포트", "backend/app/crew/report_coach.py FORBIDDEN", "리포트 금지어(합불·나이·학벌·성별)", "EML-03, M-41", "AC-79", "—",
      "—", "금지 문장 6개 / 정상 코칭 문장 2개", "금지 6/6 차단, 정상 오탐 0", "결정적", "스크립트", "로컬", "P1"),
    R("UT-43", "평가 플랜", "tools/qa_evalplan.py load_eval_runs · documents/03_QA_doc/eval_runs/", "평가 결과 파일 판독 → 지표·EV 세 겹 판정 기록", "EVP-04, EVP-06, PSP-10", "AC-71, AC-82, AC-86", "eval_runs/*.json",
      "rag-eval·report-eval·golden-set 결과 파일", "eval_runs의 json에서 M-xx 값·CI·n·통과 여부 추출", "파일이 있으면 지표 1개 이상 판독, 없으면 보류(결과 대기)", "결정적", "스크립트(호스트)", "로컬", "P1"),
    R("UT-44", "운영·관측", "backend/ 전체 · backend/app/config.py", "LangSmith 온라인 평가기 미사용 · DEV_MODE=0이면 추적 끔", "6.3.9, PSP-06·09", "AC-96", "—",
      "—", "backend/ 소스에서 langsmith 평가기 API(evaluate·aevaluate·run_evaluator·RunEvaluator) 검색 + config 추적 차단 조건 확인",
      "평가기 호출 0건, config가 DEV_MODE=0에서 LANGSMITH_TRACING을 끔", "결정적", "스크립트(호스트)", "로컬", "P1"),
]
REG_IT = [
    R("IT-39", "사내문서 RAG", "backend/app/internal_store.py · questionbank/vectorDB/chroma", "Chroma 80문서·2,270조각·등급 분리(혼입 0)", "QST-11, EVP-03", "AC-75", "Chroma internal_docs_public·restricted",
      "Chroma 적재 완료(모델 호출 없음)", "두 컬렉션 메타데이터 읽기 → doc_id·disclosure 집계", "문서 80 · 조각 2,270(public 394 · restricted 1,876) · public칸 비공개 0 · restricted칸 공개 0",
      "결정적", "스크립트", "로컬", "P1"),
]


# ======================= 호스트 검사 =======================
def eval_dir(qa_dir):
    return os.path.join(qa_dir, "eval_runs")


def _ci_low(ci):
    if isinstance(ci, (list, tuple)) and ci:
        return ci[0]
    if isinstance(ci, dict):
        return ci.get("lo", ci.get("low"))
    return None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _metric_from(d):
    """지표 dict 하나 → {value, ci_low, n, pass, note}. 모양이 지표마다 달라 흔한 키를 차례로 본다."""
    if not isinstance(d, dict):
        return None
    val = next((_num(d.get(k)) for k in ("value", "mean", "p", "leaks", "rho") if _num(d.get(k)) is not None), None)
    if val is None:                      # 한 단계 안쪽(예: M-31 → redis 검색분)
        for sub in d.values():
            if isinstance(sub, dict) and any(_num(sub.get(k)) is not None for k in ("mean", "p", "value")):
                return _metric_from(sub) | {"note": d.get("note"), "pass": d.get("pass")}
        return None
    ci = d.get("ci") or d.get("ci95") or d.get("wilson95")
    n = next((_num(d.get(k)) for k in ("n", "hits", "queries") if _num(d.get(k)) is not None), None)
    return {"value": val, "ci_low": _num(_ci_low(ci)), "n": n, "pass": d.get("pass"), "note": d.get("note")}


def _walk(obj, out, src):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and re.fullmatch(r"(?:M|EV)-\d{2}", k):
                m = _metric_from(v)
                if m:
                    out.setdefault(k, m | {"src": src})
            else:
                _walk(v, out, src)
        mid = obj.get("id")
        if isinstance(mid, str) and re.fullmatch(r"(?:M|EV)-\d{2}", mid):
            m = _metric_from(obj)
            if m:
                out.setdefault(mid, m | {"src": src})
    elif isinstance(obj, list):
        for x in obj:
            _walk(x, out, src)


def _kind(fname):
    f = fname.lower()
    if "report" in f:
        return "report"
    if "golden" in f or f.startswith("g_"):
        return "golden"
    if "followup" in f:
        return "followup"
    return "rag"


def load_eval_runs(qa_dir):
    """{kind: {"files": [...], "metrics": {M-xx: {...}}}} — 파일이 없으면 {}."""
    res = {}
    for p in sorted(glob.glob(os.path.join(eval_dir(qa_dir), "*.json"))):     # 맨 위 결과 파일만(raw_*/ 중간 산출물은 읽지 않음)
        try:
            obj = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        name = os.path.relpath(p, eval_dir(qa_dir)).replace("\\", "/")
        k = _kind(os.path.basename(p))
        got = {}
        _walk(obj, got, name)
        slot = res.setdefault(k, {"files": [], "metrics": {}})
        slot["files"].append(name)
        for mid, m in got.items():
            slot["metrics"].setdefault(mid, m)
    return res


def host_checks(root, qa_dir):
    import time
    out = {}
    t = time.time()
    runs = load_eval_runs(qa_dir)
    nmet = sum(len(v["metrics"]) for v in runs.values())
    files = [f for v in runs.values() for f in v["files"]]
    out["UT-43"] = {"id": "UT-43", "status": "통과" if nmet else "보류",
                    "actual": (f"결과 파일 {len(files)}개 · 지표 {nmet}개 판독(" + ", ".join(f"{k} {len(v['metrics'])}" for k, v in runs.items()) + ")")
                    if nmet else "eval_runs 결과 파일 없음 — 평가 결과 대기(2단계)", "ms": round((time.time() - t) * 1000)}
    t = time.time()
    pat = re.compile(r"from\s+langsmith(?:\.evaluation)?\s+import\s+[^\n]*\b(evaluate|aevaluate|RunEvaluator|run_evaluator)\b|langsmith\.evaluation|\.run_on_dataset\(")
    hits = []
    for f in glob.glob(os.path.join(root, "backend", "**", "*.py"), recursive=True):
        try:
            if pat.search(open(f, encoding="utf-8").read()):
                hits.append(os.path.relpath(f, root))
        except Exception:
            continue
    cfg = open(os.path.join(root, "backend", "app", "config.py"), encoding="utf-8").read()
    gate = bool(re.search(r"DEV_MODE", cfg)) and bool(re.search(r"LANGSMITH_TRACING", cfg))
    out["UT-44"] = {"id": "UT-44", "status": "통과" if not hits and gate else "실패",
                    "actual": f"온라인 평가기 호출 {len(hits)}건" + (f"({', '.join(hits[:3])})" if hits else "") + f" · config DEV_MODE 추적 차단 코드 {gate}",
                    "ms": round((time.time() - t) * 1000)}
    return out, runs


# ======================= 엑셀 기록 =======================
def _style(src, dst):
    if src.has_style:
        dst.font, dst.fill, dst.border, dst.alignment, dst.number_format = copy(src.font), copy(src.fill), copy(src.border), copy(src.alignment), src.number_format


def _extend_rules(ws, upto):
    """데이터 유효성·조건부 서식 범위의 마지막 행을 upto까지 늘린다(줄이지는 않음)."""
    from openpyxl.formatting.formatting import ConditionalFormattingList

    def grow(rng):
        def rep(m):
            return f"{m.group(1)}{max(int(m.group(2)), upto)}"
        return re.sub(r"(:[A-Z]+)(\d+)", rep, str(rng))
    for dv in ws.data_validations.dataValidation:
        dv.sqref = type(dv.sqref)(grow(dv.sqref))
    new = ConditionalFormattingList()
    for cf in ws.conditional_formatting:
        for rule in cf.rules:
            new.add(grow(cf.sqref), rule)
    ws.conditional_formatting = new


def _row_of(ws, tid, col=1, start=5):
    for r in range(start, ws.max_row + 1):
        if ws.cell(r, col).value == tid:
            return r
    return None


def _metric_formula(r, kind):
    if kind == "rec":
        return f'=IF(I{r}="","미측정","기록")'
    if kind == "alarm":
        return f'=IF(I{r}="","미측정",IF(I{r}>E{r},"경보","기록"))'
    ci = f'IF(R{r}="","달성",IF(S{r}="","보류",IF(S{r}>=R{r},"달성","보류")))'
    return f'=IF(I{r}="","미측정",IF(F{r}="이상",IF(I{r}>=E{r},{ci},"미달"),IF(I{r}<=E{r},"달성","미달")))'


def _ev_formula(r, kind):
    if kind == "rec":
        return f'=IF(I{r}="","미측정","기록")'
    if kind == "alarm":
        return f'=IF(I{r}="","미측정",IF(I{r}>K{r},"경보","기록"))'
    return f'=IF(I{r}="","미측정",IF(L{r}="이상",IF(I{r}>=K{r},"달성","미달"),IF(I{r}<=K{r},"달성","미달")))'


def _fill_metric(ws, r, m, today):
    ws.cell(r, 9).value = m["value"]
    if m.get("n") is not None:
        ws.cell(r, 10).value = m["n"]
    ws.cell(r, 11).value = today
    base = "2026-11-12 첫 기준선" if today == BASELINE_DATE else f"기준선 {BASELINE_DATE} 대비 비교"
    ws.cell(r, 12).value = f"eval_runs/{m['src']} · {base}" + (f" · CI95 하한 {round(m['ci_low'], 3)}" if m.get("ci_low") is not None else "")
    if m.get("ci_low") is not None:
        ws.cell(r, 19).value = m["ci_low"]


def apply_plan(plan, runs, today):
    """계획서: 4_AI품질지표 M-31~54 · 1_범위·전략 블록 · 3_시나리오 · 5_리스크."""
    ws = plan["4_AI품질지표"]
    for i, h in enumerate(EXTRA_HDR, 15):                      # O~S 머리글(기존 1~14열 이름은 그대로)
        ws.cell(4, i).value = h
        _style(ws.cell(4, 14), ws.cell(4, i))
        ws.column_dimensions[chr(64 + i)].width = (16, 18, 22, 12, 12)[i - 15]
    footer = next(r for r in range(5, ws.max_row + 1) if ws.cell(r, 1).value == "요약")
    data_end = max(r for r in range(5, footer) if isinstance(ws.cell(r, 1).value, str) and ws.cell(r, 1).value.startswith("M-"))
    yellow_src = ws.cell(5, 9)
    flat = {k: v for kind in runs.values() for k, v in kind["metrics"].items()}
    for (mid, role, name, unit, goal, direction, defin, data, typ, judge, timing, cilow, note, kind) in METRICS:
        r = _row_of(ws, mid)
        if r is None:
            data_end += 1
            ws.insert_rows(data_end)
            for c in range(1, 15):
                _style(ws.cell(data_end - 1, c), ws.cell(data_end, c))
            r = data_end
        for c, v in enumerate([mid, role, name, unit, goal, direction, defin, data], 1):
            ws.cell(r, c).value = v
        ws.cell(r, 13).value = note
        ws.cell(r, 14).value = _metric_formula(r, kind)
        for c, v in zip(range(15, 19), (typ, judge, timing, cilow)):
            ws.cell(r, c).value = v
            _style(ws.cell(r, 13), ws.cell(r, c))
        _style(yellow_src, ws.cell(r, 19))                      # CI 하한(측정) = 노란 칸
        if mid in flat and mid not in ("M-44",) and kind != "alarm":
            _fill_metric(ws, r, flat[mid], today)
    # 요약 수식: 범위 다시 쓰기 + 보류·기록·경보 행 보장
    rows_by = {ws.cell(r, 1).value: r for r in range(data_end + 1, ws.max_row + 1) if ws.cell(r, 1).value}
    last = max(rows_by.values())
    for lab in ("보류", "기록", "경보"):
        if lab not in rows_by:
            last += 1
            ws.cell(last, 1).value = lab
            _style(ws.cell(rows_by["미측정"], 1), ws.cell(last, 1))
            _style(ws.cell(rows_by["미측정"], 2), ws.cell(last, 2))
            rows_by[lab] = last
    for lab in ("달성", "미달", "미측정", "보류", "기록", "경보"):
        ws.cell(rows_by[lab], 2).value = f'=COUNTIF(N5:N{data_end},"{lab}")'
    ws.cell(rows_by["지표 수"], 2).value = f"=COUNTA(A5:A{data_end})"
    ws.cell(rows_by["측정률"], 2).value = (f'=IFERROR((B{rows_by["달성"]}+B{rows_by["미달"]}+B{rows_by["보류"]}+B{rows_by["기록"]}+B{rows_by["경보"]})'
                                          f'/B{rows_by["지표 수"]},0)')
    _extend_rules(ws, 150)
    # 1_범위·전략 — 평가 플랜 블록(제목 행을 찾아 그 아래를 다시 씀)
    st = plan["1_범위·전략"]
    tr = _row_of(st, STRATEGY_TITLE)
    if tr is None:
        tr = st.max_row + 2
    _style(st.cell(12, 1), st.cell(tr, 1))
    st.cell(tr, 1).value = STRATEGY_TITLE
    for c, h in enumerate(STRATEGY_HDR, 1):
        st.cell(tr + 1, c).value = h
        _style(st.cell(4, c), st.cell(tr + 1, c))
    for i, (eid, item, body, mets, acs) in enumerate(STRATEGY, tr + 2):
        for rng in list(st.merged_cells.ranges):
            if rng.min_row == i:
                st.unmerge_cells(str(rng))
        for c, v in zip((1, 2, 3, 7, 8), (eid, item, body, mets, acs)):
            st.cell(i, c).value = v
        for c in range(1, 9):
            _style(st.cell(5, c), st.cell(i, c))
        st.merge_cells(start_row=i, start_column=3, end_row=i, end_column=6)
    # 3_시나리오 · 5_리스크
    for row in SCENARIOS:
        _upsert(plan["3_시나리오"], list(row))
    rk = plan["5_리스크"]
    for row in RISKS:
        r = _upsert(rk, list(row))
        rk.cell(r, 5).value = f"=C{r}*D{r}"
    _extend_rules(rk, 60)


def _upsert(ws, vals, start=5):
    r = _row_of(ws, vals[0], start=start)
    if r is None:
        last = max(rr for rr in range(start, ws.max_row + 1) if isinstance(ws.cell(rr, 1).value, str) and re.match(r"^[A-Z0-9]+-\d+$", ws.cell(rr, 1).value))
        r = last + 1
        if ws.cell(r, 1).value not in (None, ""):
            ws.insert_rows(r)
        for c in range(1, len(vals) + 1):
            _style(ws.cell(last, c), ws.cell(r, c))
    for c, v in enumerate(vals, 1):
        if v is not None:
            ws.cell(r, c).value = v
    return r


def apply_cases(wb, runs, today):
    """테스트케이스: AI평가 EV-29~63(정의 칸만, 노란 칸은 결과 파일이 있을 때만)."""
    ws = wb["AI평가"]
    src_kind = {"EV-29": "rag", "EV-30": "rag", "EV-31": "rag", "EV-32": "rag", "EV-33": "rag", "EV-34": "rag", "EV-35": "rag",
                "EV-36": "golden", "EV-37": "golden", "EV-38": "golden", "EV-39": "rag", "EV-40": "rag",
                "EV-41": "report", "EV-42": "report", "EV-43": "report", "EV-44": "report", "EV-45": "report", "EV-46": "report",
                "EV-47": "report", "EV-48": "report", "EV-55": "followup", "EV-56": "followup", "EV-57": "followup"}
    for (ev, mid, role, target, model, data, proc, unit, goal, direction, kind, acs, note) in EVS:
        r = _row_of(ws, ev, start=6)
        new = r is None
        if new:
            last = max(rr for rr in range(6, ws.max_row + 1) if isinstance(ws.cell(rr, 1).value, str) and ws.cell(rr, 1).value.startswith("EV-"))
            r = last + 1
            if ws.cell(r, 1).value not in (None, ""):
                ws.insert_rows(r)
            for c in range(1, 18):
                _style(ws.cell(last, c), ws.cell(r, c))
        for c, v in zip((1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12), (ev, mid, role, target, model, "로컬 회사 PC", data, proc, unit, goal, direction)):
            ws.cell(r, c).value = v
        ws.cell(r, 14).value = _ev_formula(r, kind)
        if new or ws.cell(r, 15).value in (None, ""):
            ws.cell(r, 15).value = "미실행"
            ws.cell(r, 17).value = f"{note} · {acs}".strip(" ·")
        k = src_kind.get(ev)
        m = runs.get(k, {}).get("metrics", {}).get(ev if mid == "—" else mid) if k else None
        if m:
            ws.cell(r, 9).value = m["value"]
            if m.get("n") is not None:
                ws.cell(r, 13).value = m["n"]
            ok = m.get("pass")
            ws.cell(r, 15).value = "통과" if ok is True else ("실패" if ok is False else "부분 통과")
            ws.cell(r, 16).value = today
            base = "2026-11-12 첫 기준선" if today == BASELINE_DATE else f"기준선 {BASELINE_DATE} 대비"
            ci = f" · CI95 하한 {round(m['ci_low'], 3)}" if m.get("ci_low") is not None else ""
            ws.cell(r, 17).value = f"{base} · eval_runs/{m['src']}{ci} · {acs}"
    _extend_rules(ws, 150)


def add_run_rows(wb, runs, started, commit):
    """eval_runs 결과 파일마다 실행기록에 RUN 한 줄(이미 기록한 파일은 건너뜀)."""
    s = wb["실행기록"]
    seen = " ".join(str(s.cell(r, 4).value or "") for r in range(5, s.max_row + 1))
    for kind, slot in runs.items():
        for f in slot["files"]:
            if f in seen:
                continue
            mets = {k: v for k, v in slot["metrics"].items() if v.get("src") == f}
            if not mets:
                continue
            s.insert_rows(5)
            runs_n = [s.cell(r, 1).value for r in range(6, s.max_row + 1) if s.cell(r, 1).value]
            ok = sum(1 for v in mets.values() if v.get("pass") is True)
            bad = sum(1 for v in mets.values() if v.get("pass") is False)
            vals = [f"RUN-{len(runs_n) + 1:03d}", started, commit, f"AI 평가({kind}) {f}", len(mets), ok, bad, len(mets) - ok - bad, None, None, "-",
                    "통과" if bad == 0 else "미달 있음", f"{BASELINE_DATE} 첫 기준선 · " + ", ".join(sorted(mets))]
            for i, v in enumerate(vals, 1):
                s.cell(5, i).value = v
                _style(s.cell(6, i), s.cell(5, i))


def trace_block(t, start_row, wb):
    """추적표 하단: AC ↔ EV ↔ M (EV 상태는 AI평가 시트에서)."""
    ev_status = {}
    for name, start in (("AI평가", 6), ("단위테스트", 5), ("통합테스트", 5)):
        ws = wb[name]
        for r in range(start, ws.max_row + 1):
            if ws.cell(r, 1).value:
                ev_status[ws.cell(r, 1).value] = ws.cell(r, 15).value
    t.cell(start_row, 1).value = "AC ↔ EV ↔ 지표 (PRD v0.13 AC-71~96) — QA 에이전트가 매번 다시 만든다"
    for i, h in enumerate(["수용 기준", "테스트·EV", "지표", "통과", "미실행·진행·보류", "커버 판정"], 1):
        t.cell(start_row + 1, i).value = h
        _style(t.cell(4, i), t.cell(start_row + 1, i))
    for i, (ac, (tests, mets)) in enumerate(AC_TRACE.items(), start_row + 2):
        evs = []
        for pre, a, b in re.findall(r"\b(EV|UT|IT)-(\d+)(?:~(\d+))?", tests):
            evs += [f"{pre}-{x:02d}" for x in range(int(a), int(b or a) + 1)]
        st = [ev_status.get(e) for e in evs]
        p = st.count("통과")
        o = sum(1 for x in st if x in (None, "미실행", "진행 중", "보류"))
        f = sum(1 for x in st if x in ("실패", "부분 통과"))
        verdict = "충족" if evs and p == len(evs) else ("결함 있음" if f else ("미검증" if evs else "문서·실행기록"))
        for c, v in enumerate((ac, tests, mets, p, o, verdict), 1):
            t.cell(i, c).value = v


def today_kst():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
