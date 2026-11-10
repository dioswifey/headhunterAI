# questionbank/ — 구조 절대 규칙 (2026-11-10 사용자 확정)

- **`regularDB/`** — 벡터 아닌 **일반 원본 문서**.
  - `questionbank.xlsx` — 질문 은행 원본(직무 시트: common·ai_ax·backend·data_sci)
  - `character_assessment/` — 인성 질문 md
  - `AI_engineer_questions/` — 외부 AI/AX 210문항 영어 CSV(구 ai-engineering-interview-questions-main)
- **`vectorDB/`** — **임베딩 모델을 거쳐 벡터화된 산출물만**(Chroma 영속 저장 등). 원본 문서 절대 금지.
- **`영찬님/`** — 팀원 '영찬님' corpus(구조 참고 자료).
- **`coding_sql_practice/`** — 코딩·SQL 문제 taxonomy(통계 JSON).

헤드헌터랩 사내문서 20개는 저장소 루트 `internal_documents/`에 있다(questionbank/ 밖, 사용자 지시).

흐름: RAG 파이프라인(ADLC 설계 참고)이 원본(regularDB/·루트 internal_documents/) → 임베딩 모델 →
**Chroma**(영속 저장 `vectorDB/`)로 자동 적재한다. 기존 Redis `qb_idx`(질문은행)는 그대로 유지.
적재 코드: `backend/app/agents/questionbank_agents.py`의 `SOURCES`(regularDB/ 경로)
