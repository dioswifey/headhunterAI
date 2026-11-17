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
  -- 코딩테스트 게이트(v0.10, DEC-06~09): AI 면접 점수와 더 이상 가중 평균하지 않는다(coding_ratio 폐지).
  -- none = 게이트 없음(코테 미사용 포함, 면접 점수만으로 판정) / hard = 코딩 60점 미달 시 면접 점수 무관 불합격
  -- soft = 60점 미달이어도 면접 점수 85점 이상이면 합격. 임계값 60·85는 고정값(config.CODING_GATE_SCORE·CODING_GATE_SOFT_OVERRIDE), 공고별 입력 아님
  coding_gate_mode TEXT NOT NULL DEFAULT 'none' CHECK (coding_gate_mode IN ('none','hard','soft')),
  pass_score       INT NOT NULL DEFAULT 70 CHECK (pass_score BETWEEN 0 AND 100),
  -- 합불 결정 방식: auto = AI 판정을 바로 확정·공개, hybrid(v0.6 'semi'에서 v0.10 개명) = 담당자가 AI 판정을 확인한 뒤 확정
  decision_mode    TEXT NOT NULL DEFAULT 'auto' CHECK (decision_mode IN ('auto','hybrid')),
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
  answer_source   TEXT CHECK (answer_source IN ('voice','typed','voice+typed')),  -- v0.9: STT(Qwen3-ASR)·비상 입력란 구분, 음성 파일은 저장 안 함
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

-- 코딩 문제 수집 에이전트(agents/coding_problem_agent.py)가 모은 원본 문제. 지원자 화면에는 안 나감.
-- 재사용 권한 확인(license_status='approved') + 품질 ready + 함수형 Python인 문제만 publish가 coding_problems로 복사
CREATE TABLE collected_problems (
  id                   SERIAL PRIMARY KEY,
  source               TEXT NOT NULL,                 -- 사이트 어댑터 이름: programmers | leetcode
  source_id            TEXT NOT NULL,
  source_url           TEXT NOT NULL,
  title                TEXT NOT NULL,
  statement            TEXT NOT NULL DEFAULT '',
  difficulty           TEXT NOT NULL DEFAULT '',      -- 원본 표기 (Lv.1, Easy …)
  level                TEXT CHECK (level IN ('basic','applied')),
  category             TEXT NOT NULL DEFAULT '',
  kind                 TEXT NOT NULL CHECK (kind IN ('python_func','sql','other')),
  languages            TEXT[] NOT NULL DEFAULT '{}',
  func_name            TEXT,
  params               TEXT[] NOT NULL DEFAULT '{}',
  starter              TEXT NOT NULL DEFAULT '',
  examples             JSONB NOT NULL DEFAULT '[]',   -- [{args, expected, public}] (공개 예제만)
  quality              TEXT NOT NULL CHECK (quality IN ('ready','needs_review')),
  issues               TEXT[] NOT NULL DEFAULT '{}',
  content_hash         TEXT NOT NULL,
  license_status       TEXT NOT NULL DEFAULT 'unverified' CHECK (license_status IN ('unverified','approved','rejected')),
  published_problem_id INT REFERENCES coding_problems(id),
  collected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source, source_id)
);

