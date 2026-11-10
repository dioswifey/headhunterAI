-- 수동 마이그레이션(v0.10 합불 판정 재설계): coding_ratio 폐지 → coding_gate_mode, decision_mode 'semi' → 'hybrid'.
-- schema.sql의 새 정의와 기존 DB를 맞출 때만 실행한다. 빈 DB로 새로 기동하면 schema.sql이 이미 새 정의로 만들므로 필요 없음.
-- 로컬: docker exec -i AI-Interview-project psql -U headhunter -d headhunter -f /dev/stdin < backend/db/migrate_coding_gate.sql
-- Railway: railway run --service api -- psql "$DATABASE_URL" -f backend/db/migrate_coding_gate.sql (실행 전 사용자 확인)

ALTER TABLE eval_criteria ADD COLUMN IF NOT EXISTS coding_gate_mode TEXT NOT NULL DEFAULT 'none'
  CHECK (coding_gate_mode IN ('none','hard','soft'));

UPDATE eval_criteria SET decision_mode = 'hybrid' WHERE decision_mode = 'semi';

ALTER TABLE eval_criteria DROP CONSTRAINT IF EXISTS eval_criteria_decision_mode_check;
ALTER TABLE eval_criteria ADD CONSTRAINT eval_criteria_decision_mode_check CHECK (decision_mode IN ('auto','hybrid'));

ALTER TABLE eval_criteria DROP COLUMN IF EXISTS coding_ratio;
