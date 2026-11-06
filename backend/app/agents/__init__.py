"""AI 작업 흐름 (ADLC v0.3 6장).

- state.py: 그래프 1 State 계약 (LangGraph 그래프 코드는 [제안])
- translation_agent.py: 언어 번역 에이전트 (Skills 패턴, 스킬 = prompts/skills/translation/*.md 자동 등록)
- questionbank_agents.py: 질문 은행 → Redis 적재 에이전트 · 동기화 에이전트 (LCEL, v0.7)
- question_chain.py: Redis 검색 결과 → prompts/generate/question_templates.md → 회사 맞춤 질문 (LCEL, v0.7)
"""