-- 코딩 문제 생성 에이전트(agents/coding_generator_agent.py, ADLC v0.4 6.11)가 새로 만든 문제. 검증 ①정확성 ②모호함·난이도 ③중복·유사도를
-- 모두 통과한 것만 저장. solution·brute·gen_large는 지원자에게 보이지 않는다. 게시되면 coding_problems로 복사
CREATE TABLE generated_problems (
  id                   SERIAL PRIMARY KEY,
  origin               TEXT NOT NULL DEFAULT 'self_generated',   -- 자체 생성(외부 수집 문제는 collected_problems)
  quality_status       TEXT NOT NULL DEFAULT 'verified' CHECK (quality_status IN ('verified','on_hold','rejected')),  -- 자체 문제 품질(외부 문제의 license_status와 별개)
  concept              TEXT NOT NULL,                 -- topic
  level                TEXT NOT NULL CHECK (level IN ('basic','applied')),   -- difficulty
  learning_objective   TEXT NOT NULL DEFAULT '',
  target_complexity    TEXT NOT NULL DEFAULT '',
  spec                 JSONB NOT NULL,                -- 출제 명세 전체
  title                TEXT NOT NULL,
  statement            TEXT NOT NULL,
  io_spec              TEXT NOT NULL DEFAULT '',
  constraints          TEXT[] NOT NULL DEFAULT '{}',
  examples             JSONB NOT NULL DEFAULT '[]',   -- [{input, expected, explanation}] expected = 실행기가 기준 풀이로 계산
  public_statement     TEXT NOT NULL,                 -- 화면용: 설명 + 입출력 규격 + 제한 사항 + 예시 설명
  func_name            TEXT NOT NULL,
  params               TEXT[] NOT NULL DEFAULT '{}',
  starter              TEXT NOT NULL,
  solution             TEXT NOT NULL,                 -- 기준 정답 코드(비공개)
  solution_explanation TEXT NOT NULL DEFAULT '',
  time_complexity      TEXT NOT NULL DEFAULT '',
  space_complexity     TEXT NOT NULL DEFAULT '',
  brute                TEXT NOT NULL,                 -- 단순 풀이(비공개)
  validator            TEXT NOT NULL DEFAULT '',      -- 제약 조건 검사 함수
  gen_examples         TEXT NOT NULL DEFAULT '',
  gen_edge             TEXT NOT NULL DEFAULT '',
  gen_random           TEXT NOT NULL DEFAULT '',
  gen_large            TEXT NOT NULL,
  wrong_solutions      JSONB NOT NULL DEFAULT '[]',   -- 테스트가 잡아내야 하는 틀린 풀이
  tests                JSONB NOT NULL,                -- [{args, expected, public}] 예시 3개 공개, 경계·무작위는 비공개
  checks               JSONB NOT NULL DEFAULT '{}',
  status               TEXT NOT NULL CHECK (status IN ('pending_review','approved','rejected','on_hold')),  -- 게시 흐름
  review_mode          TEXT NOT NULL CHECK (review_mode IN ('auto','manual')),
  published_problem_id INT REFERENCES coding_problems(id),
  generation_log       JSONB NOT NULL DEFAULT '[]',   -- 생성·수정·예시 설명 이력
  verification_log     JSONB NOT NULL DEFAULT '[]',   -- 검증 V1~V11 결과
  verification_summary JSONB NOT NULL DEFAULT '{}',
  history              JSONB NOT NULL DEFAULT '[]',   -- 전체 이력(두 로그를 합친 것)
  license              TEXT NOT NULL,
  references_used      TEXT[] NOT NULL DEFAULT '{}',
  model                TEXT,
  prompt_version       TEXT,
  content_hash         TEXT NOT NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
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
  writer      TEXT,                                  -- local | template (코칭 문단 작성 경로. 과거 행은 'crewai'로 남아있을 수 있음, 2026-11-17 CrewAI 제거)
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- SQL 문제은행(2026-11-06): SQLD 유형 객관식. 자체 생성(OpenAI) + 실행 검증(격리 DB sqlbank) + 로컬 Qwen 풀이 대조.
-- 수집한 외부 문제 문장은 쓰지 않는다(유형 통계만, agents/sql_scope.py)
CREATE TABLE sql_problems (
  id                   SERIAL PRIMARY KEY,
  origin               TEXT NOT NULL DEFAULT 'self_generated',
  quality_status       TEXT NOT NULL DEFAULT 'verified' CHECK (quality_status IN ('verified','on_hold','rejected')),
  status               TEXT NOT NULL DEFAULT 'published' CHECK (status IN ('published','on_hold','rejected')),
  subject              INT NOT NULL CHECK (subject IN (1,2)),          -- 1 데이터 모델링의 이해 · 2 SQL 기본과 활용
  area                 TEXT NOT NULL,                                  -- 주요항목
  item                 TEXT NOT NULL,                                  -- 세부항목
  kind                 TEXT NOT NULL CHECK (kind IN ('concept','result','sql_pick')),
  title                TEXT NOT NULL,
  stem                 TEXT NOT NULL,                                  -- 문제 문장
  setup_sql            TEXT NOT NULL DEFAULT '',                       -- 표 만들기(화면에는 tables로 보여 줌)
  query_sql            TEXT NOT NULL DEFAULT '',                       -- 문제에 보여 주는 SQL(result)
  tables               JSONB NOT NULL DEFAULT '{}',                    -- 실행기가 만든 표 스냅샷 {이름: {columns, rows}}
  choices              JSONB NOT NULL,                                 -- [{type: text|table|sql, ...}] 4개
  answer               INT NOT NULL CHECK (answer BETWEEN 1 AND 4),
  explanation          TEXT NOT NULL,
  hidden               JSONB NOT NULL DEFAULT '{}',                    -- 정답 계산용(기준 SQL·오답 SQL), 화면에 내보내지 않음
  generation_model     TEXT,
  prompt_version       TEXT,
  generation_log       JSONB NOT NULL DEFAULT '[]',
  verification_log     JSONB NOT NULL DEFAULT '[]',
  usage                JSONB NOT NULL DEFAULT '{}',
  elapsed_sec          INT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 결제 (v0.9, PRD PAY-07~12, TRD v0.6 14장): 네이버페이 자동결제. 카드·계좌 번호와 결제 수단 원문은 저장하지 않는다
CREATE TABLE subscriptions (
  id                  SERIAL PRIMARY KEY,
  owner_type          TEXT NOT NULL CHECK (owner_type IN ('user','company')),
  owner_id            INT NOT NULL,
  plan_code           TEXT NOT NULL,                              -- config.PLANS 키
  amount              INT NOT NULL,                               -- 등록 시점 서버 요금표 금액(원)
  provider            TEXT NOT NULL DEFAULT 'naverpay' CHECK (provider IN ('naverpay','toss','mock')),
  status              TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','active','canceled','past_due','expired','failed')),
  reg_state           TEXT,                                       -- 등록창 왕복 검증용 무작위 값(1회용)
  recurrent_id        TEXT,                                       -- 네이버페이 자동결제 등록 번호(결제 수단 원문 아님)
  current_period_end  TIMESTAMPTZ,                                -- 이 시각까지 Advanced
  next_charge_at      TIMESTAMPTZ,
  retry_count         INT NOT NULL DEFAULT 0,
  created_by          INT REFERENCES users(id),
  canceled_at         TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX subscriptions_one_live ON subscriptions (owner_type, owner_id) WHERE status IN ('active','canceled','past_due');

CREATE TABLE payments (
  id                  SERIAL PRIMARY KEY,
  subscription_id     INT NOT NULL REFERENCES subscriptions(id),
  merchant_pay_id     TEXT NOT NULL UNIQUE,                       -- 우리 주문 번호(멱등 키)
  amount              INT NOT NULL,
  provider            TEXT NOT NULL,
  status              TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','paid','failed','canceled')),
  provider_payment_id TEXT,                                       -- 네이버페이 결제 번호(paymentId)
  period_start        TIMESTAMPTZ,
  period_end          TIMESTAMPTZ,
  fail_code           TEXT,
  fail_message        TEXT,
  paid_at             TIMESTAMPTZ,
  canceled_at         TIMESTAMPTZ,
  receipt             JSONB NOT NULL DEFAULT '{}',                -- 결제 일시·금액·수단 종류만(카드 번호·계좌 번호 제외)
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON subscriptions (status, next_charge_at);
CREATE INDEX ON payments (subscription_id, created_at);

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
CREATE INDEX ON sql_problems (status, subject, item);
