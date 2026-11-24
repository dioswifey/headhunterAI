"""QA 에이전트 — 자동 검사 실행 → QA_doc 엑셀에 기록 → 버전 관리 에이전트 호출 (2026-11-06 사용자 지시).

현업 QA 문서 요소를 이 프로젝트에 맞춘 것:
    테스트 계획(범위·환경·시나리오·지표·리스크) · 테스트 케이스(UT·IT·E2E·EV, 사전 조건·절차·예상·실제·상태·결함)
    · 결함 관리(심각도·우선순위·원인·조치·재발 방지) · 요구사항 추적표(요구사항 ↔ 테스트) · 실행 기록(테스트 요약 보고서)
    · 회귀(바뀐 영역의 테스트 재실행) · 커버리지 공백(테스트 없는 모듈) · 버전 관리

    python tools/qa_agent.py run            # 검사 실행 + 엑셀 기록 + 버전 에이전트 apply (기본)
    python tools/qa_agent.py run --dry-run  # 검사만, 엑셀은 안 씀
    python tools/qa_agent.py gaps           # 테스트 없는 모듈·화면 목록만

검사 정의 = 아래 REGISTRY(엑셀 행 내용) + tools/qa_checks.py(컨테이너 안 실행) + 이 파일의 정적 검사.
새 기능을 만들면 REGISTRY와 qa_checks.py에 케이스를 추가한다(ID는 바꾸지 않고 이어 붙임).
엑셀이 다른 프로그램에서 열려 있으면 아무것도 쓰지 않고 멈춘다.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QA = os.environ.get("QA_DOC_DIR") or os.path.join(ROOT, "documents", "03_QA_doc")   # 2026-11 폴더 이동(documents/03_QA_doc).   # 시험할 때만 복사본 폴더로 바꿈
TC_FILE = os.path.join(QA, "headhunterAI_테스트케이스.xlsx")
PLAN_FILE = os.path.join(QA, "headhunterAI_QA_테스트계획서.xlsx")
QA_MD = os.path.join(QA, "QA.md")
API = "headhunterAI-api"
TODAY = dt.date.today().isoformat()
KST = dt.timezone(dt.timedelta(hours=9))

# ---------------- 케이스 정의 (엑셀 행) ----------------
# 단위/통합/E2E 열: TC ID, 계층, 모듈·파일, 대상, 관련 요구사항, 관련 AC, DB·저장소, 사전 조건, 입력·절차, 예상 결과,
#                   판정 방식, 자동화, 환경, 우선순위, (상태, 실제 결과, 소요, 실행일, 결함 ID = 실행 결과)
R = lambda *a: list(a)  # noqa: E731
REGISTRY = {
    "단위테스트": [
        R("UT-20", "코딩 문제은행", "backend/app/agents/coding_verifier_agent.py", "execution_verifier (V1~V7) — 정상 초안", "PRC-08", "AC-49", "—",
          "실행기 컨테이너 동작", "손으로 쓴 정상 초안(가장 긴 오름 구간) 검증", "V1~V7 모두 통과", "결정적", "스크립트", "로컬", "P1"),
        R("UT-21", "코딩 문제은행", "backend/app/agents/coding_verifier_agent.py", "V5 자명성 검사", "PRC-08", "AC-49", "—",
          "실행기 동작", "기준·단순 풀이를 len(nums)로 바꾼 초안", "V5_trivial에서 실패", "결정적", "스크립트", "로컬", "P1"),
        R("UT-22", "코딩 문제은행", "backend/app/agents/coding_verifier_agent.py", "V6 틀린 풀이 검출", "PRC-08", "AC-49", "—",
          "실행기 동작", "빈 입력을 뺀 약한 테스트 초안", "V6_wrong에서 실패(틀린 풀이가 통과)", "결정적", "스크립트", "로컬", "P1"),
        R("UT-23", "코딩 문제은행", "backend/app/agents/coding_verifier_agent.py", "V2 입력 제약 검사", "PRC-08", "AC-49", "—",
          "실행기 동작", "무작위 입력에 'x'를 섞은 초안", "V2_inputs에서 실패", "결정적", "스크립트", "로컬", "P1"),
        R("UT-24", "공고", "backend/app/job_import.py", "import_job — 제목 줄 분리 · 링크 차단", "JOB-09", "AC-45", "—",
          "—", "샘플 공고 본문 붙여넣기 / 127.0.0.1·10.x·file 주소", "칸이 원문 그대로 나뉨, 근무지 줄 분리, 내부·파일 주소 3/3 차단", "결정적", "스크립트", "로컬", "P1"),
        R("UT-25", "공고", "backend/app/presence.py", "beat — 실측 조회 인원 표시 기준", "APP-08", "AC-47", "Redis presence:job:*",
          "Redis 동작(시험 키 999999는 끝에서 삭제)", "서로 다른 IP 1·9·10명, 같은 IP 재신호", "10명 미만 None, 같은 IP 중복 안 셈, 10명 = 10", "결정적", "스크립트", "로컬", "P1"),
        R("UT-26", "코딩 문제은행", "backend/app/agents/coding_specs.py", "framework_specs — 프로그래머스 유형 통계 기반 명세", "PRC-08", "AC-49", "programmers_taxonomy.json",
          "유형 통계 파일", "framework_specs(10, seed=11)", "10개, Junior 6·Senior 4(Lv.0~1 비율), 구조는 10회 이상 나온 것만", "결정적", "스크립트", "로컬", "P2"),
        R("UT-27", "질문 은행", "backend/app/agents/translation_agent.py", "scope — 범위 필터(한국·영어권)", "V8 결정·사용자 지시", "—", "—",
          "스킬 scope_ko_en.md", "Indian languages 질문 / 일반 IT 질문", "앞은 drop(규칙), 뒤는 keep", "결정적", "스크립트", "로컬", "P2"),
        R("UT-28", "정적 검사", "backend/ · tools/", "파이썬 전체 문법(py_compile)", "—", "—", "—",
          "—", "모든 .py를 py_compile", "오류 0", "결정적", "스크립트", "로컬", "P1"),
        R("UT-29", "정적 검사", "main/ · admin/ · applicant/ · shared/", "자바스크립트 문법(node)", "—", "—", "—",
          "node 설치", "모든 .js를 new Function으로 파싱", "오류 0", "결정적", "스크립트", "로컬", "P1"),
        R("UT-30", "코딩 문제은행", "backend/app/agents/coding_quality.py", "eloquence · code_size (품질 기준 Q-02·Q-08)", "PRC-08", "AC-49", "—",
          "—", "자연스러운 문장 / 번역투 문장 / 작은 함수", "자연스러운 문장 80점 이상, 번역투 문장은 더 낮음, 코드 규모 정확", "결정적", "스크립트", "로컬", "P2"),
        R("UT-31", "SQL 문제은행", "backend/app/sqlbank.py", "격리 실행기: 금지 문장 차단 · 앱 DB 표 접근 불가 · 항상 ROLLBACK", "PRC-07", "—", "sqlbank DB",
          "sqlbank_init.sql 적용", "표 만들고 조회 / pg_ 함수 / COMMIT / 앱 표 users 조회 / 실행 후 표 남았는지", "정상 조회 결과, 금지 2건 차단, users 없음, 실행 후 표 없음", "결정적", "스크립트", "로컬", "P1"),
        R("UT-32", "SQL 문제은행", "agents/sql_scope.py · sql_generator_agent.split_sql", "출제 명세 배분(공식 10:40) · SQL 문장 나누기", "PRC-07", "—", "—",
          "—", "plan(60) / 따옴표 안 ; 가 있는 SQL", "1과목 12 · 2과목 48, 세부항목 모두 1개 이상 / 문장 2개", "결정적", "스크립트", "로컬", "P2"),
        R("UT-33", "코딩테스트 보안", "backend/runner/runner.py · coding.sandbox_run(hide_hidden)", "비공개 테스트 격리: 누출 시도 차단 · 기준 풀이 통과", "COD-12", "AC-53", "coding_problems",
          "실행기 컨테이너 동작", "실행 폴더 파일 읽기 / 호출 프레임 조회 / print로 입력 출력 / 게시 문제 기준 풀이 20개", "누출 0건(비공개 케이스 출력·반환값 없음), 기준 풀이 전부 통과", "결정적", "스크립트", "로컬", "P1"),
        R("UT-34", "결제", "backend/app/payments add_month · merchant_user_id · config.PAYMENTS_MODE", "기간 계산 · 사용자 키 · 가짜 결제 차단 규칙", "PAY-07, PAY-10", "AC-56", "—",
          "—", "1월 31일 + 1개월 / 같은 소유자 키 2번 / mock 모드 규칙", "2월 말일, 같은 40자 해시(개인정보 없음), Railway·DEV_MODE=0이면 mock → off", "결정적", "스크립트", "로컬", "P1"),
        R("UT-35", "LangGraph", "backend/app/agents/question_graph.py · backend/app/agents/evaluation_graph.py", "그래프 1·2 구조 · 모델 없이 질문 생성 = 기존 build_set 형식", "QST-01, EVL-01", "—", "question_bank",
          "langgraph 설치", "그래프 노드 목록 / 지원 1건 generate_set(use_llm=False)", "노드가 ADLC 6.1·6.1-B·6.3과 같음, 8문항(인성 2·이력서 3·직무 2·기업 1)·카테고리 순서·출처 종류가 build_set과 같음", "결정적", "스크립트", "로컬", "P1"),
        R("UT-36", "LangGraph", "backend/app/agents/question_graph.py _pick_redis · retrieve", "기본 질문을 Redis 질문 은행(번역본)에서 직무별로 뽑기", "QST-01, LNG-01", "—", "Redis qb_idx",
          "Redis 질문 은행 적재 · 임베딩 모델", "AI/AX 공고 검색어로 직무 2·인성 2·기업 1 검색(questions.PLAN)", "직무 문항은 ai_ax만, 인성은 character_assessment md판(common:CA-), 모두 번역됨(한국어 문장), 중복 그룹 없음", "확률적", "스크립트", "로컬", "P1"),
        R("UT-37", "질문 생성", "questions.py load_character_bank·one_sentence·BLOCKLIST·resume_pairs · agents/translation_agent.py blank_empty·check_wh", "인성 md 은행 · 질문 규칙 · 번역 빈 칸·의문사 보존 (2026-11-09)", "QST-01, RSQ-03, LNG-01", "—", "questionbank/vectorDB/character_assessment",
          "—", "md 읽기 / '우선순위'·'학교 순위' / 두 문장 질문 / 빈 intent 번역 / How 질문 번역 / 이력서 후보 2개", "8문항 이상·필수 칸 있음, 우선순위 허용·학교 순위 차단, 두 문장 실패, 빈 칸은 빈 칸, 의문사 누락 지적, 이력서 질문 3개(같은 항목은 다른 관점)", "결정적", "스크립트", "로컬", "P1"),
        R("UT-38", "배포", "backend/app/bank_seed.py · backend/db/seed/", "문제·질문 은행 배포 파일(코딩·SQL·DB 질문 은행 SQL + Redis 질문 은행) 적재", "PRC-07, PRC-09, QST-01", "—", "coding_problems · sql_problems · question_bank · Redis qb_idx",
          "seed 파일 커밋", "seed 폴더 파일 확인 / 로컬 DB에 apply_sql 다시 실행", "파일 4개 있음, 이미 있는 DB에는 새 행 0(중복 없음). 빈 DB·빈 Redis 적재는 2026-11-09 수동 확인(79·60·29·235)", "결정적", "스크립트", "로컬", "P1"),
    ],
    "통합테스트": [
        R("IT-23", "코딩 연습", "backend/app/routes_applicant.py practice_*", "무제한 코딩 연습 API: 목록→문제→실행→제출", "PRC-07", "AC-49", "coding_problems",
          "테스트 계정 로그인, 게시 문제 1개 이상", "최근 자체 생성 문제에 기준 풀이·시작 코드 제출", "기준 풀이 전체 통과, 시작 코드는 일부만, 숨은 테스트 수 반환", "결정적", "스크립트", "로컬", "P1"),
        R("IT-24", "코딩 연습", "routes_applicant.practice_list · applicant.js", "Junior/Senior 레벨 분리", "PRC-07", "AC-49", "coding_problems.level",
          "로그인", "목록을 level로 나눠 셈", "Junior + Senior = 게시 문제 수", "결정적", "스크립트", "로컬", "P2"),
        R("IT-25", "공고", "backend/app/routes_auth.py jobs", "공고 목록 is_new · viewers", "APP-08, APP-09", "AC-47, AC-48", "jobs, Redis",
          "—", "GET /api/jobs와 DB 규칙 비교", "is_new가 규칙(접수 중 + 7일 이내)과 같고 10명 미만 숫자 없음", "결정적", "스크립트", "로컬", "P1"),
        R("IT-27", "코딩 문제은행", "collected_problems · coding_problem_agent.publish", "외부 수집 문제 게시 게이트", "PRC-08", "AC-49", "collected_problems",
          "—", "DB 집계", "권한 승인 없이 게시된 외부 문제 0", "결정적", "스크립트", "로컬", "P1"),
        R("IT-28", "코딩 문제은행", "generated_problems · coding_generator_agent", "자체 문제 게시 불변식", "PRC-08", "AC-49", "generated_problems",
          "—", "DB 집계", "게시분은 모두 verified, 보류분 게시 0, origin=self_generated", "결정적", "스크립트", "로컬", "P1"),
        R("IT-29", "SQL 연습", "routes_applicant.practice_sql_*", "SQL 연습 API: 목록 → 문제 → 답 확인", "PRC-07", "—", "sql_problems",
          "테스트 계정 로그인, 게시 문제 1개 이상", "목록·문제 조회, 정답/오답 보기로 답 확인", "문제 응답에 answer·hidden·explanation 없음, 정답 보기 correct=true, 오답 false", "결정적", "스크립트", "로컬", "P1"),
        R("IT-30", "SQL 문제은행", "sql_problems · sql_generator_agent", "SQL 문제 게시 불변식", "PRC-07", "—", "sql_problems",
          "—", "DB 집계 + 실행형 문제 정답 재계산", "게시분 verified · 보기 4개 · 정답 1~4, result 문제는 다시 실행해도 정답 보기와 같음", "결정적", "스크립트", "로컬", "P1"),
        R("IT-31", "지원자 관리", "routes_admin.applicants_delete", "지원자 선택·전체 삭제 API", "ADM-01", "—", "applications",
          "담당자 로그인", "빈 목록 / 없는 id", "빈 목록 400, 없는(또는 다른 회사) id는 0건 삭제", "결정적", "스크립트", "로컬", "P2"),
        R("IT-32", "계정", "backend/app/seed.py sync_recruiter_names · ensure_demo_companies", "기업 담당자 계정 이름 = 기업명 · 개발 테스트 기업 3곳", "ACC-07", "AC-50", "users, companies",
          "api 기동 완료", "DB 집계", "모든 recruiter 이름 = 소속 기업명, 헤드헌터랩_서울·경기·인천 각 공고 3개(ai_ax·backend·data_sci)", "결정적", "스크립트", "로컬", "P2"),
        R("IT-33", "공고", "backend/app/routes_auth.py companies · jobs?company", "단계형 공고 API: 기업 목록 → 기업 공고 → 준비 중 직무 지원 거부", "APP-10", "AC-51, AC-52", "jobs, companies",
          "테스트 계정 로그인", "GET /api/companies, /api/jobs?company=, 준비 중 직무 공고로 POST /api/applications", "공고 수 DB와 일치, role_status 포함, 준비 중 직무 지원 400", "결정적", "스크립트", "로컬", "P1"),
        R("IT-34", "결제", "backend/app/routes_payments.py · payments", "네이버페이 자동결제 흐름(가짜 결제 모드)", "PAY-07~09, PAY-12", "AC-56, AC-57, AC-58", "subscriptions, payments",
          "PAYMENTS_MODE=mock(로컬)", "새 시험 계정 가입 → 구독 → 반환 주소 재사용 → 중복 구독 → 갱신 → 해지 → 지원자 환불 호출, 별도 계정 3회 실패", "첫 결제 후 advanced, 재사용 invalid, 중복 409, 갱신 +1개월, 해지 후 기간 유지, 환불 403, 3회 실패 expired", "결정적", "스크립트", "로컬", "P1"),
        R("IT-35", "계정", "backend/app/routes_auth.py withdraw", "회원 탈퇴: 계정 삭제 · 결제 기록 보관 · 담당자 차단", "ACC-10", "—", "users, subscriptions, payments, audit_logs",
          "PAYMENTS_MODE=mock(로컬)", "새 시험 계정 가입·구독 → 틀린 비밀번호 → 탈퇴 → 담당자 탈퇴 호출", "틀린 비밀번호 400, 계정 0건, 구독 canceled·created_by NULL·결제 1건 유지, 감사 로그, 담당자 403", "결정적", "스크립트", "로컬", "P1"),
        R("IT-36", "개인정보", "main/privacy.html · terms.html · 동의 v2", "처리방침·약관 페이지 · 동의 문구 버전", "ACC-08, ACC-09", "AC-59", "consents",
          "—", "GET /privacy, /terms, 화면 3곳 하단 링크, CONSENT_VERSION", "200·200, 세 화면에 legal-links, 버전 2026-11-09.v3", "결정적", "스크립트", "로컬", "P1"),
        R("IT-37", "음성", "backend/app/speech.py · backend/speech", "Qwen3-TTS → Qwen3-ASR 왕복", "INT-01, INT-04", "AC-54, AC-55", "—",
          "음성 컨테이너 기동·모델 적재", "짧은 한국어 문장을 TTS로 만들고 STT로 되돌림", "wav 생성, 되돌린 글이 원문과 같음(문장부호 무시). 음성 서비스가 꺼져 있으면 보류", "결정적", "스크립트", "로컬", "P2"),
        R("IT-38", "LangGraph", "backend/app/agents/question_graph.py answer_step · backend/app/agents/evaluation_graph.py run", "면접 한 걸음(꼬리질문) → 응시 종료 → 그래프 2 평가 저장", "INT-06, EVL-01~06", "AC-06", "attempts, attempt_questions, evaluations",
          "로컬 Qwen 실행, 테스트 지원자 지원 건", "임시 응시(질문 2) → 일반론 답변 → 꼬리질문 답 → 둘째 답 → 평가", "꼬리질문 1개 생성, 응시 completed, evaluations 1행(graph 표시)·question_scores 저장, 임시 응시 삭제", "비결정적(LLM)", "스크립트", "로컬", "P1"),
    ],
    "E2E테스트": [
        R("E2E-17", "코딩 연습 화면", "applicant/#/practice/coding/junior·senior", "레벨 메뉴 → 문제 → 실행 → 제출(채점 모션 1.5초)", "PRC-07", "AC-49", "—",
          "로그인", "브라우저에서 Junior·Senior 메뉴, 문제 풀기, 제출", "레벨별 목록·문제 화면·모션·결과 표시, 390px 폭에서 가로 스크롤 없음", "사람 검토", "브라우저 자동", "로컬", "P2"),
        R("E2E-18", "기업 공고 화면", "admin/ 공고 폼", "① AI 초안 · ② 기존 공고 가져오기", "JOB-02, JOB-09, JOB-10", "AC-45, AC-46", "—",
          "담당자 로그인", "② 버튼 → 본문 붙여넣기 → 가져오기", "칸이 채워지고 자동 게시 안 됨", "사람 검토", "브라우저 자동", "로컬", "P2"),
        R("E2E-19", "공고 화면", "applicant/#/jobs · #/jobs/{id}", "NEW 배지 · 조회 인원 알약", "APP-08, APP-09", "AC-47, AC-48", "—",
          "—", "7일 이내 공고 상세·목록 보기", "NEW 표시, 조회 인원은 10명 이상일 때만", "사람 검토", "브라우저 자동", "로컬", "P3"),
        R("E2E-20", "코딩 연습 진입", "main/#/features/coding-practice · applicant/#/practice/sql", "바로가기 버튼 → 미니 창(알고리즘/SQL) · 영역 전환 · 언어 전환(Python / C 준비 중)", "PRC-07", "AC-49", "—",
          "—", "첫 화면에서 바로가기 버튼(스크롤 없이 보임) 클릭 → 알고리즘/SQL 버튼·설명 확인 → 각 화면 이동, 문제 화면 C 버튼 비활성", "버튼이 첫 화면에 보이고 테두리 빛 효과, 미니 창 2개 버튼·대상 설명, SQL은 준비 중 화면, C는 선택 불가", "사람 검토", "브라우저 자동", "로컬", "P2"),
        R("E2E-21", "SQL 연습 화면", "applicant/#/practice/sql/s1·s2 · #/practice/sql/{id}", "과목 메뉴 → 문제(표·SQL·보기) → 정답 확인·해설", "PRC-07", "—", "—",
          "로그인", "제1·2과목 메뉴, 개념·실행 결과·SQL 고르기 문제 하나씩 풀기", "표·결과 표·SQL 보기가 보이고 해설 번호가 정답 위치와 같음, 390px 폭 가로 스크롤 없음", "사람 검토", "브라우저 자동", "로컬", "P2"),
        R("E2E-22", "지원자 목록", "admin/#/applicants", "체크박스 선택(전체 선택) · 선택 삭제 · 목록 전체 삭제 · 비교", "ADM-01, ADM-03", "—", "—",
          "담당자 로그인", "미완료 지원자 포함 선택, 확인 창에서 취소/삭제", "모든 행 선택 가능, 확인 창 후 삭제, 비교는 분석 완료 2~3명만", "사람 검토", "브라우저 자동", "로컬", "P2"),
        R("E2E-23", "채용 공고 화면", "applicant/#/jobs → role → browse → company · #/jobs/applied", "직무 → 둘러보기/내가 지원한 공고 → 기업 → 공고", "APP-10, ACC-07", "AC-50, AC-51, AC-52", "—",
          "이지원 로그인", "AI/AX → 둘러보기 → 헤드헌터랩_서울 → 백엔드 공고 상세 / 내가 지원한 공고", "준비 중 직무 카드 누를 수 없음, 기업 4곳, 기업 공고 3개 중 준비 중 2개는 지원 버튼 비활성, 담당자 화면 상단 = 기업명", "사람 검토", "브라우저 자동", "로컬", "P2"),
    ],
}
# AI 평가 열: EV ID, 지표 ID, AI 역할, 대상, 모델, 환경, 데이터셋·표본, 절차, (측정값), 단위, 목표, 방향, (표본 수, 판정, 상태, 실행일), 비고
EV_REG = [
    ("EV-17", "M-19", "A10·A11 코딩 문제은행", "검증 통과율", "비율", 0.8, "이상", "현재 OPENAI_MODEL로 만든 generated_problems", "quality_status=verified 비율"),
    ("EV-18", "M-20", "A10·A11 코딩 문제은행", "첫 시도 통과율", "비율", 0.6, "이상", "같음", "수정 없이 통과한 비율"),
    ("EV-19", "M-21", "A10·A11 코딩 문제은행", "문제당 평균 소요", "초", 120, "이하", "같음", "생성+검증 전체 시간 평균"),
    ("EV-20", "M-22", "A10 코딩 문제은행", "통과 1문제당 토큰", "토큰", 15000, "이하", "같음", "입력+출력 토큰 ÷ 통과 수(비용 대리 지표)"),
    ("EV-21", "M-23", "A11 유사도(참고)", "수집 문제와 최대 유사도", "코사인", 0.9, "미만", "verified 전체", "V11 top_collected 최댓값"),
    ("EV-27", "M-29", "A12 SQL 문제은행", "검증 통과율", "비율", 0.8, "이상", "sql_problems 전체", "status=published 비율"),
    ("EV-28", "M-30", "A12 SQL 문제은행", "개념 문제 로컬 풀이 일치율", "비율", 0.8, "이상", "concept 게시분", "S6 로컬 Qwen 블라인드 풀이 = 정답 비율(게시분은 정의상 1 → 생성 기록 전체로 계산)"),
]
# 결함(현업 결함 보고 형식): ID, 심각도, 우선순위, 영역, 발견 경로, 증상, 원인, 조치, 관련 파일, 상태, 발견일, 해결일, 재발 방지, 비고
DEFECTS = [
    R("QA-12", "보통", "P2", "번역", "QA-10 재검사", "'Indian languages' → '인도어들' 뜻 오역", "규칙 검사는 말투만 봄", "범위 필터로 해당 문항 제외(2026-11-05)", "translation_agent.py, scope_ko_en.md", "통과", "2026-11-04", "2026-11-05", "뜻 확인 스킬은 미구현 — 골든셋으로 측정 예정", "UT-27"),
    R("QA-13", "보통", "P1", "코드", "IT-07", "question_chain CLI에서 PoolClosed", "연결 풀이 앱 시작 때만 열림", "CLI main()에서 db.pool.open()", "agents/question_chain.py", "통과", "2026-11-04", "2026-11-04", "새 CLI도 같은 줄 추가(coding_* 에이전트 적용)", ""),
    R("QA-14", "높음", "P1", "코딩 문제은행", "배치 생성", "생성 문제가 지시문 노출·모순 조건·자명한 출력인데 게시됨", "소형 모델 AI 검토만 믿음", "V5 자명성·V6 틀린 풀이·V9 규칙 검사 추가, 해당 2건 게시 취소", "coding_verifier_agent.py", "통과", "2026-11-05", "2026-11-05", "UT-20~23 회귀", "UT-21"),
    R("QA-15", "보통", "P1", "코딩 문제은행", "배치 생성", "모델이 JSON으로 쓴 테스트 입력이 매개변수와 안 맞아 대부분 실패", "입력 형식을 모델이 직접 씀", "입력을 생성 코드(gen_*)로 만들고 validate()로 검사, 매개변수 1개 형식 정리", "coding_verifier_agent.py", "통과", "2026-11-05", "2026-11-05", "UT-23", ""),
    R("QA-16", "보통", "P2", "코딩 문제은행", "OpenAI 시험", "'틀린 풀이'가 사실 맞는 다른 구현이라 V6 반복 실패", "생성 지시 모호", "틀린 풀이 = 기준 풀이 + 버그 1개 + '# 버그:' 주석, 실패 메시지에 코드 포함", "coding_problem_generator.md, coding_verifier_agent.py", "통과", "2026-11-05", "2026-11-06", "UT-22", ""),
    R("QA-17", "낮음", "P2", "코딩 문제은행", "OpenAI 시험", "긴 경계값이 실행기 출력 300자 제한에 걸려 V2 실패", "실행기 repr 300자", "긴 경계값은 버리고 짧은 것 4개 이상 요구(최대 크기는 V7)", "coding_verifier_agent.py", "통과", "2026-11-05", "2026-11-05", "", ""),
    R("QA-18", "보통", "P2", "코딩 문제은행", "OpenAI 시험", "로컬 검토(V10)가 '빈 입력 허용' 명시를 모호함으로 오판", "V9 규칙과 V10 기준 불일치", "검토 지시문에 판단 기준 추가", "coding_problem_review.md", "통과", "2026-11-05", "2026-11-05", "", ""),
    R("QA-19", "낮음", "P3", "코딩 문제은행", "OpenAI 시험", "JSON 오류로 실패한 호출 토큰이 비용에 안 잡힘", "실패 경로에서 usage 버림", "LLMError에 usage를 실어 기록, finish_reason 기록", "llm.py, coding_generator_agent.py", "통과", "2026-11-05", "2026-11-05", "", "EV-20"),
    R("QA-20", "보통", "P2", "성능", "로컬 생성", "qwen3:8b 생성이 문제당 수십 분(12.6 tok/s, VRAM 6GB)", "개발 PC GPU 부족", "문제은행 생성만 OpenAI API(사용자 결정 V8-08)", "llm.py, config.py", "통과", "2026-11-05", "2026-11-05", "집 PC 12GB에서 재측정", "EV-19"),
    R("QA-21", "보통", "P2", "코딩 문제은행", "사람 검토", "자동 검증을 통과한 문제가 널리 알려진 대표 문제(오른쪽 첫 번째 큰 수)", "유사도는 수집 문제와만 비교", "해당 문제 게시 내리고 검토 대기", "generated_problems id 14", "보류", "2026-11-05", "", "대표 문제 목록 대조 또는 사람 검토 필요", "EV-21"),
    R("QA-22", "낮음", "P3", "코딩 문제은행", "유형 통계", "4B 개념 분류가 기출 182 중 161을 '기초 문법'으로 몰아 줌", "소형 모델 분류 한계", "미조치 — 규칙 분류 47건·구조 통계만 신뢰", "coding_taxonomy.py", "보류", "2026-11-05", "", "8B·14B로 재분류", ""),
    R("QA-23", "낮음", "P3", "수집", "수집 에이전트", "LeetCode가 자동 요청에 403", "사이트 봇 차단", "차단 시 중단(우회 안 함) — 정책", "collectors/leetcode.py", "보류", "2026-11-05", "", "ExpertHire 공식 API로 대체 검토", ""),
    R("QA-24", "보통", "P1", "SQL 문제은행", "시험 생성 3문제", "실행 결과형 해설의 보기 번호(①)가 실제 정답 위치(④)와 다름 · 문제 문장에 표·SQL이 중복", "보기 위치를 실행기가 정하는데 모델이 번호를 씀", "해설은 [정답]·[오답1~3] 표시 → 실행기가 번호로 바꿈, stem에 표·SQL 금지 검사(S1)", "sql_generator_agent.py, sql_problem_generator.md", "통과", "2026-11-06", "2026-11-06", "IT-30", ""),
    R("QA-25", "낮음", "P3", "수집", "SQL 유형 통계", "SQLD양파·네이버 블로그·카페·dataq.or.kr이 robots.txt로 자동 수집 금지(SQLD양파는 요청 후 403)", "사이트 정책", "수집하지 않음 — 공식 범위 + 네이버 검색 API(키 필요) 통계로 대체", "agents/sql_scope.py", "보류", "2026-11-06", "", "NAVER_CLIENT_ID·SECRET 발급 후 collect-stats", ""),
    R("QA-26", "보통", "P2", "지원자 관리", "사용자 보고", "지원자 목록에서 분석 미완료 지원자는 체크박스가 비활성이라 선택·삭제 불가", "체크박스를 비교 전용으로만 씀", "모든 행 선택 + 전체 선택 + 선택 삭제·목록 전체 삭제(확인 창), 비교는 분석 완료 2~3명만", "admin/admin.js, routes_admin.py", "통과", "2026-11-06", "2026-11-06", "IT-31, E2E-22", ""),
]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_evalplan as EP  # noqa: E402  — PRD v0.13 평가 플랜·운영 지표(M-31~54, EV-29~63, QA-27~30)

REGISTRY["단위테스트"] += EP.REG_UT
REGISTRY["통합테스트"] += EP.REG_IT
DEFECTS += EP.DEFECTS

METRICS = [  # 계획서 4_AI품질지표: 지표 ID, AI 역할, 지표, 단위, 목표, 방향, 정의·계산, 데이터셋·표본
    ("M-19", "A10·A11 코딩 문제은행", "검증 통과율", "비율", 0.8, "이상", "verified ÷ 생성 기록(현재 모델)", "EV-17"),
    ("M-20", "A10·A11 코딩 문제은행", "첫 시도 통과율", "비율", 0.6, "이상", "attempts=1로 verified ÷ 생성 기록", "EV-18"),
    ("M-21", "A10·A11 코딩 문제은행", "문제당 평균 소요", "초", 120, "이하", "elapsed_sec 평균", "EV-19"),
    ("M-22", "A10 코딩 문제은행", "통과 1문제당 토큰", "토큰", 15000, "이하", "(입력+출력 토큰) ÷ 통과 수", "EV-20"),
    ("M-23", "A11 유사도(참고)", "수집 문제와 최대 유사도", "코사인", 0.9, "이하", "V11 top_collected 최댓값(참고 지표)", "EV-21"),
    ("M-29", "A12 SQL 문제은행", "검증 통과율", "비율", 0.8, "이상", "published ÷ sql_problems 전체", "EV-27"),
    ("M-30", "A12 SQL 문제은행", "개념 문제 로컬 풀이 일치율", "비율", 0.8, "이상", "S6 블라인드 풀이 정답 일치 ÷ 개념 문제 시도", "EV-28"),
]
SCENARIOS = [  # 시나리오 ID, 시나리오, 검증 포인트, 관련 요구사항, 관련 AC, 테스트 케이스, 우선순위
    ("S-11", "자체 코딩 문제은행: 프로그래머스 유형 통계 → 생성(OpenAI) → 실행 검증 → 게시 → 연습 화면", "외부 문제 미사용·검증 통과분만 게시·레벨 분리", "PRC-07, PRC-08", "AC-49", "UT-20~23, UT-26, IT-23·24·27·28, EV-17~21, E2E-17", "P1"),
    ("S-13", "SQL 문제은행: SQLD 공식 범위·비율 → 생성(OpenAI) → 격리 DB 실행 검증·로컬 풀이 대조 → 게시 → SQL 연습 화면", "실행형은 실행기가 정답 계산, 개념형은 로컬 풀이 일치, 정답·해설은 답 고른 뒤", "PRC-07", "—", "UT-31·32, IT-29·30, EV-27·28, E2E-21", "P1"),
    ("S-12", "공고 작성(① AI 초안 · ② 기존 공고 가져오기) → 게시 → 지원자 공고 화면(NEW·조회 인원)", "원문 외 내용 없음·자동 게시 없음·실측 10명 미만 숨김", "JOB-08~10, APP-08·09", "AC-45~48", "UT-24·25, IT-25, E2E-18·19", "P2"),
]
RISKS = [  # 리스크 ID, 내용, 영향, 가능성, 점수, 대응, 관련 지표, 관련 테스트, 담당
    ("R-11", "문제은행 생성의 외부 API 비용·의존(키 노출·요금 변경·장애)", 3, 2, None, "생성에만 사용, 토큰·비용 기록(EV-20), 키는 .env만, 실패 시 로컬 Qwen으로 전환 가능", "M-22", "EV-20", "김소현"),
    ("R-12", "자동 검증을 통과한 문제가 널리 알려진 문제와 같음(독창성)", 3, 3, None, "사람 검토 대기(QA-21), 대표 문제 대조 목록 도입 검토", "M-23", "EV-21", "김소현"),
    ("R-13", "외부 수집 문제의 재사용 권한 미확인", 4, 2, None, "게시 게이트(권한 승인분만), 통계만 사용", "—", "IT-27", "김소현"),
    ("R-14", "SQL 개념 문제(1과목 등)의 정답 오류 — 실행으로 확인할 수 없음", 3, 3, None, "로컬 Qwen 블라인드 풀이 일치 필수(S6), 사람 검토 표본 확인", "M-30", "EV-28", "김소현"),
    ("R-15", "SQLD는 Oracle 문법 기준인데 실행 검증은 PostgreSQL", 2, 3, None, "Oracle 전용 문법 금지·NVL/NVL2 흉내 함수, 해당 주제(PIVOT·CONNECT BY·TCL·DCL)는 개념형", "—", "IT-30", "김소현"),
]
COVERED_MARK = "qa_agent"


# ---------------- 실행 ----------------
def sh(cmd, timeout=900):
    env = dict(os.environ, MSYS_NO_PATHCONV="1", PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return p.returncode, p.stdout, p.stderr


def run_container_checks(no_gpu=False):
    code, _, err = sh(["docker", "cp", os.path.join(ROOT, "tools", "qa_checks.py"), f"{API}:/srv/qa_checks.py"])
    if code:
        return {}, f"컨테이너 검사 불가: {err.strip()[:150]}"
    code, out, err = sh(["docker", "exec", "-e", f"QA_NO_GPU={'1' if no_gpu else '0'}", "-w", "/srv", API, "python", "qa_checks.py"])
    sh(["docker", "exec", API, "rm", "-f", "/srv/qa_checks.py"])
    res = {}
    for line in out.splitlines():
        try:
            d = json.loads(line)
            res[d["id"]] = d
        except Exception:
            continue
    return res, None if res else f"컨테이너 검사 결과 없음: {err.strip()[-200:]}"


def static_checks():
    import py_compile
    res, t = {}, time.time()
    bad = []
    for f in glob.glob(os.path.join(ROOT, "backend", "**", "*.py"), recursive=True) + glob.glob(os.path.join(ROOT, "tools", "*.py")):
        try:
            py_compile.compile(f, doraise=True, cfile=os.devnull if os.name != "nt" else None)
        except Exception as e:
            bad.append(f"{os.path.relpath(f, ROOT)}: {str(e)[:80]}")
    res["UT-28"] = {"id": "UT-28", "status": "통과" if not bad else "실패", "actual": "오류 0" if not bad else "; ".join(bad[:3]),
                    "ms": round((time.time() - t) * 1000)}
    t = time.time()
    js = [f for d in ("main", "admin", "applicant", "shared") for f in glob.glob(os.path.join(ROOT, d, "*.js"))]
    bad = []
    for f in js:
        c, _, e = sh(["node", "-e", f"new Function(require('fs').readFileSync({json.dumps(f)},'utf8'))"], timeout=60)
        if c:
            bad.append(f"{os.path.relpath(f, ROOT)}: {e.strip()[:80]}")
    res["UT-29"] = {"id": "UT-29", "status": "통과" if not bad else "실패",
                    "actual": f"{len(js)}개 파일 오류 0" if not bad else "; ".join(bad[:3]), "ms": round((time.time() - t) * 1000)}
    return res


def coverage_gaps(wb):
    """백엔드 모듈 중 어떤 테스트 케이스의 '모듈·파일' 칸에도 안 나오는 것."""
    text = []
    for name in ("단위테스트", "통합테스트", "E2E테스트"):
        ws = wb[name]
        for r in range(5, ws.max_row + 1):
            text.append(str(ws.cell(r, 3).value or "") + " " + str(ws.cell(r, 4).value or ""))
    blob = " ".join(text)
    mods = sorted(os.path.relpath(f, ROOT).replace("\\", "/") for f in glob.glob(os.path.join(ROOT, "backend", "app", "**", "*.py"), recursive=True)
                  if not f.endswith("__init__.py"))
    gaps = [m for m in mods if os.path.basename(m)[:-3] not in blob and m not in blob]
    return gaps


def changed_files():
    _, out, _ = sh(["git", "status", "--porcelain"])
    return [l[3:] for l in out.splitlines() if l.strip()]


# ---------------- 엑셀 기록 ----------------
def locked():
    return [os.path.basename(p) for p in glob.glob(os.path.join(QA, ".~lock.*#")) + glob.glob(os.path.join(QA, "~$*"))]


def _copy_style(ws, src_row, dst_row, ncol):
    from copy import copy
    for c in range(1, ncol + 1):
        s, d = ws.cell(src_row, c), ws.cell(dst_row, c)
        if s.has_style:
            d.font, d.fill, d.border, d.alignment, d.number_format = copy(s.font), copy(s.fill), copy(s.border), copy(s.alignment), s.number_format


def _find_row(ws, tid, start=5):
    last = start - 1
    for r in range(start, ws.max_row + 1):
        v = ws.cell(r, 1).value
        if v == tid:
            return r, None
        if isinstance(v, str) and re.match(r"^[A-Z0-9]+-\d+$", v):
            last = r
    return None, last


def upsert(ws, row_vals, start=5, keep_human=()):
    """ID(첫 칸)로 행을 찾아 갱신, 없으면 마지막 ID 행 다음에 추가(스타일 복사). keep_human 열은 사람이 쓴 값이 있으면 유지."""
    r, last = _find_row(ws, row_vals[0], start)
    new = r is None
    if new:
        r = last + 1
        if ws.cell(r, 1).value not in (None, ""):
            ws.insert_rows(r)
        _copy_style(ws, last, r, len(row_vals))
    for c, v in enumerate(row_vals, start=1):
        if v is None:
            continue
        if c in keep_human and not new and ws.cell(r, c).value not in (None, ""):
            continue
        ws.cell(r, c).value = v
    return r, new


def _fmt(actual):
    return actual if isinstance(actual, str) else (actual.get("note") or json.dumps(actual, ensure_ascii=False))


def record(results, gaps, changed, started, secs, eval_runs=None):
    import openpyxl
    from openpyxl.utils import get_column_letter
    wb = openpyxl.load_workbook(TC_FILE)
    plan = openpyxl.load_workbook(PLAN_FILE)
    added, updated = [], []
    # 1) 테스트 케이스 + 실행 결과
    for sheet, rows in REGISTRY.items():
        ws = wb[sheet]
        for meta in rows:
            res = results.get(meta[0])
            run = [res["status"], _fmt(res["actual"]), f"{res['ms']}ms", TODAY, None] if res else ["미실행", "자동 검사 없음 — 브라우저 확인 필요", None, None, None]
            # 자동 검사가 없는 행(E2E 등): 사람이 적은 상태·실제 결과·소요·실행일·결함 ID를 덮어쓰지 않는다(새 행·빈 칸만 '미실행')
            human = () if res else tuple(range(len(meta) + 1, len(meta) + len(run) + 1))
            r, new = upsert(ws, meta + run, keep_human=human)
            (added if new else updated).append(meta[0])
    # 기존 케이스 중 재실행한 회귀 항목(UT-03·07·13): 상태·실제 결과·소요·실행일만
    for tid in ("UT-03", "UT-07", "UT-13"):
        res = results.get(tid)
        if not res:
            continue
        ws = wb["단위테스트"]
        r, _ = _find_row(ws, tid)
        if r:
            ws.cell(r, 15).value, ws.cell(r, 16).value, ws.cell(r, 17).value, ws.cell(r, 18).value = res["status"], _fmt(res["actual"]), f"{res['ms']}ms", TODAY
            updated.append(tid)
    # 2) AI 평가
    ws = wb["AI평가"]
    model_name = lambda: "OPENAI_MODEL"  # noqa: E731 — 검사 결과가 없을 때만 쓰는 자리 표시
    for ev, mid, role, target, unit, goal, direction, data, proc in EV_REG:
        res = results.get(ev)
        val = res["actual"].get("value") if res and isinstance(res["actual"], dict) else None
        n = res["actual"].get("n") if res and isinstance(res["actual"], dict) else None
        model = res["actual"].get("model") if res and isinstance(res["actual"], dict) else model_name()
        judge = None if val is None else ("달성" if ((direction == "이상" and val >= goal) or (direction in ("이하",) and val <= goal) or (direction == "미만" and val < goal)) else "미달")
        row = [ev, mid, role, target, model, "로컬 실행기 + OpenAI 생성", data, proc, val, unit, goal, direction, n, judge,
               (res or {}).get("status", "미실행"), TODAY if res else None, (res["actual"].get("note") if res and isinstance(res["actual"], dict) else None)]
        r, new = upsert(ws, row, start=6)
        (added if new else updated).append(ev)
    # 3) 결함
    ws = wb["결함관리"]
    for d in DEFECTS:
        r, new = upsert(ws, d, keep_human=(7, 8, 13))
        (added if new else updated).append(d[0])
    # 4) 실행 기록(테스트 요약 보고서)
    if "실행기록" not in wb.sheetnames:
        s = wb.create_sheet("실행기록", index=wb.sheetnames.index("변경이력"))
        s["A1"] = "실행 기록 (테스트 요약 보고서)"
        s["A2"] = "QA 에이전트(tools/qa_agent.py run)가 실행할 때마다 맨 위(5행)에 한 줄을 넣는다."
        hdr = ["실행 ID", "일시(KST)", "기준 커밋", "실행 범위", "전체", "통과", "실패", "보류·미실행", "소요(초)", "바뀐 파일 수", "테스트 없는 모듈", "판정", "비고"]
        for i, h in enumerate(hdr, 1):
            s.cell(4, i).value = h
            s.column_dimensions[get_column_letter(i)].width = [10, 18, 10, 34, 7, 7, 7, 10, 9, 10, 60, 10, 40][i - 1]
        _copy_style_from(wb["변경이력"], 4, s, 4, len(hdr))
    s = wb["실행기록"]
    s.insert_rows(5)
    ok = sum(1 for v in results.values() if v["status"] == "통과")
    bad = sum(1 for v in results.values() if v["status"] == "실패")
    other = len(results) - ok - bad
    _, commit, _ = sh(["git", "rev-parse", "--short", "HEAD"])
    runs = [s.cell(r, 1).value for r in range(6, s.max_row + 1) if s.cell(r, 1).value]
    rid = f"RUN-{len(runs) + 1:03d}"
    vals = [rid, started, commit.strip(), "자동 검사 " + ", ".join(sorted(results)), len(results), ok, bad, other, secs, len(changed),
            ", ".join(gaps) or "없음", "통과" if bad == 0 else "실패 있음", f"케이스 추가 {len(set(added))} · 갱신 {len(set(updated))}"]
    for i, v in enumerate(vals, 1):
        s.cell(5, i).value = v
    # 5) 요구사항 추적표
    if "추적표" in wb.sheetnames:
        del wb["추적표"]
    t = wb.create_sheet("추적표", index=wb.sheetnames.index("결함관리"))
    t["A1"] = "요구사항 추적표 (요구사항 ↔ 테스트) — QA 에이전트가 매번 다시 만든다"
    t["A2"] = "각 테스트 시트의 '관련 요구사항' 칸에서 뽑는다. 상태 = 그 요구사항에 묶인 테스트 상태 개수."
    for i, h in enumerate(["요구사항 ID", "테스트 케이스", "통과", "실패·부분", "미실행·보류·진행 중", "커버 판정"], 1):
        t.cell(4, i).value = h
    _copy_style_from(wb["변경이력"], 4, t, 4, 6)
    trace = {}
    for name, idcol, reqcol, stcol, st in (("단위테스트", 1, 5, 15, 5), ("통합테스트", 1, 5, 15, 5), ("E2E테스트", 1, 5, 15, 5)):
        ws = wb[name]
        for r in range(st, ws.max_row + 1):
            tid, req, status = ws.cell(r, idcol).value, ws.cell(r, reqcol).value, ws.cell(r, stcol).value
            if not tid or not req:
                continue
            for tok in re.findall(r"[A-Z]{2,4}-\d+(?:~\d+)?", str(req)):
                trace.setdefault(tok, []).append((tid, status))
    for i, (req, items) in enumerate(sorted(trace.items()), start=5):
        st = [s2 for _, s2 in items]
        p = st.count("통과")
        f = sum(1 for x in st if x in ("실패", "부분 통과"))
        o = len(st) - p - f
        t.cell(i, 1).value, t.cell(i, 2).value, t.cell(i, 3).value, t.cell(i, 4).value, t.cell(i, 5).value = req, ", ".join(x for x, _ in items), p, f, o
        t.cell(i, 6).value = "충족" if f == 0 and p > 0 else ("결함 있음" if f else "미검증")
    for col, w in zip("ABCDEF", (16, 60, 7, 10, 14, 10)):
        t.column_dimensions[col].width = w
    EP.apply_cases(wb, eval_runs or {}, TODAY)                     # AI평가 EV-29~63 (+ 평가 결과 파일이 있으면 실제 결과)
    EP.trace_block(t, 5 + len(trace) + 2, wb)                 # 추적표 하단 AC ↔ EV ↔ M
    EP.add_run_rows(wb, eval_runs or {}, started, commit.strip())   # 평가 결과 파일별 RUN
    # 6) 계획서: 지표·시나리오·리스크
    ws = plan["4_AI품질지표"]
    footer = next(r for r in range(5, ws.max_row + 1) if ws.cell(r, 1).value == "달성")
    data_end = max(r for r in range(5, footer) if isinstance(ws.cell(r, 1).value, str) and ws.cell(r, 1).value.startswith("M-"))
    for mid, role, name, unit, goal, direction, defin, data in METRICS:
        res = results.get(data)
        val = res["actual"].get("value") if res and isinstance(res["actual"], dict) else None
        n = res["actual"].get("n") if res and isinstance(res["actual"], dict) else None
        r, _ = _find_row(ws, mid)
        if r is None:
            data_end += 1
            ws.insert_rows(data_end)
            _copy_style(ws, data_end - 1, data_end, 14)
            r = data_end
            footer += 1
        cmp = "<=" if direction in ("이하",) else ">="
        for c, v in enumerate([mid, role, name, unit, goal, direction, defin, data, val, n, TODAY if res else None,
                               (res["actual"].get("note") if res and isinstance(res["actual"], dict) else None), "QA 에이전트 자동 측정"], 1):
            ws.cell(r, c).value = v
        ws.cell(r, 14).value = f'=IF(I{r}="","미측정",IF(F{r}="이상",IF(I{r}>=E{r},"달성","미달"),IF(I{r}{cmp}E{r},"달성","미달")))'
    for lab, f in (("달성", '=COUNTIF(N5:N{e},"달성")'), ("미달", '=COUNTIF(N5:N{e},"미달")'), ("미측정", '=COUNTIF(N5:N{e},"미측정")')):
        rr = next(r for r in range(5, ws.max_row + 1) if ws.cell(r, 1).value == lab)
        ws.cell(rr, 2).value = f.format(e=data_end)
    rows_by = {ws.cell(r, 1).value: r for r in range(data_end + 1, ws.max_row + 1)}
    if "지표 수" in rows_by:
        ws.cell(rows_by["지표 수"], 2).value = f'=COUNTA(A5:A{data_end})'
    if "측정률" in rows_by and all(k in rows_by for k in ("달성", "미달", "지표 수")):   # 행을 끼워 넣으면 옛 행 번호가 남으므로 다시 씀
        ws.cell(rows_by["측정률"], 2).value = f'=IFERROR((B{rows_by["달성"]}+B{rows_by["미달"]})/B{rows_by["지표 수"]},0)'
    EP.apply_plan(plan, eval_runs or {}, TODAY)                    # M-31~54 · 평가 플랜 블록 · S-14~20 · R-16~22
    for row in SCENARIOS:
        upsert(plan["3_시나리오"], list(row))
    for risk_id, *rest in RISKS:
        row = [risk_id] + rest
        r, _ = upsert(plan["5_리스크"], row)
        plan["5_리스크"].cell(r, 5).value = f"=C{r}*D{r}"
    for book in (wb, plan):
        book.calculation.fullCalcOnLoad = True
    wb.save(TC_FILE)
    plan.save(PLAN_FILE)
    return rid, sorted(set(added)), sorted(set(updated))


def _copy_style_from(src_ws, src_row, dst_ws, dst_row, ncol):
    from copy import copy
    for c in range(1, ncol + 1):
        s, d = src_ws.cell(src_row, 1), dst_ws.cell(dst_row, c)
        if s.has_style:
            d.font, d.fill, d.border, d.alignment = copy(s.font), copy(s.fill), copy(s.border), copy(s.alignment)


def update_qa_md():
    """결함 표(QA.md)에 없는 결함 번호를 한 줄씩 추가(상세 원인·조치는 사람이 보강)."""
    text = open(QA_MD, "rb").read().decode("utf-8")
    add = [d for d in DEFECTS if f"| {d[0]} |" not in text]
    if not add:
        return []
    lines = "".join(f"| {d[0]} | {d[9]} | {d[3]} | {d[5]} | {d[10]} |\n" for d in add)
    m = re.search(r"(\| QA-\d+ \|[^\n]*\n)(?![\s\S]*\| QA-\d+ \|)", text)
    text = text[:m.end()] + lines + text[m.end():] if m else text + "\n" + lines
    detail = "".join(f"\n### {d[0]} {d[5][:40]} — {d[9]}\n\n- **발견**: {d[4]} ({d[10]})\n- **원인**: {d[6]}\n- **조치**: {d[7]}\n- **재발 방지**: {d[12] or '—'}\n" for d in add)
    text = text.rstrip("\n") + "\n" + detail
    open(QA_MD, "wb").write(text.encode("utf-8"))
    return [d[0] for d in add]



# ---------------- 모델 A/B (코딩 문제은행 품질) ----------------
AB_METRICS = [  # (키, 표시 이름, 기준표 ID, 좋은 방향)
    ("pass_rate", "검증 통과율", "Q-01·Q-04·Q-05·Q-07", "높음"), ("first_try", "첫 시도 통과율", "Q-09", "높음"),
    ("structure_ok", "입출력 구조 일치율", "Q-01", "높음"), ("eloquence", "문장 자연스러움(규칙 0~100)", "Q-02", "높음"),
    ("sim_collected_max", "수집 문제 최대 유사도(참고)", "Q-03", "낮음"), ("trigram_overlap_max", "수집 문제 3글자 겹침률 최대", "Q-03", "낮음"),
    ("sim_in_batch_max", "묶음 안 최대 유사도(중복)", "Q-03", "낮음"), ("ambiguous", "모호함 판정 수", "Q-04", "낮음"),
    ("examples_ok", "예시 설명 일치율", "Q-04", "높음"), ("tests_mean", "평균 테스트 수", "Q-05", "높음"),
    ("edge_mean", "평균 경계 입력 수", "Q-05", "높음"), ("mutation_mean", "평균 변이 사망률", "Q-05", "높음"),
    ("discrimination", "시간 제한 판별력(단순 풀이 시간 초과 비율)", "Q-06", "높음"), ("validator_ok", "입력 검증 통과율", "Q-07", "높음"),
    ("junior_lines", "Junior 기준 풀이 평균 줄 수", "Q-08", "참고"), ("senior_lines", "Senior 기준 풀이 평균 줄 수", "Q-08", "참고"),
    ("sec_mean", "문제당 평균 소요(초)", "Q-09", "낮음"), ("tokens_per_verified", "통과 1문제당 토큰", "Q-09", "낮음"),
]


def _sheet(wb, name, before=None, title="", note="", header=(), widths=()):
    from openpyxl.utils import get_column_letter
    if name in wb.sheetnames:
        del wb[name]
    ws = wb.create_sheet(name, index=wb.sheetnames.index(before) if before in wb.sheetnames else None)
    ws["A1"], ws["A2"] = title, note
    for i, h in enumerate(header, 1):
        ws.cell(4, i).value = h
        if i <= len(widths):
            ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
    if "변경이력" in wb.sheetnames:
        _copy_style_from(wb["변경이력"], 4, ws, 4, len(header))
    return ws


def ab_record(res):
    import openpyxl
    from openpyxl.styles import PatternFill
    yellow = PatternFill("solid", fgColor="FFF2CC")
    wb = openpyxl.load_workbook(TC_FILE)
    plan = openpyxl.load_workbook(PLAN_FILE)
    a, b = res["a"], res["b"]
    # 1) 계획서: 품질 기준표
    ws = _sheet(plan, "7_문제품질기준", before="변경이력", title="7. 코딩 문제 품질 기준표 (Q-01~Q-09)",
                note="사용자 기준(통계·개념 충실도, 문장 자연스러움, 독창적 재해석) + 업계 출제 기준(HackerEarth·CodeChef 출제 가이드, Polygon·testlib 실무). "
                     "가중치 합 100. 유사도는 참고 지표이며 독창성·법적 적합성을 확정하지 않는다.",
                header=("기준 ID", "기준", "출처", "측정 방법", "자동/사람", "통과선", "가중치"), widths=(9, 22, 40, 50, 20, 30, 8))
    for i, row in enumerate(res["criteria"], 5):
        for c, v in enumerate(row, 1):
            ws.cell(i, c).value = v
    # 2) 테스트케이스: A/B 시트
    ws = _sheet(wb, "AB테스트", before="결함관리", title=f"모델 A/B — A: {a} · B: {b} (같은 명세 10개, seed 11)",
                note=f"QA 에이전트(tools/qa_agent.py ab)가 다시 만든다. 짝 비교 심판 = 로컬 {res['judge']}(순서를 바꿔 두 번, 엇갈리면 '같음'). 외부 API로 채점하지 않는다.",
                header=("지표", "기준표", f"A {a}", f"B {b}", "좋은 방향", "우세"), widths=(40, 18, 16, 16, 10, 10))
    r = 5
    for key, name, q, better in AB_METRICS:
        va, vb = res["A"].get(key), res["B"].get(key)
        win = "-"
        if va == vb:
            win = "같음"
        elif better in ("높음", "낮음") and va is not None and vb is not None:
            win = ("A" if va > vb else "B") if better == "높음" else ("A" if va < vb else "B")
        for c, v in enumerate((name, q, va, vb, better, win), 1):
            ws.cell(r, c).value = v
        r += 1
    r += 1
    ws.cell(r, 1).value = "짝 비교(로컬 심판) 승수"
    r += 1
    for c, h in enumerate(("항목", "A 승", "B 승", "같음"), 1):
        ws.cell(r, c).value = h
    for k, label in (("natural", "자연스러움(Q-02)"), ("concept", "개념 필요(Q-01)"), ("clarity", "명확성(Q-04)")):
        r += 1
        for c, v in enumerate((label, res["wins"]["A"][k], res["wins"]["B"][k], res["wins"]["같음"][k]), 1):
            ws.cell(r, c).value = v
    r += 2
    ws.cell(r, 1).value = "명세별 짝 비교"
    r += 1
    for c, h in enumerate(("명세(개념/레벨/구조)", "A 문제 ID", "B 문제 ID", "자연스러움", "개념 필요", "명확성", "심판 이유"), 1):
        ws.cell(r, c).value = h
    for p in res["pairs"]:
        r += 1
        for c, v in enumerate((p["spec"], p["a_id"], p["b_id"], p["natural"], p["concept"], p["clarity"], p["reason"]), 1):
            ws.cell(r, c).value = v
    r += 2
    ws.cell(r, 1).value = "문제별 상세"
    r += 1
    cols = ("모델", "ID", "레벨", "개념", "제목", "검증", "시도", "초", "토큰", "자연스러움", "번역투", "수집 유사도", "3글자 겹침", "가장 가까운 수집 문제",
            "묶음 안 유사도", "테스트", "경계", "변이", "시간 제한 판별", "풀이 줄")
    for c, h in enumerate(cols, 1):
        ws.cell(r, c).value = h
    for model, det in ((a, res["detail_a"]), (b, res["detail_b"])):
        for x in det:
            r += 1
            vals = (model, x["id"], x["level"], x["concept"], x["title"], "통과" if x["verified"] else "보류", x["attempts"], x["sec"], x["tokens"],
                    x["eloquence"], (x["eloquence_detail"] or {}).get("translationese"), x.get("sim_collected"), x.get("trigram_overlap"),
                    x.get("nearest"), x["sim_in_batch"], x["tests"], x["edge"], x["mutation"],
                    ((x["discrimination"] or {}).get("brute_timeout")), (x["code"] or {}).get("lines"))
            for c, v in enumerate(vals, 1):
                ws.cell(r, c).value = v
    # 3) 테스트케이스: 사람 채점표(노란 칸)
    ws = _sheet(wb, "품질평가_사람", before="결함관리", title="코딩 문제 품질 — 사람 채점표 (노란 칸에 1~5점)",
                note="Q-01 개념 충실 · Q-02 자연스러움 · Q-03 독창적 재해석(유명 문제와 다름) · Q-04 명확성 · Q-08 난이도. 에이전트는 노란 칸 값을 보존한다.",
                header=("모델", "문제 ID", "레벨", "제목", "Q-01 개념", "Q-02 자연스러움", "Q-03 독창성", "Q-04 명확성", "Q-08 난이도", "유명 문제와 비슷함(예/아니오)", "코멘트"),
                widths=(14, 9, 8, 30, 10, 13, 11, 11, 11, 18, 40))
    keep = res.get("_human", {})
    r = 5
    for model, det in ((a, res["detail_a"]), (b, res["detail_b"])):
        for x in det:
            if not x["verified"]:
                continue
            for c, v in enumerate((model, x["id"], x["level"], x["title"]), 1):
                ws.cell(r, c).value = v
            for c in range(5, 12):
                ws.cell(r, c).fill = yellow
                prev = keep.get((model, x["id"]), {}).get(c)
                if prev not in (None, ""):
                    ws.cell(r, c).value = prev
            r += 1
    # 4) AI평가 EV-22~26 · 지표 M-24~28
    A, B = res["A"], res["B"]
    wins_b = res["wins"]["B"]
    ev_rows = [
        ("EV-22", "M-24", "A/B 검증 통과율 차이(B-A)", round((B["pass_rate"] or 0) - (A["pass_rate"] or 0), 3), "비율", 0, "이상"),
        ("EV-23", "M-25", "문장 자연스러움 규칙 점수(B)", B["eloquence"], "점", 80, "이상"),
        ("EV-24", "M-26", "수집 문제 3글자 겹침률 최대(B)", B["trigram_overlap_max"], "비율", 0.3, "이하"),
        ("EV-25", "M-27", "시간 제한 판별력(B)", B["discrimination"], "비율", 0.7, "이상"),
        ("EV-26", "M-28", "짝 비교 B 승(자연스러움+개념+명확성)", wins_b["natural"] + wins_b["concept"] + wins_b["clarity"], "승", 15, "이상"),
    ]
    ws = wb["AI평가"]
    for ev, mid, name, val, unit, goal, direction in ev_rows:
        judge = None if val is None else ("달성" if (val >= goal if direction == "이상" else val <= goal) else "미달")
        upsert(ws, [ev, mid, "A10·A11 코딩 문제은행 A/B", name, f"A {a} · B {b}", "로컬 실행기 + 로컬 Qwen 심판", "같은 명세 10개(seed 11)",
                    "coding_quality.ab", val, unit, goal, direction, A["generated"] + B["generated"], judge,
                    ("통과" if judge == "달성" else "실패") if judge else "미실행", TODAY,
                    f"A {A.get('pass_rate')} / B {B.get('pass_rate')} 통과율"], start=6)
    pl = plan["4_AI품질지표"]
    footer = next(r for r in range(5, pl.max_row + 1) if pl.cell(r, 1).value == "달성")
    data_end = max(r for r in range(5, footer) if isinstance(pl.cell(r, 1).value, str) and pl.cell(r, 1).value.startswith("M-"))
    for ev, mid, name, val, unit, goal, direction in ev_rows:
        rr, _ = _find_row(pl, mid)
        if rr is None:
            data_end += 1
            pl.insert_rows(data_end)
            _copy_style(pl, data_end - 1, data_end, 14)
            rr = data_end
        cmp = "<=" if direction == "이하" else ">="
        for c, v in enumerate([mid, "A10·A11 코딩 문제은행 A/B", name, unit, goal, direction, "tools/qa_agent.py ab", ev, val, 10, TODAY,
                               f"A {a} · B {b}", "모델 비교"], 1):
            pl.cell(rr, c).value = v
        pl.cell(rr, 14).value = f'=IF(I{rr}="","미측정",IF(F{rr}="이상",IF(I{rr}>=E{rr},"달성","미달"),IF(I{rr}{cmp}E{rr},"달성","미달")))'
    rows_by = {pl.cell(r, 1).value: r for r in range(data_end + 1, pl.max_row + 1)}
    for lab in ("달성", "미달", "미측정"):
        if lab in rows_by:
            pl.cell(rows_by[lab], 2).value = f'=COUNTIF(N5:N{data_end},"{lab}")'
    if "지표 수" in rows_by:
        pl.cell(rows_by["지표 수"], 2).value = f'=COUNTA(A5:A{data_end})'
    if "측정률" in rows_by and "달성" in rows_by and "미달" in rows_by and "지표 수" in rows_by:
        pl.cell(rows_by["측정률"], 2).value = f'=IFERROR((B{rows_by["달성"]}+B{rows_by["미달"]})/B{rows_by["지표 수"]},0)'
    # 5) 실행 기록
    if "실행기록" in wb.sheetnames:
        s = wb["실행기록"]
        s.insert_rows(5)
        runs = [s.cell(x, 1).value for x in range(6, s.max_row + 1) if s.cell(x, 1).value]
        commit = sh(["git", "rev-parse", "--short", "HEAD"])[1].strip()
        for c, v in enumerate((f"RUN-{len(runs) + 1:03d}", dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M"), commit,
                               f"모델 A/B {a} vs {b}", A["generated"] + B["generated"], A["verified"] + B["verified"], 0, 0, None, None, "-",
                               "기록", f"통과율 A {A['pass_rate']} · B {B['pass_rate']}"), 1):
            s.cell(5, c).value = v
    for book in (wb, plan):
        book.calculation.fullCalcOnLoad = True
    wb.save(TC_FILE)
    plan.save(PLAN_FILE)


def _human_scores():
    """품질평가_사람 시트의 노란 칸 값(사람 점수)을 보존용으로 읽는다."""
    import openpyxl
    if not os.path.exists(TC_FILE):
        return {}
    wb = openpyxl.load_workbook(TC_FILE)
    if "품질평가_사람" not in wb.sheetnames:
        return {}
    ws = wb["품질평가_사람"]
    return {(ws.cell(r, 1).value, ws.cell(r, 2).value): {c: ws.cell(r, c).value for c in range(5, 12)}
            for r in range(5, ws.max_row + 1) if ws.cell(r, 2).value}


def run_ab(model_a, model_b, dry=False):
    code, out, err = sh(["docker", "exec", API, "python", "-m", "app.agents.coding_quality", "ab", model_a, model_b], timeout=3600)
    if code:
        print("[QA] A/B 실패:", err.strip()[-300:])
        sys.exit(1)
    res = json.loads(out.strip().splitlines()[-1])
    A, B = res["A"], res["B"]
    print(f"[QA] A/B {model_a} vs {model_b}")
    for key, name, q, better in AB_METRICS:
        print(f"   {name:<34} A {A.get(key)!s:<8} B {B.get(key)!s:<8} ({better})")
    print("   짝 비교 승수:", res["wins"])
    json.dump(res, open(os.path.join(ROOT, "tools", ".last_ab.json"), "w", encoding="utf-8"), ensure_ascii=False, default=str)
    if dry:
        return res
    lk = locked()
    if lk:
        print(f"[QA] 엑셀 기록 안 함 — 열려 있음: {', '.join(lk)}. 결과는 tools/.last_ab.json — 닫은 뒤 `python tools/qa_agent.py ab-record`")
        sys.exit(2)
    res["_human"] = _human_scores()
    ab_record(res)
    print("[QA] 기록: 계획서 7_문제품질기준 · 테스트케이스 AB테스트·품질평가_사람·AI평가 EV-22~26 · 지표 M-24~28 · 실행기록")
    return res

def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--no-gpu", action="store_true", help="임베딩·LLM·음성 검사 생략(다른 평가와 GPU 경합 방지)")
    sub.add_parser("gaps")
    ab = sub.add_parser("ab", help="코딩 문제은행 모델 A/B 채점 + 엑셀 기록")
    ab.add_argument("model_a")
    ab.add_argument("model_b")
    ab.add_argument("--dry-run", action="store_true")
    sub.add_parser("ab-record", help="엑셀이 열려 있어 못 쓴 마지막 A/B 결과를 기록")
    a = p.parse_args(argv)
    cmd = a.cmd or "run"
    import openpyxl
    if cmd == "ab":
        run_ab(a.model_a, a.model_b, a.dry_run)
        return
    if cmd == "ab-record":
        res = json.load(open(os.path.join(ROOT, "tools", ".last_ab.json"), encoding="utf-8"))
        if locked():
            sys.exit("[QA] 아직 엑셀이 열려 있음")
        res["_human"] = _human_scores()
        ab_record(res)
        print("[QA] 마지막 A/B 결과 기록 완료")
        return
    if cmd == "gaps":
        print("\n".join(coverage_gaps(openpyxl.load_workbook(TC_FILE, read_only=False))) or "없음")
        return
    started = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    t0 = time.time()
    results, err = run_container_checks(getattr(a, "no_gpu", False))
    results.update(static_checks())
    host, runs = EP.host_checks(ROOT, QA)
    results.update(host)
    secs = round(time.time() - t0, 1)
    ok = sum(v["status"] == "통과" for v in results.values())
    print(f"[QA] 검사 {len(results)}건 · 통과 {ok} · 실패 {sum(v['status'] == '실패' for v in results.values())} · "
          f"보류 {sum(v['status'] == '보류' for v in results.values())} · {secs}초" + (f" · {err}" if err else ""))
    for tid in sorted(results):
        v = results[tid]
        if v["status"] != "통과":
            print(f"   {tid} {v['status']}: {_fmt(v['actual'])[:140]}")
    if getattr(a, "dry_run", False):
        return
    lk = locked()
    if lk:
        print(f"[QA] 엑셀 기록 안 함 — 다른 프로그램에서 열려 있음: {', '.join(lk)} (닫고 다시 실행)")
        sys.exit(2)
    gaps = coverage_gaps(openpyxl.load_workbook(TC_FILE))
    rid, added, updated = record(results, gaps, changed_files(), started, secs, runs)
    md = update_qa_md()
    print(f"[QA] {rid} 기록 · 케이스 추가 {len(added)} · 갱신 {len(updated)} · QA.md 결함 추가 {md or '없음'}")
    print(f"[QA] 테스트 없는 모듈 {len(gaps)}개: {', '.join(gaps) or '없음'}")
    code, out, err2 = sh([sys.executable, os.path.join(ROOT, "tools", "qa_version_agent.py"), "apply"])
    print("[QA] 버전 에이전트:", (out.strip().splitlines() or [err2.strip()])[-1][:200])


if __name__ == "__main__":
    sys.exit(main())
