-- headhunterAI schema (TRD v0.1 §3 ERD와 1:1 대응). 변경 시 TRD ERD도 같이 고친다.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE companies (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL,
  intro       TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id                    SERIAL PRIMARY KEY,
  role                  TEXT NOT NULL CHECK (role IN ('applicant','recruiter','operator')),
  company_id            INT REFERENCES companies(id),
  email                 TEXT NOT NULL UNIQUE,
  password_hash         TEXT NOT NULL,
  name                  TEXT NOT NULL,
  phone                 TEXT,
  email_verified        BOOLEAN NOT NULL DEFAULT false,
  age14_confirmed       BOOLEAN NOT NULL DEFAULT false,
  must_change_password  BOOLEAN NOT NULL DEFAULT false,
  failed_logins         INT NOT NULL DEFAULT 0,
  locked_until          TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (role <> 'recruiter' OR company_id IS NOT NULL)
);

CREATE TABLE sessions (
  token       TEXT PRIMARY KEY,
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at  TIMESTAMPTZ NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE job_roles (
  code    TEXT PRIMARY KEY,
  name    TEXT NOT NULL,
  status  TEXT NOT NULL CHECK (status IN ('active','preparing')),
  sort    INT NOT NULL DEFAULT 0
);

CREATE TABLE jobs (
  id               SERIAL PRIMARY KEY,
  company_id       INT NOT NULL REFERENCES companies(id),
  role_code        TEXT NOT NULL REFERENCES job_roles(code),
  title            TEXT NOT NULL,
  company_intro    TEXT NOT NULL DEFAULT '',
  duties           TEXT NOT NULL DEFAULT '',
  requirements     TEXT NOT NULL DEFAULT '',
  preferred        TEXT NOT NULL DEFAULT '',
  employment_type  TEXT NOT NULL DEFAULT '정규직',
  location         TEXT NOT NULL DEFAULT '',
  headcount        INT NOT NULL DEFAULT 1,
  open_date        DATE NOT NULL,
  close_date       DATE NOT NULL,
  status           TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','closed')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 평가 기준: 공고당 1행, 첫 응시 시작 시 locked_at 기록 (JOB-07)
CREATE TABLE eval_criteria (
  job_id           INT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  version          INT NOT NULL DEFAULT 1,
  w_expertise      INT NOT NULL DEFAULT 30,
  w_logic          INT NOT NULL DEFAULT 25,
  w_relevance      INT NOT NULL DEFAULT 20,
  w_delivery       INT NOT NULL DEFAULT 10,
  w_fit            INT NOT NULL DEFAULT 15,
  coding_enabled   BOOLEAN NOT NULL DEFAULT true,
  coding_ratio     INT NOT NULL DEFAULT 30 CHECK (coding_ratio BETWEEN 10 AND 90 AND coding_ratio % 10 = 0),
  pass_score       INT NOT NULL DEFAULT 70 CHECK (pass_score BETWEEN 0 AND 100),
  -- 합불 결정 방식 (v0.6): auto = AI 판정을 바로 확정·공개, semi = 담당자가 AI 판정을 확인한 뒤 확정
  decision_mode    TEXT NOT NULL DEFAULT 'auto' CHECK (decision_mode IN ('auto','semi')),
  locked_at        TIMESTAMPTZ,
  CHECK (w_expertise + w_logic + w_relevance + w_delivery + w_fit = 100),
  CHECK (LEAST(w_expertise, w_logic, w_relevance, w_delivery, w_fit) >= 5)
);

CREATE TABLE files (
  id            SERIAL PRIMARY KEY,
  owner_id      INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL CHECK (kind IN ('resume_original','ocr_page','recording')),
  filename      TEXT NOT NULL,
  mime          TEXT NOT NULL,
  size_bytes    INT NOT NULL,
  storage_path  TEXT NOT NULL,
  expires_at    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 이력서 인식 작업 (OCR-01~07): 업로드 1건 = 1행
CREATE TABLE resume_extractions (
  id               SERIAL PRIMARY KEY,
  user_id          INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  file_id          INT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  status           TEXT NOT NULL DEFAULT 'processing' CHECK (status IN ('processing','review','confirmed','failed')),
  stage            TEXT NOT NULL DEFAULT 'text',      -- text | ocr | structure | done
  progress         INT NOT NULL DEFAULT 0,
  pages            JSONB NOT NULL DEFAULT '[]',       -- [{page, method:'text'|'ocr', confidence, text}]
  raw_text         TEXT NOT NULL DEFAULT '',
  structured       JSONB,                             -- AI 구조화 결과(자동 추출값)
  ocr_engine       TEXT,
  failure_reason   TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at       TIMESTAMPTZ NOT NULL DEFAULT now() + interval '24 hours'
);

-- 마이페이지 등록 이력서 (탈퇴 시까지)
CREATE TABLE resumes (
  id          SERIAL PRIMARY KEY,
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title       TEXT NOT NULL,
  data        JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE applications (
  id                SERIAL PRIMARY KEY,
  job_id            INT NOT NULL REFERENCES jobs(id),
  user_id           INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  step              TEXT NOT NULL DEFAULT 'consent' CHECK (step IN ('consent','resume','env','ready','started')),
  resume_snapshot   JSONB,                          -- 지원자 확인 완료본 (APP-05, OCR-05)
  resume_edits      JSONB,                          -- 자동 추출값 대비 지원자 수정 필드 목록
  extraction_id     INT REFERENCES resume_extractions(id) ON DELETE SET NULL,
  pregen_status     TEXT NOT NULL DEFAULT 'none' CHECK (pregen_status IN ('none','running','ready','failed')),
  pregen_questions  JSONB,                          -- 환경 점검 단계에서 미리 만든 질문 세트 (RSQ-05)
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (job_id, user_id)                          -- 중복 지원 차단 (APP-02)
);

CREATE TABLE consents (
  id              SERIAL PRIMARY KEY,
  user_id         INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  application_id  INT REFERENCES applications(id) ON DELETE CASCADE,
  scope           TEXT NOT NULL CHECK (scope IN ('signup','application')),
  version         TEXT NOT NULL,
  items           JSONB NOT NULL,
  agreed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  withdrawn_at    TIMESTAMPTZ
);

-- 응시: 지원 건당 1회 (APP-02)
CREATE TABLE attempts (
  id                SERIAL PRIMARY KEY,
  application_id    INT NOT NULL UNIQUE REFERENCES applications(id) ON DELETE CASCADE,
  status            TEXT NOT NULL DEFAULT 'ready'
                    CHECK (status IN ('ready','interview','coding','interrupted','completed','terminated','cancelled')),
  phase             TEXT NOT NULL DEFAULT 'interview' CHECK (phase IN ('interview','coding')),
  criteria_version  INT,
  criteria_snapshot JSONB,
  current_seq       INT NOT NULL DEFAULT 1,
  followups_used    INT NOT NULL DEFAULT 0,
  exit_count        INT NOT NULL DEFAULT 0,
  interrupt_count   INT NOT NULL DEFAULT 0,
  last_heartbeat    TIMESTAMPTZ,
  user_exit_at      TIMESTAMPTZ,                    -- beforeunload/pagehide 기록 (RES-04 판정)
  coding_deadline   TIMESTAMPTZ,
  coding_remaining  INT,                            -- 장애 중단 시 남은 초 (COD-04)
  started_at        TIMESTAMPTZ,
  completed_at      TIMESTAMPTZ,
  analysis_status   TEXT NOT NULL DEFAULT 'none'
                    CHECK (analysis_status IN ('none','queued','running','done','needs_check')),
  publish_at        TIMESTAMPTZ,
  publish_notified  BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE question_bank (
  id           SERIAL PRIMARY KEY,
  category     TEXT NOT NULL CHECK (category IN ('personality','company','job')),
  subcategory  TEXT NOT NULL DEFAULT '',
  text         TEXT NOT NULL,
  source       TEXT NOT NULL DEFAULT 'db' CHECK (source IN ('db','generated')),
  dup_group    TEXT,
  use_count    INT NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 출제 질문 스냅샷 (QST-03). 꼬리질문은 parent_id로 원 문항에 연결
CREATE TABLE attempt_questions (
  id              SERIAL PRIMARY KEY,
  attempt_id      INT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  seq             INT NOT NULL,
  parent_id       INT REFERENCES attempt_questions(id) ON DELETE CASCADE,
  category        TEXT NOT NULL CHECK (category IN ('personality','resume','company','job','followup')),
  eval_items      TEXT[] NOT NULL,
  text            TEXT NOT NULL,
  bank_id         INT REFERENCES question_bank(id) ON DELETE SET NULL,
  resume_ref      JSONB,                            -- {section, index, label, quote} (RSQ-02)
  gen_method      TEXT NOT NULL DEFAULT 'db',       -- db | llm | template | fallback_job
  time_limit      INT NOT NULL DEFAULT 90,
  answer_text     TEXT,
  answer_status   TEXT NOT NULL DEFAULT 'pending' CHECK (answer_status IN ('pending','answered','no_answer')),
  answered_at     TIMESTAMPTZ,
  prescore        JSONB,
  UNIQUE (attempt_id, seq, parent_id)
);

CREATE TABLE coding_problems (
  id          SERIAL PRIMARY KEY,
  level       TEXT NOT NULL CHECK (level IN ('basic','applied')),
  title       TEXT NOT NULL,
  statement   TEXT NOT NULL,
  starter     TEXT NOT NULL,
  func_name   TEXT NOT NULL,
  tests       JSONB NOT NULL                       -- [{args, expected, public}]
);

CREATE TABLE coding_submissions (
  id            SERIAL PRIMARY KEY,
  attempt_id    INT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  problem_id    INT NOT NULL REFERENCES coding_problems(id),
  code          TEXT NOT NULL DEFAULT '',
  saved_at      TIMESTAMPTZ,
  submitted_at  TIMESTAMPTZ,
  passed        INT,
  total         INT,
  paste_blocked INT NOT NULL DEFAULT 0,
  UNIQUE (attempt_id, problem_id)
);

-- 장애·이탈·응시 이벤트 (RES-06, CHT-05)
CREATE TABLE attempt_events (
  id          SERIAL PRIMARY KEY,
  attempt_id  INT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  type        TEXT NOT NULL,
  detail      JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 분석 작업 큐 (EVL-08): 응시당 1행 → 중복 결과 방지
CREATE TABLE analysis_jobs (
  attempt_id   INT PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE,
  status       TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','needs_check')),
  tries        INT NOT NULL DEFAULT 0,
  next_run_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_error   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE evaluations (
  attempt_id        INT PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE,
  item_scores       JSONB NOT NULL,                -- {expertise: 80.0, ...}
  interview_score   NUMERIC(5,2) NOT NULL,
  coding_score      NUMERIC(5,2),
  total_score       NUMERIC(5,2) NOT NULL,
  result            TEXT NOT NULL CHECK (result IN ('pass','fail')),   -- AI(코드 산출) 판정, 바꾸지 않는다
  reason            TEXT,
  final_result      TEXT CHECK (final_result IN ('pass','fail')),     -- 확정 판정. auto는 산출 즉시, semi는 담당자 확인 후. NULL = 확인 대기
  decided_by        INT REFERENCES users(id),                         -- NULL + decided_at 있음 = 자동 확정
  decided_at        TIMESTAMPTZ,
  decision_note     TEXT,                                             -- 담당자가 판정을 바꾼 경우 메모(선택)
  criteria_snapshot JSONB NOT NULL,
  versions          JSONB NOT NULL,                -- {model, prompt, rubric, ocr}
  strengths         JSONB NOT NULL DEFAULT '[]',
  improvements      JSONB NOT NULL DEFAULT '[]',
  summary           TEXT NOT NULL DEFAULT '',
  code_feedback     TEXT,
  public_feedback   JSONB NOT NULL,                -- 지원자 공개용 (RPT-03/04)
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE question_scores (
  id            SERIAL PRIMARY KEY,
  question_id   INT NOT NULL REFERENCES attempt_questions(id) ON DELETE CASCADE,
  eval_item     TEXT NOT NULL,
  score         INT NOT NULL CHECK (score BETWEEN 0 AND 5),
  quote         TEXT,
  verified      BOOLEAN NOT NULL DEFAULT false,
  rationale     TEXT,
  UNIQUE (question_id, eval_item)
);

CREATE TABLE memos (
  id              SERIAL PRIMARY KEY,
  application_id  INT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  author_id       INT NOT NULL REFERENCES users(id),
  body            TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE notifications (
  id          SERIAL PRIMARY KEY,
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type        TEXT NOT NULL,
  title       TEXT NOT NULL,
  body        TEXT NOT NULL DEFAULT '',
  read_at     TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 이메일 발송 기록 (v0.6): 지원자 정량 리포트. 본문은 저장하지 않는다
CREATE TABLE email_logs (
  id          SERIAL PRIMARY KEY,
  attempt_id  INT REFERENCES attempts(id) ON DELETE CASCADE,
  user_id     INT REFERENCES users(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,                         -- applicant_report
  to_addr     TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('sent','failed')),
  writer      TEXT,                                  -- crewai | template (코칭 문단 작성 경로)
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 감사·삭제 로그: 원문 없이 식별자·시각·행위만 (6.1, 3년)
CREATE TABLE audit_logs (
  id           BIGSERIAL PRIMARY KEY,
  actor_id     INT,
  action       TEXT NOT NULL,
  target_type  TEXT NOT NULL,
  target_id    TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON jobs (company_id, status);
CREATE INDEX ON applications (user_id);
CREATE INDEX ON attempts (status);
CREATE INDEX ON attempt_questions (attempt_id, seq);
CREATE INDEX ON attempt_events (attempt_id, created_at);
CREATE INDEX ON analysis_jobs (status, next_run_at);
CREATE INDEX ON question_bank (category, use_count);
CREATE INDEX ON email_logs (attempt_id, created_at);
