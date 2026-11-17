"""AI 작업 흐름 (ADLC v0.3 6장).

- state.py: 그래프 1 State 계약 (LangGraph 그래프 코드는 [제안])
- translation_agent.py: 언어 번역 에이전트 (Skills 패턴, 스킬 = prompts/skills/translation/*.md 자동 등록)
- questionbank_agents.py: 질문 은행 → Redis 적재 에이전트 · 동기화 에이전트 (LCEL, v0.7)
- question_chain.py: Redis 검색 결과 → prompts/generate/question_templates.md → 회사 맞춤 질문 (LCEL, v0.7)
- report_coach.py: 지원자 정량 리포트 메일의 코치 코멘트 (v0.11, 2026-11-17 `crew/`에서 이동 — CrewAI 제거,
  다른 evaluate/generate 모듈과 같은 llm.chat_json 1회 직접 호출 패턴. 메일 발송 흐름 밖의 "메일 생성" 호출이지
  그래프 1·1-B·2 어디에도 안 들어간다)
- decision_notice.py: 합불 통보 메일 문구 (v0.11, 2026-11-17 `crew/`에서 이동, 같은 이유)
"""
