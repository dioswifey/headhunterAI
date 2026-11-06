# headhunterAI API 명세 v0.6

- 엔드포인트 70개 · 원본 `openapi_v0.6.json`(OpenAPI 3) — Swagger UI는 서버 실행 중 `http://localhost:8000/docs`
- 기준 경로 `/api`, 요청·응답 JSON(업로드만 multipart). 인증은 HttpOnly 세션 쿠키 `hh_session`(로그인 시 발급, 2시간 연장)
- 오류 응답: `{"detail": "한국어 메시지"}` · 401 로그인 필요 · 403 권한 없음 · 404 없음(다른 회사 데이터 포함) · 409 상태 충돌 · 429 횟수 제한 · 503 설정·용량 부족
- 권한: 담당자 API는 항상 세션 사용자의 회사 범위로 제한, 지원자 API는 본인 데이터만
- 설명은 코드의 함수 설명(docstring) 첫 줄. 정책 설명은 TRD 4장·PRD 4장

## 인증

| 메서드 | 경로 | 권한 | 파라미터 | 요청 본문 | 설명 |
|---|---|---|---|---|---|
| POST | `/api/auth/change-password` | 로그인 | - | `ChangePw` {current: string, new: string} | 초기 비밀번호 변경(담당자는 변경 전 다른 API 403). ACC-03 |
| POST | `/api/auth/login` | 공개 | - | `Login` {email: string, password: string, role: string} | role=applicant·recruiter. 5회 실패 시 10분 잠금, 계정 존재 여부 비노출. ACC-05 |
| POST | `/api/auth/logout` | 공개(로그인 선택) | - | - | 세션 삭제 |
| GET | `/api/auth/me` | 공개(로그인 선택) | - | - | 현재 사용자(로그인 안 했으면 null) |
| POST | `/api/auth/signup` | 공개 | - | `Signup` {name: string, email: string, password: string, phone: string, age14: boolean, terms: boolean, privacy: boolean} | 지원자 가입(이름·이메일·비밀번호·휴대전화·만 14세·필수 동의). ACC-02 |

## 공개

| 메서드 | 경로 | 권한 | 파라미터 | 요청 본문 | 설명 |
|---|---|---|---|---|---|
| GET | `/api/companies` | 공개 | role?(q) | - | 공고를 올린 기업 목록(공개, APP-10): 기업마다 공개 공고 수와 지금 지원할 수 있는 공고 수(운영 중 직무·접수 중). |
| GET | `/api/job-roles` | 공개 | - | - | 직무 목록(준비 중 포함) |
| GET | `/api/jobs` | 공개 | role?(q), q?(q), company?(q) | - | 게시·마감 공고 목록(직무·기업·검색어 필터, role_status·company_id 포함). APP-01, APP-10 |
| GET | `/api/jobs/{job_id}` | 공개 | job_id(p) | - | 공고 상세·예상 소요·코딩테스트 여부 |
| POST | `/api/jobs/{job_id}/viewing` | 공개 | job_id(p) | - | 공고 상세를 보고 있다는 신호(30초마다). 반환 viewers = 실측 인원, 표시 기준 미만이면 null (presence.py). |
| GET | `/api/system/health` | 공개 | - | - | APP-06 ⑤ AI 서버 상태. |

## 지원자

