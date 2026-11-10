# regularDB — PostgreSQL(관계형 DB)로 적재되는 원본 자료 자리

2026-11-10 `questionbank/` 재정리로 만든 폴더. 지금은 비어 있다.

조사 결과(2026-11-10): 현재 Postgres `question_bank` 테이블은 `backend/app/seed.py`에 하드코딩된
시드 29문항으로 채워지고, 배포본은 `backend/db/seed/bank_question_bank.sql`로 적재된다(둘 다
`questionbank/`의 파일을 직접 읽지 않음). 즉 지금 `questionbank/`에 있던 파일 중 Postgres로
직접 적재되는 "원본 파일"은 없었고, `questionbank.xlsx`·`character_assessment/`·
`ai-engineering-interview-questions-main/`은 모두 `questionbank/vectorDB/`로 옮겼다
(Redis 벡터 저장소 적재 전용, `backend/app/agents/questionbank_agents.py` SOURCES 참고).

앞으로 Postgres에 직접 적재할 원본 자료(예: `question_bank` 시드를 xlsx·CSV 등 파일 기반으로
바꿀 경우)가 생기면 이 폴더에 둔다.

**판단 보류**: `questionbank/coding_sql_practice/`(코딩·SQL taxonomy, 메인 체크아웃에만 있고
아직 git에 커밋 안 됨)는 성격상 regularDB에 가까워 보이지만, 사용자가 이미 별도로 재정리해 둔
결과라 이 작업에서는 옮기지 않았다. 자세한 내용은 2026-11-10 questionbank 재정리 작업 보고 참고.