| 메서드 | 경로 | 권한 | 파라미터 | 요청 본문 | 설명 |
|---|---|---|---|---|---|
| POST | `/api/applications` | 지원자 | - | `Apply` {job_id: integer} | 지원 생성(이미 있으면 기존 건 반환). APP-02 |
| GET | `/api/applications/{app_id}` | 지원자 | app_id(p) | - | 지원 단계·공고·응시 상태 |
| POST | `/api/applications/{app_id}/attempt` | 지원자 | app_id(p) | - | 응시 시작: 동시 응시 상한, 평가 기준 잠금, 질문 스냅샷 생성. INT-08, JOB-07 |
| DELETE | `/api/applications/{app_id}/consent` | 지원자 | app_id(p) | - | 동의 철회 → 진행 중 응시 취소, 데이터 즉시 삭제. |
| POST | `/api/applications/{app_id}/consent` | 지원자 | app_id(p) | `Consent` {items: object} | 5개 항목 동의 기록(버전). APP-03 |
| POST | `/api/applications/{app_id}/env` | 지원자 | app_id(p) | `EnvCheck` {mic: boolean, cam: boolean, speaker: boolean, network: boolean, ai: boolean} | 환경 점검 5항목 통과 기록 → 질문 사전 생성 시작. APP-06, RSQ-05 |
| GET | `/api/applications/{app_id}/report` | 지원자 | app_id(p) | - | 완료 화면 폴링용: 분석 상태 · 결정 방식 · (확정됐으면) 합불 · 리포트 메일 상태. |
| POST | `/api/applications/{app_id}/report/email` | 지원자 | app_id(p) | - | '결과 리포트 이메일로 받기' — 평가가 끝난 건만, 60초 간격 · 응시당 최대 5회. |
| GET | `/api/applications/{app_id}/result` | 지원자 | app_id(p) | - | v0.6: 합불은 확정(final_result)되면 바로 공개. auto = 평가 직후, semi = 담당자 확인 후. 점수는 리포트 메일·화면 공통 정량값. |
| POST | `/api/applications/{app_id}/resume` | 지원자 | app_id(p) | `ResumeConfirm` {data: object, extraction_id?: integer, save_title?: string} | 이력서 확인 완료 스냅샷 저장, 자동값 대비 수정 필드 계산. APP-05, OCR-05 |
| GET | `/api/attempts/{attempt_id}` | 지원자 | attempt_id(p) | - | 현재 상태·현재 질문·남은 시간. 재접속 시 장애/임의 종료 판정 |
| POST | `/api/attempts/{attempt_id}/answer` | 지원자 | attempt_id(p) | `Answer` {question_id: integer, text: string} | 답변 저장 → 꼬리질문 판단 → 다음 질문 또는 코딩/완료 전이. INT-02~07 |
| GET | `/api/attempts/{attempt_id}/coding` | 지원자 | attempt_id(p) | - | 코딩 문제·공개 예제·저장 코드·남은 시간. COD-01 |
| POST | `/api/attempts/{attempt_id}/coding/submit` | 지원자 | attempt_id(p) | - | 최종 제출 → 응시 완료. COD-04 |
| POST | `/api/attempts/{attempt_id}/coding/{problem_id}/run` | 지원자 | attempt_id(p), problem_id(p) | `Code` {code: string} | 공개 예제로 실행(샌드박스). COD-05 |
| POST | `/api/attempts/{attempt_id}/coding/{problem_id}/save` | 지원자 | attempt_id(p), problem_id(p) | `Code` {code: string} | 코드 자동 저장 |
| POST | `/api/attempts/{attempt_id}/events` | 지원자 | attempt_id(p) | `Event` {type: string, detail?: object} | exit · user_exit · paste_block · offline 이벤트. 5.2, RES-04, COD-07 |
| POST | `/api/attempts/{attempt_id}/heartbeat` | 지원자 | attempt_id(p) | - | 5초 주기 생존 신호. 5.1 |
| POST | `/api/attempts/{attempt_id}/resume` | 지원자 | attempt_id(p) | - | RES-02: 이어가기 — 끊긴 질문을 새 답변 시간으로, 코딩은 남은 시간 유지. |
| GET | `/api/me/applications` | 지원자 | - | - | 내 지원 현황·응시·분석 상태·결과 공개 여부·결정 방식 |
| GET | `/api/me/notifications` | 로그인 | - | - | 사이트 내 알림 |
| POST | `/api/me/notifications/read` | 로그인 | - | - | 알림 모두 읽음 |
| GET | `/api/me/resumes` | 지원자 | - | - | 마이페이지 등록 이력서 목록. APP-04 |
| POST | `/api/me/resumes` | 지원자 | - | `ResumeSave` {title: string, data: object} | 이력서 저장 |
| DELETE | `/api/me/resumes/{rid}` | 지원자 | rid(p) | - | 이력서 삭제 |
| GET | `/api/practice/coding` | 지원자 | - | - | - |
| GET | `/api/practice/coding/{pid}` | 지원자 | pid(p) | - | - |
| POST | `/api/practice/coding/{pid}/run` | 지원자 | pid(p) | `Code` {code: string} | - |
| POST | `/api/practice/coding/{pid}/submit` | 지원자 | pid(p) | `Code` {code: string} | - |
| GET | `/api/practice/sql` | 지원자 | subject?(q) | - | - |
| GET | `/api/practice/sql/{pid}` | 지원자 | pid(p) | - | - |
| POST | `/api/practice/sql/{pid}/answer` | 지원자 | pid(p) | `SqlAnswer` {choice: integer} | - |
| GET | `/api/resume/extractions/{xid}` | 지원자 | xid(p) | - | OCR 진행 단계·진행률·쪽별 결과·구조화 결과. OCR-03, 07 |
| GET | `/api/resume/extractions/{xid}/file` | 지원자 | xid(p) | - | 업로드 원본 파일 |
| POST | `/api/resume/uploads` | 지원자 | - | multipart/form-data: file: string | 이력서 업로드(PDF·JPG·PNG, 10MB) → extraction_id, OCR 시작. OCR-01, 6.2 |

## 기업 담당자

| 메서드 | 경로 | 권한 | 파라미터 | 요청 본문 | 설명 |
|---|---|---|---|---|---|
| GET | `/api/admin/applicants` | 기업 담당자 | job_id?(q), result?(q), status?(q) | - | 지원자 목록(공고·결과 pass/fail/pending·상태 필터). ADM-01, DEC-05 |
| POST | `/api/admin/applicants/delete` | 기업 담당자 | - | `DeleteIn` {ids: array} | 지원자 목록에서 선택·전체 삭제(2026-11-06 사용자 지시). 우리 회사 공고의 지원 건만 지운다. |
| GET | `/api/admin/applicants/{app_id}` | 기업 담당자 | app_id(p) | - | 한 페이지 리포트: 결과·점수·근거·질문별 답변·꼬리질문·코드·이벤트·이력서·메모. RPT-01, ADM-02 |
| POST | `/api/admin/applicants/{app_id}/decision` | 기업 담당자 | app_id(p) | `DecisionIn` {action: string, note?: string} | 반자동(semi) 공고: 담당자는 '허용'(그대로 확정) 또는 '판정 변경'만 한다(원클릭). 점수는 바꾸지 않고 AI 판정(result)은 그대로 남는다. |
| POST | `/api/admin/applicants/{app_id}/memos` | 기업 담당자 | app_id(p) | `MemoIn` {body: string} | 내부 메모. ADM-05 |
| GET | `/api/admin/compare` | 기업 담당자 | ids(q) | - | ADM-03: 2~3명 대조, 결과 변경 없음. |
| GET | `/api/admin/files/{file_id}` | 기업 담당자 | file_id(p) | - | 이력서 원본(우리 회사 지원 건만). ADM-02 |
| GET | `/api/admin/jobs` | 기업 담당자 | - | - | 우리 회사 공고 목록 |
| POST | `/api/admin/jobs` | 기업 담당자 | - | `JobIn` {title: string, role_code?: string, company_intro?: string, duties?: string, requirements?: string, preferred?: string, employment_type?: string, location?: string, headcount?: integer, open_date: string, close_date: string} | 공고 생성(차별 소지 표현 경고 반환). JOB-01, 02 |
| POST | `/api/admin/jobs/draft-ai` | 기업 담당자 | - | `DraftIn` {seniority: string, focus: string, stack: string, location: string, extra?: string} | AI 공고 초안. JOB-02 |
| POST | `/api/admin/jobs/import` | 기업 담당자 | - | `ImportIn` {text?: string, url?: string} | JOB-09 (PRD v0.8): 기존 공고 가져오기 — 붙여넣기 또는 링크. 원문을 칸에 옮겨 담기만 하고 내용을 지어내지 않는다. |
| DELETE | `/api/admin/jobs/{job_id}` | 기업 담당자 | job_id(p) | - | 공고 삭제(지원자 있으면 불가) |
| GET | `/api/admin/jobs/{job_id}` | 기업 담당자 | job_id(p) | - | 공고 상세 + 평가 기준 |
| PUT | `/api/admin/jobs/{job_id}` | 기업 담당자 | job_id(p) | `JobIn` {title: string, role_code?: string, company_intro?: string, duties?: string, requirements?: string, preferred?: string, employment_type?: string, location?: string, headcount?: integer, open_date: string, close_date: string} | 공고 수정 |
| POST | `/api/admin/jobs/{job_id}/close` | 기업 담당자 | job_id(p) | - | 마감. JOB-01 |
| PUT | `/api/admin/jobs/{job_id}/criteria` | 기업 담당자 | job_id(p) | `CriteriaIn` {w_expertise: integer, w_logic: integer, w_relevance: integer, w_delivery: integer, w_fit: integer, coding_enabled: boolean, coding_ratio: integer, pass_score: integer, decision_mode?: string} | 가중치·코딩 사용·반영 비중·합격 기준·합불 결정 방식(auto/semi). 잠금 후 409. JOB-03~07, DEC-01 |
| POST | `/api/admin/jobs/{job_id}/publish` | 기업 담당자 | job_id(p) | - | 게시. JOB-01 |
| GET | `/api/admin/notifications` | 기업 담당자 | - | - | 담당자 알림. ADM-08 |
| GET | `/api/admin/shortlist` | 기업 담당자 | job_id?(q) | - | ADM-06: 1차 합격자 + 삭제 예정일. |
| GET | `/api/admin/stats` | 기업 담당자 | - | - | 월별 응시·공고별 합격률(확정 판정 기준)·평균·장애·OCR 집계. ADM-07 |

## 운영자

| 메서드 | 경로 | 권한 | 파라미터 | 요청 본문 | 설명 |
|---|---|---|---|---|---|
| GET | `/api/ops/analysis` | 운영자 | - | - | 운영자: 완료되지 않은 분석 작업. EVL-08 |
| POST | `/api/ops/analysis/{attempt_id}/retry` | 운영자 | attempt_id(p) | - | 운영자: 분석 재처리(시도 횟수 초기화). EVL-08 |

## 개발 전용 (DEV_MODE=1)

| 메서드 | 경로 | 권한 | 파라미터 | 요청 본문 | 설명 |
|---|---|---|---|---|---|
| POST | `/api/dev/publish-now/{job_id}` | 로그인 | job_id(p) | - | 개발 환경 전용: 결과 공개 시각(마감 다음 날 10:00)을 지금으로 당긴다. 운영 빌드(DEV_MODE=0)에는 없음. |
| POST | `/api/dev/reset-my-applications` | 로그인 | app_id?(q) | - | 개발 환경 전용: 지원자 테스트 계정(이지원)만 자기 지원 기록을 지운다(app_id를 주면 그 건만, 없으면 전부)(응시·답변·평가 포함, 이력서 파일은 유지). |

## 정적 화면 (API 밖)

| 경로 | 내용 |
|---|---|
| `/` · `/main.html` | 공통 첫 페이지 (`#/features` · `#/about` · `#/pricing`) |
| `/applicant/` · `/admin/` | 지원자 · 기업 담당자 화면 (해시 라우팅) |
