"""AI 리포트 품질 — 확률적 측정 (지표 M-38~M-44, 2026-11-12).

제품 코드·제품 프롬프트는 고치지 않는다. 가상 응시(질문 8 + 답변)를 만들어 제품 경로를 그대로 통과시킨다.

  그래프 2 노드(agents/evaluation_graph): evaluate_blocks(A4 채점·인용) → compute(코드 점수) → summarize(강점·개선점·총평) → decide
  → report_mail._coach(crew/report_coach.py CrewAI 코치 코멘트, 실패 시 템플릿) → report_mail.render / render_text(메일 본문)

- **DB에 쓰지 않는다**: load·save 노드는 부르지 않고 State를 메모리에서 만든다. 안전장치로 db.one/all_/tx는 호출되면 예외,
  db.run(report_mail._coach 끝의 캐시 UPDATE 한 줄)은 기록만 하고 실행하지 않는다.
- **메일을 보내지 않는다**: report_mail.send / mailer.send를 부르지 않고, 본문을 만드는 render·render_text만 부른다(mailer.send는 예외로 막음).
- 모델 호출은 모두 llm.chat_json(로컬 Ollama). 제품 경로 = 컨테이너 LLM_MODEL, 판정 = --model(기본 qwen3:8b).
- 지원자 데이터 없음 — 아래 질문·답변은 모두 가상으로 쓴 것이다. LangSmith 추적은 이 프로세스에서 끈다.

    python -m tools.eval_reports run    --out /tmp/repeval/run.json                     # 제품 경로 통과(생성)
    python -m tools.eval_reports judge  --run /tmp/repeval/run.json --out /tmp/repeval/judged.json --model qwen3:8b
    python -m tools.eval_reports report --dir /tmp/repeval --name 2026-11-12           # json·md·xlsx

run·judge는 응시 1건마다 파일에 쓰고, 이미 끝난 응시는 건너뛴다(중단 후 이어서 실행 가능). Ollama 호출은 항상 하나씩(순차).

통계: 평균의 95% CI = 응시 단위 부트스트랩(B=5000), 비율의 95% CI = Wilson 구간.
"""
import argparse
import json
import math
import os
import random
import re
import sys
import time
import traceback
from datetime import datetime, timezone

os.environ["LANGSMITH_TRACING"] = "false"          # 가상 데이터라도 이 측정은 외부 추적을 보내지 않는다
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from app import config, db, evaluation as ev, llm, mailer, report_mail  # noqa: E402
from app.agents import evaluation_graph as eg  # noqa: E402
from app.questions import EVAL_ITEMS  # noqa: E402

PROMPT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "prompts", "evaluate",
                           "judge_report_quality.md")
RUBRIC = ("factual", "specificity", "actionability", "tone", "consistency")
RUBRIC_KO = {"factual": "사실 정확성", "specificity": "구체성", "actionability": "실행 가능성", "tone": "어조", "consistency": "점수와 일관성"}
LEVEL_KO = {"ex": "우수", "avg": "보통", "poor": "미흡", "short": "짧음", "off": "주제 이탈", "mixA": "혼합A(인성·기업 우수/직무 미흡)",
            "mixB": "혼합B(직무 우수/인성·기업 미흡)"}
LEVEL_ORD = {"ex": 4, "avg": 3, "poor": 2, "short": 1, "off": 1}   # 변별력 검사용 서열(혼합은 제외)


# ====================== 안전장치: DB·메일 차단 ======================
DB_WRITES_BLOCKED = []


def _no_db(*a, **k):
    raise RuntimeError("eval_reports: DB 접근 금지")


def _record_run(sql, *params):
    DB_WRITES_BLOCKED.append(sql.split()[0] + " " + sql.split()[1] if sql.split() else sql)
    return None


def _no_mail(*a, **k):
    raise RuntimeError("eval_reports: 메일 발송 금지")


db.one = _no_db
db.all_ = _no_db
db.tx = _no_db
db.run = _record_run
mailer.send = _no_mail


# ====================== 가상 응시 데이터 (실존 인물·회사 아님) ======================
COMPANY = "헤드헌터랩_서울"
JOBS = {"ai_ax": "LLM 애플리케이션 엔지니어 (AI 상담 에이전트)", "backend": "백엔드 개발자 (상담 플랫폼 API)",
        "data_sci": "데이터 분석가 (상담 품질·지표)"}

# 질문 1건 = (category, text, resume_ref quote or None, {ex, avg, poor})
SHARED = {
    "P1": ("personality", "팀원과 의견이 크게 달랐던 경험이 있다면, 어떻게 조율했는지 말씀해 주세요.", None, {
        "ex": "작년 사내 챗봇 프로젝트에서 저는 정확도를 위해 출시를 2주 미루자고 했고, 기획 담당자는 일정대로 내자고 했습니다. 저는 실패 사례 30건을 표로 정리해 위험을 보여 주고, 핵심 기능만 먼저 내고 나머지는 2차에 넣는 절충안을 제안했습니다. 결과적으로 일정은 지키면서 오류 문의가 줄었고, 이후 결정 전에 근거 자료를 먼저 공유하는 습관이 생겼습니다.",
        "avg": "팀원과 기능 우선순위로 의견이 달랐던 적이 있습니다. 서로 이야기를 나눠서 중간 지점을 찾았고 프로젝트는 무사히 끝났습니다.",
        "poor": "의견이 다를 때는 보통 제가 맞다고 생각하는 쪽으로 진행했습니다. 결과가 좋으면 다들 이해해 주었습니다."}),
    "P2": ("personality", "본인이 맡은 일에서 실수를 했을 때 어떻게 책임지고 수습했는지 사례를 들어 주세요.", None, {
        "ex": "배포 스크립트에서 환경 변수를 잘못 넣어 운영 서버 로그 수집이 3시간 멈춘 적이 있습니다. 바로 팀에 알리고 이전 설정으로 되돌린 뒤, 누락된 로그 구간을 백업에서 복구했습니다. 재발을 막으려고 배포 전 설정 비교 체크리스트를 만들어 팀 위키에 올렸고 이후 같은 실수는 없었습니다.",
        "avg": "제가 맡은 문서에 오타가 있어서 다시 고친 적이 있습니다. 실수를 인정하고 바로 수정했습니다.",
        "poor": "큰 실수는 거의 하지 않는 편이라 특별히 떠오르는 사례는 없습니다. 실수가 생기면 그냥 다시 하면 된다고 생각합니다."}),
    "C1": ("company", "헤드헌터랩_서울의 상담 플랫폼 사업에 지원한 동기와 입사 후 기여하고 싶은 부분은 무엇인가요?", None, {
        "ex": "상담 플랫폼은 상담 기록이라는 데이터가 꾸준히 쌓이는 구조라, 제가 해 온 데이터 기반 개선 경험을 바로 쓸 수 있다고 봤습니다. 입사 후에는 상담 요약과 품질 지표를 연결해 상담사 업무 시간을 줄이는 일에 기여하고 싶습니다. 처음 3개월은 현재 지표와 데이터 흐름을 익히는 데 집중하겠습니다.",
        "avg": "상담 플랫폼 분야에 관심이 있어서 지원했습니다. 입사하면 맡은 일을 성실히 해서 회사에 도움이 되고 싶습니다.",
        "poor": "집에서 가깝고 연봉 조건이 괜찮아서 지원했습니다. 무엇이든 시키시는 일은 하겠습니다."}),
}

ROLE_Q = {
    "ai_ax": [
        ("resume", "이력서의 '사내 규정 RAG 챗봇' 프로젝트에서 검색 품질을 어떻게 측정하고 개선했는지 설명해 주세요.",
         "사내 규정 문서 RAG 챗봇 구축, 검색 정확도 개선", {
             "ex": "사내 규정 질문 120개로 평가 세트를 만들고 정답 문서가 상위 5개 안에 들어오는 비율(Recall@5)을 쟀습니다. 처음 0.62였는데, 조항 단위로 문서를 자르고 제목을 본문에 붙이는 방식으로 바꿔 0.81까지 올렸습니다. 이후 재순위 모델을 붙여 답변 근거 표시 정확도도 함께 확인했습니다.",
             "avg": "질문 몇 개로 직접 검색해 보면서 결과가 괜찮은지 확인했습니다. 문서를 자르는 크기를 바꿔 보니 조금 더 잘 찾았습니다.",
             "poor": "검색 품질은 따로 측정하지 않았고 답변이 그럴듯하면 괜찮다고 봤습니다."}),
        ("resume", "이력서의 'LLM 응답 지연 단축' 경험에서 어떤 병목을 찾았고 어떻게 줄였나요?", "LLM 응답 지연 단축", {
            "ex": "응답 시간을 단계별로 찍어 보니 프롬프트가 길어 첫 토큰까지 4초가 걸리는 것이 병목이었습니다. 반복되는 시스템 프롬프트를 줄이고 검색 문서를 상위 3개로 제한했으며, 스트리밍 응답을 적용해 체감 대기 시간을 1초 안쪽으로 줄였습니다.",
            "avg": "응답이 느려서 프롬프트를 조금 줄였습니다. 그랬더니 속도가 빨라졌습니다.",
            "poor": "서버를 더 좋은 것으로 바꾸면 해결된다고 생각해서 그렇게 요청했습니다."}),
        ("resume", "이력서의 '상담 요약 프롬프트 개선' 작업에서 요약 품질을 어떤 기준으로 평가했나요?", "상담 요약 프롬프트 개선", {
            "ex": "상담 요약 200건을 뽑아 핵심 요청 누락 여부, 사실 오류, 길이 기준 세 가지로 상담사 2명이 채점했습니다. 프롬프트에 '고객 요청·조치·후속 일정' 세 칸 형식을 넣은 뒤 누락률이 18%에서 6%로 줄었습니다.",
            "avg": "요약 결과를 몇 개 읽어 보고 자연스러운지 판단했습니다. 형식을 정해 주니 더 깔끔해졌습니다.",
            "poor": "모델이 알아서 잘 요약해 줘서 따로 평가할 필요는 없었습니다."}),
        ("job", "LLM 서비스에서 환각(hallucination)을 줄이기 위해 설계 단계에서 쓸 수 있는 방법을 설명해 주세요.", None, {
            "ex": "먼저 검색 증강으로 근거 문서를 주고, 답변이 근거에 없는 내용이면 '모른다'고 말하도록 지시합니다. 또 답변 문장마다 출처 조각을 달게 해서 출처가 없는 문장은 걸러 내고, 평가 세트로 근거 일치율을 주기적으로 측정합니다.",
            "avg": "RAG를 써서 문서를 참고하게 하면 환각이 줄어듭니다. 프롬프트에 정확하게 답하라고 쓰는 것도 도움이 됩니다.",
            "poor": "모델 크기를 키우면 환각은 거의 사라집니다."}),
        ("job", "임베딩 모델을 교체할 때 기존 벡터 인덱스는 어떻게 처리해야 하며 그 이유는 무엇인가요?", None, {
            "ex": "임베딩 모델이 바뀌면 벡터 공간이 달라져 기존 벡터와 새 질의 벡터를 비교할 수 없으므로 전체 문서를 새 모델로 다시 임베딩해 인덱스를 새로 만들어야 합니다. 운영 중에는 새 인덱스를 따로 만든 뒤 평가 세트로 검색 품질을 비교하고 전환합니다.",
            "avg": "새 모델로 다시 임베딩하는 것이 좋습니다. 그래야 검색 결과가 더 정확해집니다.",
            "poor": "기존 인덱스는 그대로 두고 새 문서만 새 모델로 넣으면 됩니다."}),
    ],
    "backend": [
        ("resume", "이력서의 '주문 API 성능 개선' 프로젝트에서 응답 시간을 줄인 방법을 설명해 주세요.", "주문 API 성능 개선", {
            "ex": "APM으로 보니 주문 조회 한 번에 쿼리가 40번 나가는 N+1 문제가 있었습니다. 조인 쿼리로 묶고 주문 상태 컬럼에 복합 인덱스를 추가해 p95 응답 시간을 1.2초에서 180ms로 줄였습니다. 배포 전에는 부하 테스트로 초당 300건에서 오류가 없는지 확인했습니다.",
            "avg": "느린 쿼리를 찾아서 인덱스를 추가했습니다. 그 뒤로 응답이 빨라졌습니다.",
            "poor": "서버 대수를 늘려서 해결했습니다. 코드는 크게 손대지 않았습니다."}),
        ("resume", "이력서의 '결제 모듈 장애 대응' 경험에서 원인을 어떻게 찾고 재발을 막았나요?", "결제 모듈 장애 대응", {
            "ex": "결제 승인 응답이 늦을 때 재시도가 겹쳐 이중 결제가 생긴 장애였습니다. 로그의 요청 ID로 같은 주문이 두 번 승인된 흐름을 확인했고, 결제 요청에 멱등 키를 붙이고 외부 결제사 호출에 타임아웃과 재시도 횟수 제한을 걸었습니다. 이후 같은 유형의 장애는 0건이었고 장애 회고 문서를 팀에 공유했습니다.",
            "avg": "결제가 가끔 실패해서 로그를 보고 원인을 찾아 고쳤습니다. 이후에는 문제가 줄었습니다.",
            "poor": "결제 쪽은 외부 업체 문제라서 업체에 연락하고 기다렸습니다."}),
        ("resume", "이력서의 'Redis 캐시 도입' 작업에서 캐시 무효화는 어떻게 설계했나요?", "Redis 캐시 도입", {
            "ex": "상품 정보는 수정될 때 해당 키를 바로 삭제하는 방식으로 무효화하고, 혹시 놓친 경우를 대비해 TTL 10분을 함께 걸었습니다. 동시에 많은 요청이 몰려 캐시가 비는 순간 DB에 부하가 몰리지 않도록 락을 걸어 한 요청만 다시 채우게 했습니다.",
            "avg": "TTL을 설정해서 일정 시간이 지나면 캐시가 지워지도록 했습니다.",
            "poor": "캐시는 한 번 넣으면 계속 쓰도록 했고 무효화는 따로 하지 않았습니다."}),
        ("job", "데이터베이스 트랜잭션 격리 수준의 차이와 실무에서의 선택 기준을 설명해 주세요.", None, {
            "ex": "격리 수준이 높을수록 더티 리드, 반복 불가능한 읽기, 팬텀 읽기를 차례로 막지만 동시성은 떨어집니다. 보통은 PostgreSQL 기본값인 Read Committed를 쓰고, 재고 차감처럼 정합성이 중요한 구간만 행 잠금이나 Serializable을 따로 적용합니다.",
            "avg": "격리 수준에는 여러 단계가 있고 높을수록 안전하지만 느립니다. 상황에 맞게 고르면 됩니다.",
            "poor": "격리 수준은 항상 가장 높은 단계로 두는 게 안전해서 그렇게 합니다."}),
        ("job", "API에 멱등성(idempotency)이 필요한 상황과 구현 방법을 설명해 주세요.", None, {
            "ex": "네트워크 오류로 클라이언트가 같은 결제·주문 요청을 다시 보낼 때 중복 처리를 막으려면 멱등성이 필요합니다. 요청마다 멱등 키를 받아 처리 결과와 함께 저장하고, 같은 키로 다시 오면 저장된 결과를 그대로 돌려줍니다.",
            "avg": "같은 요청이 여러 번 와도 결과가 같아야 할 때 필요합니다. 요청에 고유 값을 붙여 확인합니다.",
            "poor": "멱등성은 GET 요청에만 해당되는 개념이라 POST에는 필요 없습니다."}),
    ],
    "data_sci": [
        ("resume", "이력서의 '상담 이탈 예측 모델' 프로젝트에서 어떤 피처가 효과적이었고 어떻게 검증했나요?", "상담 이탈 예측 모델", {
            "ex": "최근 30일 상담 횟수 변화율과 마지막 상담의 미해결 여부가 가장 효과적인 피처였습니다. 시간 순서대로 학습·검증 기간을 나누고 AUC와 상위 10% 구간의 정밀도를 함께 봤는데, 검증 AUC가 0.78이었습니다. 피처 중요도는 SHAP으로 확인해 운영팀에 설명했습니다.",
            "avg": "상담 횟수 같은 피처를 넣었고 정확도로 성능을 확인했습니다. 결과는 꽤 괜찮았습니다.",
            "poor": "가능한 모든 컬럼을 넣고 학습했는데 정확도가 99%가 나와서 잘 된 것 같습니다."}),
        ("resume", "이력서의 'A/B 테스트 설계' 경험에서 표본 크기와 판단 기준을 어떻게 정했나요?", "A/B 테스트 설계", {
            "ex": "기존 전환율 8%에서 1%p 개선을 감지하려고 유의수준 5%, 검정력 80%로 계산해 그룹당 약 1만 2천 명이 필요하다고 정했습니다. 판단 기준과 기간 2주를 시작 전에 문서로 고정했고, 중간에 결과를 보고 멈추지 않도록 했습니다.",
            "avg": "두 그룹으로 나눠 일정 기간 실험하고 전환율 차이를 비교했습니다.",
            "poor": "결과가 좋아 보일 때까지 실험을 계속 돌리다가 차이가 나면 멈췄습니다."}),
        ("resume", "이력서의 '대시보드 지표 정의' 작업에서 지표 정의가 엇갈렸을 때 어떻게 정리했나요?", "대시보드 지표 정의", {
            "ex": "'해결률'을 팀마다 다르게 세고 있어서, 재문의 7일 이내 없음 기준으로 정의를 하나로 정하고 계산 SQL을 지표 사전에 올렸습니다. 기존 수치와 새 정의 수치를 한 달 동안 같이 보여 줘 차이를 설명했습니다.",
            "avg": "팀들과 회의해서 지표 정의를 맞췄습니다. 이후에는 혼란이 줄었습니다.",
            "poor": "제가 생각하는 정의로 대시보드를 만들고 따로 공유하지는 않았습니다."}),
        ("job", "불균형 데이터에서 분류 모델을 평가할 때 정확도 대신 쓸 지표와 그 이유를 설명해 주세요.", None, {
            "ex": "양성 비율이 낮으면 모두 음성으로 찍어도 정확도가 높게 나오므로, 정밀도·재현율과 PR-AUC를 봅니다. 업무에서 놓치는 비용이 크면 재현율을, 오탐 비용이 크면 정밀도를 우선하고 임계값을 그 기준으로 조정합니다.",
            "avg": "F1 점수를 쓰는 것이 좋습니다. 정확도는 불균형 데이터에서 믿기 어렵습니다.",
            "poor": "정확도가 가장 직관적이라서 정확도만 보면 충분합니다."}),
        ("job", "시계열 데이터를 학습·검증 세트로 나눌 때 주의할 점은 무엇인가요?", None, {
            "ex": "무작위로 섞어 나누면 미래 정보가 학습에 들어가는 누수가 생기므로, 시간 순서대로 앞부분을 학습, 뒷부분을 검증으로 씁니다. 롤링 방식으로 여러 시점에서 검증하고, 이동 평균 같은 피처도 기준 시점 이전 데이터로만 계산합니다.",
            "avg": "시간 순서를 지켜서 나누는 것이 중요합니다.",
            "poor": "다른 데이터처럼 무작위로 8:2로 나누면 됩니다."}),
    ],
}

SHORT = ["네, 해 봤습니다.", "잘 기억이 나지 않습니다.", "열심히 했습니다.", "모르겠습니다.", "그때그때 다르게 했습니다.",
         "팀과 잘 상의했습니다.", "성능을 개선했습니다.", "중요하다고 생각합니다."]
OFF = ["주말에는 주로 등산을 다니며 체력을 관리합니다. 최근에는 북한산에 다녀왔습니다.",
       "저는 커피를 좋아해서 핸드드립을 직접 내려 마십니다. 원두는 산미가 있는 것을 좋아합니다.",
       "요즘 재미있게 본 드라마는 법정물인데 전개가 빨라서 좋았습니다.",
       "어릴 때부터 축구를 좋아해 동네 조기축구회에서 활동하고 있습니다.",
       "여행을 좋아해서 작년에는 제주도를 자전거로 일주했습니다.",
       "점심에는 보통 김치찌개를 먹는데 회사 근처 맛집을 찾는 것이 취미입니다.",
       "고양이를 키우고 있어서 퇴근 후에는 주로 고양이와 놀아 줍니다.",
       "최근 피아노를 다시 배우기 시작해 쇼팽 곡을 연습하고 있습니다."]


def _answer(level, cat, base, idx):
    if level in ("ex", "avg", "poor"):
        return base[level]
    if level == "short":
        return SHORT[idx % len(SHORT)]
    if level == "off":
        return OFF[idx % len(OFF)]
    if level == "mixA":   # 인성·기업 우수, 이력서 미흡, 직무 보통 → fit 높고 expertise 낮게
        return base["ex"] if cat in ("personality", "company") else base["poor"] if cat == "resume" else base["avg"]
    if level == "mixB":   # 이력서·직무 우수, 인성·기업 미흡 → expertise 높고 fit 낮게
        return base["poor"] if cat in ("personality", "company") else base["ex"]
    raise ValueError(level)


def build_attempts():
    """역할 3 × (우수·보통·미흡·짧음·주제 이탈·혼합A·혼합B) = 21건. 질문 순서 = 인성2 → 이력서3 → 직무2 → 기업1."""
    out = []
    n = 0
    for role in ("ai_ax", "backend", "data_sci"):
        qs = [SHARED["P1"], SHARED["P2"]] + ROLE_Q[role] + [SHARED["C1"]]
        for level in ("ex", "avg", "poor", "short", "off", "mixA", "mixB"):
            n += 1
            blocks = []
            for i, (cat, text, ref, base) in enumerate(qs):
                qid = n * 100 + i + 1
                ans = _answer(level, cat, base, i + n)
                blocks.append({"q": {"id": qid, "seq": i + 1, "text": text, "category": cat, "eval_items": EVAL_ITEMS[cat],
                                     "answer_text": ans, "answer_status": "answered",
                                     "resume_ref": {"label": "프로젝트", "quote": ref} if ref else None},
                               "followups": []})
            out.append({"id": f"R-{n:02d}", "role": role, "job_title": JOBS[role], "level": level, "blocks": blocks})
    return out


CRITERIA = {"w_expertise": 30, "w_logic": 25, "w_relevance": 20, "w_delivery": 10, "w_fit": 15, "coding_enabled": False,
            "coding_gate_mode": "none", "pass_score": 70, "decision_mode": "auto"}


# ====================== 1) run: 제품 경로 통과 ======================
def _load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _dump(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def run_one(att):
    t0 = time.time()
    rec = {"id": att["id"], "role": att["role"], "job_title": att["job_title"], "level": att["level"],
           "questions": [{"seq": b["q"]["seq"], "category": b["q"]["category"], "text": b["q"]["text"],
                          "answer": b["q"]["answer_text"], "eval_items": b["q"]["eval_items"]} for b in att["blocks"]]}
    try:
        s = {"attempt_id": -1, "att": {"resume_snapshot": None}, "criteria": CRITERIA, "blocks": att["blocks"]}
        s.update(eg.evaluate_blocks(s))
        t1 = time.time()
        s.update(eg.compute(s))
        s.update(eg.summarize(s))
        s.update(eg.decide(s))
        t2 = time.time()
        summ = s["summary"]
        summary_template = summ.get("summary", "").startswith("자동 요약 생성에 실패")
        # report_mail 경로(메모리 r) — save 노드가 evaluations에 넣을 값과 같은 모양
        r = {"attempt_id": -1, "status": "completed", "completed_at": datetime.now(timezone.utc), "name": "가상지원자",
             "email": "", "job_title": att["job_title"], "company": COMPANY, "item_scores": s["item_scores"],
             "interview_score": s["interview"], "coding_score": None, "strengths": summ["strengths"][:3],
             "improvements": summ["improvements"][:3], "public_feedback": {}, "criteria_snapshot": CRITERIA}
        items = [(ev.ITEM_KO[it], float(r["item_scores"].get(it, 0))) for it in ev.ITEMS]
        facts = {"answered": len(att["blocks"]), "base": len(att["blocks"]), "followups": 0, "coding": None}
        coach, writer = report_mail._coach(r, items, facts)
        t3 = time.time()
        html_body = report_mail.render(r, items, facts, coach)
        text_body = report_mail.render_text(r, items, facts, coach)
        rec.update({
            "ok": True, "error": None,
            "scores_by_q": {str(k): v for k, v in s["scores_by_q"].items()},
            "item_scores": s["item_scores"], "interview_score": s["interview"], "ai_result_code": s["result"],
            "strengths": r["strengths"], "improvements": r["improvements"], "summary": summ.get("summary", ""),
            "summary_template": summary_template,
            "coaching": coach.get("coaching", ""), "actions": coach.get("actions", []), "coach_writer": writer, "coach_reject": coach.get("reject"),
            "mail_text": text_body, "mail_html_len": len(html_body),
            "sec": {"evaluate": round(t1 - t0, 1), "summarize": round(t2 - t1, 1), "coach": round(t3 - t2, 1), "total": round(t3 - t0, 1)},
        })
    except Exception as e:  # noqa: BLE001 — 생성 실패도 측정 대상(M-43)
        rec.update({"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:],
                    "sec": {"total": round(time.time() - t0, 1)}})
    return rec


def cmd_run(a):
    data = _load_json(a.out, {"meta": {}, "runs": []})
    done = {r["id"] for r in data["runs"] if r.get("ok")}
    data["runs"] = [r for r in data["runs"] if r.get("ok")]
    data["meta"].update({"pipeline_model": llm.model_version(), "report_writer": config.REPORT_WRITER,
                         "started": data["meta"].get("started") or datetime.now().isoformat(timespec="seconds")})
    atts = build_attempts()
    if a.limit:
        atts = atts[:a.limit]
    for att in atts:
        if att["id"] in done:
            continue
        rec = run_one(att)
        data["runs"].append(rec)
        data["meta"]["blocked_db_writes"] = DB_WRITES_BLOCKED
        data["meta"]["finished"] = datetime.now().isoformat(timespec="seconds")
        _dump(a.out, data)
        print(f"{rec['id']} {rec['role']:8s} {rec['level']:5s} ok={rec['ok']} score={rec.get('interview_score')} "
              f"writer={rec.get('coach_writer')} sec={rec['sec']}", flush=True)


# ====================== 2) judge: M-39 판정 ======================
JUDGE_SCHEMA = {"type": "object", "properties": {
    **{k: {"type": "integer", "minimum": 1, "maximum": 5} for k in RUBRIC},
    "reasons": {"type": "object", "properties": {k: {"type": "string"} for k in RUBRIC}, "required": list(RUBRIC)},
    "strength_items": {"type": "array", "items": {"type": "string", "enum": ev.ITEMS + ["none"]}},
    "improvement_items": {"type": "array", "items": {"type": "string", "enum": ev.ITEMS + ["none"]}},
    "issues": {"type": "array", "items": {"type": "string"}}},
    "required": list(RUBRIC) + ["reasons", "strength_items", "improvement_items", "issues"]}


def judge_input(rec):
    qa = []
    for q in rec["questions"]:
        sc = next((v for k, v in rec["scores_by_q"].items() if int(k) % 100 == q["seq"]), {})
        qa.append(f"[{q['seq']}] {q['text']}\n답변: {q['answer']}\n점수: " +
                  ", ".join(f"{ev.ITEM_KO[k]} {v['score']}" for k, v in sc.items()))
    items = ", ".join(f"{ev.ITEM_KO[k]} {v}" for k, v in rec["item_scores"].items())
    rep = ("강점:\n" + "\n".join(f"- {x}" for x in rec["strengths"]) + "\n개선점:\n" + "\n".join(f"- {x}" for x in rec["improvements"]) +
           f"\n총평: {rec['summary']}\n코치 코멘트: {rec['coaching']}\n연습 과제:\n" + "\n".join(f"- {x}" for x in rec["actions"]))
    return (f"[질문과 답변]\n" + "\n\n".join(qa) + f"\n\n[항목 점수(100점 환산)]\n{items}\nAI 면접 점수: {rec['interview_score']}"
            f"\n\n[리포트]\n{rep}")


def cmd_judge(a):
    with open(PROMPT_PATH, encoding="utf-8") as f:
        system = f.read()
    run = _load_json(a.run, None)
    data = _load_json(a.out, {"meta": {}, "judged": {}})
    key = "judged2" if a.second else "judged"          # 2회차 = 같은 모델·같은 프롬프트, temperature 0.5 (판정 흔들림 측정)
    temp = 0.5 if a.second else 0
    data.setdefault(key, {})
    data["meta"].update({"judge_model": a.model, "prompt": "prompts/evaluate/judge_report_quality.md",
                         f"{key}_temperature": temp})
    for rec in run["runs"]:
        if not rec.get("ok") or (data[key].get(rec["id"]) or {}).get("ok"):   # 판정 실패만 다시
            continue
        t0 = time.time()
        res, err = None, None
        for _ in range(2):
            try:
                res = llm.chat_json(system, judge_input(rec), JUDGE_SCHEMA, temperature=temp, model=a.model, timeout=a.timeout,
                                    num_ctx=8192, max_tokens=1500)
                if all(isinstance(res.get(k), int) and 1 <= res[k] <= 5 for k in RUBRIC):
                    break
                err, res = "invalid scores", None
            except llm.LLMError as e:
                err = str(e)
        data[key][rec["id"]] = {"ok": res is not None, "error": None if res else err, "result": res,
                                     "sec": round(time.time() - t0, 1)}
        _dump(a.out, data)
        print(key, rec["id"], "ok" if res else f"FAIL {err}", data[key][rec["id"]]["sec"],
              {k: res[k] for k in RUBRIC} if res else "", flush=True)


# ====================== 3) report: 지표 계산 ======================
NUM = re.compile(r"\d+(?:[.,]\d+)?")
FORBID_RESULT = re.compile(r"합격|불합격|합불|탈락|통과\s*여부|당락|채용\s*여부")
FORBID_PERSONAL = re.compile(r"나이|연령|성별|남성|여성|외모|학벌|출신|고향|결혼|가족|종교|장애인|장애\s*(?:여부|유무)|국적|인종")  # '장애 대응'(시스템 장애)은 제외
ITEM_KW = {
    "expertise": ["전문성", "전문 지식", "기술", "개념", "이해도", "지식", "원리", "직무"],
    "logic": ["논리", "근거", "일관", "인과", "체계", "추론"],
    "relevance": ["구체", "사례", "경험", "관련성", "질문 의도", "예시", "수치", "실제"],
    "delivery": ["전달", "명확", "간결", "핵심을 먼저", "표현", "설명 방식", "구조화", "말하기"],
    "fit": ["협업", "책임", "성장", "동기", "조직", "팀", "소통", "태도", "적합"],
}


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(p, 3), round(max(0, c - h), 3), round(min(1, c + h), 3))


def boot_mean(xs, B=5000, seed=7):
    xs = np.asarray(xs, dtype=float)
    if len(xs) == 0:
        return (None, None, None)
    rng = np.random.default_rng(seed)
    ms = rng.choice(xs, size=(B, len(xs)), replace=True).mean(axis=1)
    return (round(float(xs.mean()), 3), round(float(np.percentile(ms, 2.5)), 3), round(float(np.percentile(ms, 97.5)), 3))


def spearman(x, y):
    def rank(v):
        v = np.asarray(v, dtype=float)
        order = v.argsort()
        r = np.empty(len(v))
        r[order] = np.arange(len(v))
        for u in np.unique(v):            # 동순위 평균
            m = v == u
            r[m] = r[m].mean()
        return r
    if len(x) < 3:
        return None
    rx, ry = rank(x), rank(y)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return round(float(np.corrcoef(rx, ry)[0, 1]), 3)


def _num_norm(s):
    s = s.replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def allowed_numbers(rec):
    """입력(질문·답변·항목 점수·AI 면접 점수·프롬프트가 말한 100점 환산)에 있는 수치."""
    src = " ".join(q["text"] + " " + q["answer"] for q in rec["questions"])
    nums = {_num_norm(n) for n in NUM.findall(src)} | {"100"}
    for v in list(rec["item_scores"].values()) + [rec["interview_score"]]:
        v = float(v)
        nums |= {_num_norm(f"{v:.2f}"), _num_norm(f"{v:.1f}"), str(round(v)), str(int(v))}
    return nums


def ai_text(rec):
    """AI가 쓴 문장만. 코치가 템플릿(report_mail._template_coach, 코드 고정 문구 — 예: '첫 10초')이면 코치 칸은 뺀다."""
    out = {"strengths": rec["strengths"], "improvements": rec["improvements"], "summary": [rec["summary"]]}
    if rec.get("coach_writer") == "local":
        out.update({"coaching": [rec["coaching"]], "actions": rec["actions"]})
    return out


def forbidden(rec):
    """M-41: AI가 쓴 문장 안의 합불 언급·입력에 없는 두 자리 이상 수치·인적 특성(차별 요소)."""
    hits = []
    allow = allowed_numbers(rec)
    for field, arr in ai_text(rec).items():
        for t in arr:
            for m in FORBID_RESULT.finditer(t):
                hits.append({"field": field, "type": "합불 언급", "text": m.group(0), "sentence": t[:160]})
            for m in FORBID_PERSONAL.finditer(t):
                hits.append({"field": field, "type": "인적 특성", "text": m.group(0), "sentence": t[:160]})
            for n in NUM.findall(t):
                nn = _num_norm(n)
                if len(nn.replace(".", "")) >= 2 and nn not in allow:
                    hits.append({"field": field, "type": "입력에 없는 수치", "text": n, "sentence": t[:160]})
    return hits


def quote_checks(rec):
    """M-38: A4 채점 인용(verified) + 강점·개선점·총평 안 따옴표 인용이 답변 원문 부분 문자열인지."""
    ans_by_seq = {q["seq"]: q["answer"] for q in rec["questions"]}
    all_ans = "\n".join(ans_by_seq.values())
    a4 = []
    for qid, sc in rec["scores_by_q"].items():
        ans = ans_by_seq[int(qid) % 100]
        for it, v in sc.items():
            if v.get("quote"):
                a4.append({"q": int(qid) % 100, "item": it, "quote": v["quote"], "ok": ev.quote_ok(v["quote"], ans),
                           "strict": v["quote"].strip() in ans})
    unverified = sum(1 for sc in rec["scores_by_q"].values() for v in sc.values() if v.get("verified") is False)
    report_q = []
    for field, arr in (("strengths", rec["strengths"]), ("improvements", rec["improvements"]), ("summary", [rec["summary"]]),
                       ("coaching", [rec["coaching"]])):
        for t in arr:
            for m in re.finditer(r"[\"“'‘]([^\"”'’]{6,120})[\"”'’]", t):
                report_q.append({"field": field, "quote": m.group(1), "ok": ev.quote_ok(m.group(1), all_ans)})
    return a4, unverified, report_q


def item_of(sentence):
    hits = {it: sum(sentence.count(k) for k in kws) for it, kws in ITEM_KW.items()}
    hits[" "] = 0
    best = max(hits.values())
    if best == 0:
        return []
    return [it for it, v in hits.items() if v == best and it != " "]


def consistency(rec):
    """M-40(코드): 강점 문장의 항목이 상위 2위(동점 포함), 개선점 문장의 항목이 하위 2위(동점 포함)에 드는가.
    항목 점수 폭(최대-최소)이 5점 미만이면 구분 불가로 분모에서 뺀다."""
    sc = {k: float(v) for k, v in rec["item_scores"].items()}
    vals = sorted(sc.values(), reverse=True)
    spread = vals[0] - vals[-1]
    top_cut, bot_cut = vals[1], vals[-2]
    res = []
    for kind, arr in (("strength", rec["strengths"]), ("improvement", rec["improvements"])):
        for t in arr:
            its = item_of(t)
            if not its:
                res.append({"kind": kind, "sentence": t, "items": [], "ok": None, "why": "항목 판별 불가"})
                continue
            ok = any(sc[i] >= top_cut for i in its) if kind == "strength" else any(sc[i] <= bot_cut for i in its)
            res.append({"kind": kind, "sentence": t, "items": its, "ok": ok if spread >= 5 else None,
                        "why": "" if spread >= 5 else f"항목 점수 폭 {spread:.1f} < 5"})
    return res, spread


def judge_consistency(rec, j):
    """M-40 보조: 판정 모델 라벨(strength_items·improvement_items) 기준 같은 규칙."""
    if not j:
        return []
    sc = {k: float(v) for k, v in rec["item_scores"].items()}
    vals = sorted(sc.values(), reverse=True)
    if vals[0] - vals[-1] < 5:
        return []
    out = []
    for lab in j.get("strength_items", []):
        if lab in sc:
            out.append(sc[lab] >= vals[1])
    for lab in j.get("improvement_items", []):
        if lab in sc:
            out.append(sc[lab] <= vals[-2])
    return out


def expected(rec):
    sc = {k: float(v) for k, v in rec["item_scores"].items()}
    order = sorted(sc, key=lambda k: -sc[k])
    return (f"강점은 상위 항목({ev.ITEM_KO[order[0]]}·{ev.ITEM_KO[order[1]]}), 개선점은 하위 항목({ev.ITEM_KO[order[-1]]}·{ev.ITEM_KO[order[-2]]})과 맞을 것"
            f" / 답변에 있는 내용·인용만 / 합불·인적 특성·입력에 없는 두 자리 이상 수치 0건 / 답변 수준 '{LEVEL_KO[rec['level']]}'에 맞는 칭찬·지적 강도"
            f" / 연습 과제는 바로 실천 가능")


def _fmt_ci(t, pct=False):
    if t[0] is None:
        return "-"
    if pct:
        return f"{t[0]:.3f} [{t[1]:.3f}, {t[2]:.3f}]"
    return f"{t[0]:.2f} [{t[1]:.2f}, {t[2]:.2f}]"


def cmd_report(a):
    run = _load_json(os.path.join(a.dir, "run.json"), None)
    jd = _load_json(os.path.join(a.dir, "judged.json"), {"meta": {}, "judged": {}})
    runs = run["runs"]
    rows = []
    for rec in runs:
        row = {"id": rec["id"], "role": rec["role"], "level": rec["level"], "ok": rec.get("ok", False), "error": rec.get("error")}
        if rec.get("ok"):
            j = (jd["judged"].get(rec["id"]) or {}).get("result")
            j2 = (jd.get("judged2", {}).get(rec["id"]) or {}).get("result")
            a4, unverified, rq = quote_checks(rec)
            cons, spread = consistency(rec)
            fb = forbidden(rec)
            jmean = round(sum(j[k] for k in RUBRIC) / len(RUBRIC), 2) if j else None
            fields_ok = bool(rec["strengths"] and rec["improvements"] and rec["summary"].strip() and rec["coaching"].strip())
            row.update({"interview_score": rec["interview_score"], "item_scores": rec["item_scores"], "a4_quotes": a4,
                        "a4_unverified": unverified, "report_quotes": rq, "consistency": cons, "item_spread": round(spread, 2),
                        "judge_consistency": judge_consistency(rec, j), "forbidden": fb, "judge": j, "judge_mean": jmean,
                        "judge2": j2, "judge2_mean": round(sum(j2[k] for k in RUBRIC) / len(RUBRIC), 2) if j2 else None,
                        "coach_template": rec["coach_writer"] != "local", "summary_template": rec["summary_template"],
                        "fields_ok": fields_ok, "sec": rec["sec"]})
        rows.append(row)
    okr = [r for r in rows if r["ok"]]
    n = len(rows)

    # ---- 지표 ----
    m38_a4 = wilson(sum(q["ok"] for q in [q for r in okr for q in r["a4_quotes"]]), sum(len(r["a4_quotes"]) for r in okr))
    m38_rep = wilson(sum(q["ok"] for r in okr for q in r["report_quotes"]), sum(len(r["report_quotes"]) for r in okr))
    unverified_total = sum(r["a4_unverified"] for r in okr)
    a4_slots = sum(len(r["a4_quotes"]) for r in okr) + unverified_total
    judged = [r for r in okr if r["judge"]]
    m39 = boot_mean([r["judge_mean"] for r in judged])
    m39_by = {k: boot_mean([r["judge"][k] for r in judged]) for k in RUBRIC}
    cons_all = [c["ok"] for r in okr for c in r["consistency"] if c["ok"] is not None]
    m40 = wilson(sum(cons_all), len(cons_all))
    cons_none = sum(1 for r in okr for c in r["consistency"] if c["ok"] is None)
    jc = [x for r in okr for x in r["judge_consistency"]]
    m40_judge = wilson(sum(jc), len(jc))
    m41_hits = [dict(h, id=r["id"]) for r in okr for h in r["forbidden"]]
    m41_att = sum(1 for r in okr if r["forbidden"])
    m42_any = wilson(sum(1 for r in okr if r["coach_template"] or r["summary_template"]), len(okr))
    m42_coach = wilson(sum(1 for r in okr if r["coach_template"]), len(okr))
    m42_sum = wilson(sum(1 for r in okr if r["summary_template"]), len(okr))
    m43 = wilson(sum(1 for r in rows if r["ok"] and r["fields_ok"]), n)

    # ---- 변별력 ----
    uni = [r for r in okr if r["level"] in LEVEL_ORD]
    by_level = {}
    for lv in ("ex", "avg", "poor", "short", "off", "mixA", "mixB"):
        rs = [r for r in okr if r["level"] == lv]
        if rs:
            by_level[lv] = {"n": len(rs), "interview_mean": round(float(np.mean([r["interview_score"] for r in rs])), 2),
                            "judge_mean": round(float(np.mean([r["judge_mean"] for r in rs if r["judge_mean"] is not None])), 2)
                            if any(r["judge_mean"] is not None for r in rs) else None}
    rho = spearman([LEVEL_ORD[r["level"]] for r in uni], [r["interview_score"] for r in uni])
    lm = [by_level[l]["interview_mean"] for l in ("ex", "avg", "poor") if l in by_level]
    low = max(by_level[l]["interview_mean"] for l in ("short", "off") if l in by_level) if any(l in by_level for l in ("short", "off")) else None
    monotone = all(x > y for x, y in zip(lm, lm[1:])) and (low is None or (lm and lm[-1] > low))
    per_role = {}
    for role in JOBS:
        rs = {r["level"]: r["interview_score"] for r in uni if r["role"] == role}
        seq = [rs.get(l) for l in ("ex", "avg", "poor")]
        lowr = [rs.get(l) for l in ("short", "off") if l in rs]
        ok = None not in seq and seq[0] > seq[1] > seq[2] and (not lowr or seq[2] > max(lowr))
        per_role[role] = {"scores": {LEVEL_KO[k]: v for k, v in rs.items()}, "monotone": ok}
    # 혼합 응시: 의도한 방향(mixA fit>expertise, mixB expertise>fit)
    mix = []
    for r in okr:
        if r["level"] in ("mixA", "mixB"):
            e, f = float(r["item_scores"]["expertise"]), float(r["item_scores"]["fit"])
            mix.append({"id": r["id"], "level": r["level"], "expertise": e, "fit": f,
                        "as_intended": (f > e) if r["level"] == "mixA" else (e > f)})

    def target(v, ok):
        return "통과" if ok else "미달"

    metrics = [
        {"id": "M-38", "name": "인용(A4 채점 quote)이 답변 원문 부분 문자열", "target": "1.00", "value": m38_a4[0], "ci": m38_a4[1:],
         "n": sum(len(r["a4_quotes"]) for r in okr), "pass": m38_a4[0] == 1.0 if m38_a4[0] is not None else False,
         "note": f"공백 무시 비교(제품 quote_ok와 같은 규칙). 공백까지 글자 그대로 일치 {wilson(sum(q['strict'] for r in okr for q in r['a4_quotes']), sum(len(r['a4_quotes']) for r in okr))[0]}. "
                 f"참고: 인용 검증 실패로 1점 확정된 항목 {unverified_total}/{a4_slots}(제품이 인용 없이 1점으로 처리 — 짧음·주제 이탈 답변에서 정상 동작)"},
        {"id": "M-38b", "name": "(보조) 리포트 문장 속 따옴표 인용이 답변 원문 부분 문자열", "target": "1.00", "value": m38_rep[0], "ci": m38_rep[1:],
         "n": sum(len(r["report_quotes"]) for r in okr), "pass": m38_rep[0] == 1.0 if m38_rep[0] is not None else True,
         "note": "강점·개선점·총평·코치 코멘트에서 따옴표로 감싼 6자 이상 구절. 요약 단계에는 인용 검증이 없어 따로 잰다"},
        {"id": "M-39", "name": "루브릭 평균(판정 모델)", "target": "≥4.0/5, 95% CI 하한 ≥3.7", "value": m39[0], "ci": m39[1:],
         "n": len(judged), "pass": m39[0] is not None and m39[0] >= 4.0 and m39[1] >= 3.7,
         "note": "; ".join(f"{RUBRIC_KO[k]} {_fmt_ci(v)}" for k, v in m39_by.items())},
        {"id": "M-40", "name": "점수-서술 일관성(코드 키워드 판별)", "target": "≥0.90", "value": m40[0], "ci": m40[1:], "n": len(cons_all),
         "pass": m40[0] is not None and m40[0] >= 0.90,
         "note": f"판별 불가·점수 폭 5 미만 제외 {cons_none}문장. 판정 모델 라벨 기준(보조) {_fmt_ci(m40_judge, True)} (n={len(jc)})"},
        {"id": "M-41", "name": "금지 내용(합불·창작 수치·인적 특성)", "target": "0건", "value": len(m41_hits), "ci": None,
         "n": len(okr), "pass": len(m41_hits) == 0,
         "note": f"위반 응시 {m41_att}/{len(okr)} " + str({t: sum(1 for h in m41_hits if h['type'] == t) for t in ('합불 언급', '인적 특성', '입력에 없는 수치')})},
        {"id": "M-42", "name": "템플릿 대체율(코치 또는 요약)", "target": "≤0.20", "value": m42_any[0], "ci": m42_any[1:], "n": len(okr),
         "pass": m42_any[0] is not None and m42_any[0] <= 0.20,
         "note": f"코치 코멘트 템플릿 {_fmt_ci(m42_coach, True)}, 요약 템플릿 {_fmt_ci(m42_sum, True)}"},
        {"id": "M-43", "name": "생성 성공률(예외 없이 모든 칸 채움)", "target": "≥0.95", "value": m43[0], "ci": m43[1:], "n": n,
         "pass": m43[0] is not None and m43[0] >= 0.95, "note": "템플릿 대체도 리포트가 만들어졌으면 성공으로 셈(대체 비율은 M-42)"},
        {"id": "M-44", "name": "판정 vs 사람 일치도(Spearman)", "target": "≥0.6 (표본 ≥10)", "value": None, "ci": None, "n": 0,
         "pass": None, "note": "미측정(사람 평가 전) — xlsx '사람검토' 시트 노란 칸을 채운 뒤 계산"},
    ]

    # ---- 예시·표본 ----
    jr = sorted([r for r in judged], key=lambda r: (-r["judge_mean"], r["id"]))
    top, bottom = jr[:2], jr[-2:][::-1]
    review = human_review(okr)
    recs = {r["id"]: r for r in runs}
    timing = {"pipeline_sec_total": round(sum(r["sec"]["total"] for r in runs), 1),
              "pipeline_sec_mean": round(float(np.mean([r["sec"]["total"] for r in runs])), 1),
              "judge_sec_total": round(sum(v["sec"] for v in jd["judged"].values()), 1)}
    out = {"name": f"reports_{a.name}", "date": a.name, "pipeline_model": run["meta"].get("pipeline_model"),
           "judge_model": jd["meta"].get("judge_model"), "judge_prompt": jd["meta"].get("prompt"),
           "n_attempts": n, "blocked_db_writes": run["meta"].get("blocked_db_writes", []), "timing": timing,
           "metrics": metrics, "discrimination": {"by_level": {LEVEL_KO[k]: v for k, v in by_level.items()}, "spearman_level_vs_score": rho,
                                                  "monotone_overall": monotone, "per_role": per_role, "mixed": mix},
           "m41_hits": m41_hits, "judge_failures": [k for k, v in jd["judged"].items() if not v["ok"]],
           "human_review": review, "judge2_done": bool(jd.get("judged2")), "rows": rows, "runs": runs}
    os.makedirs(a.dir, exist_ok=True)
    _dump(os.path.join(a.dir, f"reports_{a.name}.json"), out)
    if _ANALYSIS:
        out["_analysis"] = _ANALYSIS
    write_md(out, recs, top, bottom, os.path.join(a.dir, f"reports_{a.name}.md"))
    out.pop("_analysis", None)
    write_xlsx(out, recs, os.path.join(a.dir, f"report_eval_{a.name}.xlsx"))
    for m in metrics:
        print(m["id"], m["value"], m["ci"], m["pass"], m["note"][:120])
    print("discrimination", rho, monotone, per_role)


REGRESS = {"M-38": "하드 제약 — 1.00 유지", "M-38b": "−5%p", "M-39": "−0.2점(평균), CI 하한 ≥3.7 유지", "M-40": "−5%p",
           "M-41": "하드 제약 — 0건 유지", "M-42": "+5%p", "M-43": "−5%p", "M-44": "−0.1(ρ)"}


def human_review(okr):
    """사람 검토 묶음(최대 10): ① 판정 불일치(2회 판정 차·코드 vs LLM) ② 통과 기준 근처 ③ 자동 통과 무작위 2~3."""
    picked, why = [], {}

    def add(rid, reason):
        if rid not in why and len(picked) < 10:
            picked.append(rid)
            why[rid] = reason

    p1 = []
    for r in okr:
        rs = []
        j, j2 = r.get("judge"), r.get("judge2")
        if j and j2:
            d = abs(r["judge_mean"] - r["judge2_mean"])
            dmax = max(abs(j[k] - j2[k]) for k in RUBRIC)
            if d >= 1.5 or dmax >= 2:
                rs.append(f"판정 2회 차이(평균 {r['judge_mean']}→{r['judge2_mean']}, 기준 최대 {dmax}점)")
        if j:
            bad = sum(1 for c in r["consistency"] if c["ok"] is False)
            if bad and j["consistency"] >= 4:
                rs.append(f"코드 점수-서술 불일치 {bad}문장인데 판정 일관성 {j['consistency']}점")
            if not bad and any(c["ok"] for c in r["consistency"]) and j["consistency"] <= 2:
                rs.append(f"코드 일관성 통과인데 판정 일관성 {j['consistency']}점")
            if r["forbidden"] and j["factual"] >= 4 and j["tone"] >= 4:
                rs.append(f"코드 금지 내용 {len(r['forbidden'])}건인데 판정 사실·어조 ≥4")
        if rs:
            p1.append((-len(rs), r["id"], "우선 1: " + "; ".join(rs)))
    for _, rid, reason in sorted(p1):
        if len(picked) < 7:                 # 근처·무작위 몫(3개 이상)을 남긴다
            add(rid, reason)
    near = sorted([r for r in okr if r.get("judge_mean") is not None and 3.5 <= r["judge_mean"] <= 4.4 and r["id"] not in why],
                  key=lambda r: (abs(r["judge_mean"] - 4.0), r["id"]))
    for r in near:
        if len(picked) < 8:
            add(r["id"], f"우선 2: 통과 기준 근처(판정 평균 {r['judge_mean']})")
    auto = [r["id"] for r in okr if r["id"] not in why and r.get("judge_mean") is not None and r["judge_mean"] >= 4.0
            and not r["forbidden"] and not any(c["ok"] is False for c in r["consistency"])]
    rnd = random.Random(20261112)
    rnd.shuffle(auto)
    for rid in auto[:3]:
        add(rid, "무작위: 자동 통과 항목")
    rest = sorted([r for r in okr if r["id"] not in why and r.get("judge_mean") is not None],
                  key=lambda r: (abs(r["judge_mean"] - 4.0), r["id"]))
    for r in rest:                          # 10개가 안 차면 기준 근처 순으로 채운다
        if len(picked) >= 10:
            break
        add(r["id"], f"보충: 기준 근처 순(판정 평균 {r['judge_mean']})")
    return [{"id": rid, "reason": why[rid]} for rid in picked]


def write_review_sheet(wb, out, recs):
    from openpyxl.styles import Alignment, Font, PatternFill
    with open(PROMPT_PATH, encoding="utf-8") as f:
        prompt = f.read()
    crit = prompt[prompt.find("[기준과 앵커]"):prompt.find("[항목 라벨]")].strip()
    rows = {r["id"]: r for r in out["rows"]}
    ws = wb.create_sheet("사람검토")
    head = ["#", "ID", "직무", "답변 수준", "입력(질문·답변 요약·항목 점수)", "실제 리포트(강점·개선점·요약·코치 코멘트)", "채점 기준(루브릭)",
            "선정 이유", "사람 점수(1~5)", "사람 메모"] + [f"AI 점수: {RUBRIC_KO[k]}" for k in RUBRIC] + ["AI 점수: 평균", "AI 점수: 2회차 평균"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    yellow = PatternFill("solid", fgColor="FFFF99")
    wrap = Alignment(wrap_text=True, vertical="top")
    for n, x in enumerate(out["human_review"], 1):
        rec, r = recs[x["id"]], rows[x["id"]]
        inp = "\n".join(f"[{q['seq']}] {q['text']}\n  답변: {q['answer'][:120]}{'…' if len(q['answer']) > 120 else ''}" for q in rec["questions"])
        inp += "\n항목 점수: " + ", ".join(f"{ev.ITEM_KO[k]} {v}" for k, v in rec["item_scores"].items()) + f" / AI 면접 {rec['interview_score']}"
        rep_ = ("[강점]\n- " + "\n- ".join(rec["strengths"]) + "\n[개선점]\n- " + "\n- ".join(rec["improvements"]) +
                f"\n[요약] {rec['summary']}\n[코치 코멘트] {rec['coaching']}\n[연습 과제]\n- " + "\n- ".join(rec["actions"]))
        j = r.get("judge") or {}
        ws.append([n, x["id"], r["role"], LEVEL_KO[r["level"]], inp, rep_, crit if n == 1 else "(1행과 같음)", x["reason"], None, None] +
                  [j.get(k) for k in RUBRIC] + [r.get("judge_mean"), r.get("judge2_mean")])
        ws.cell(ws.max_row, 9).fill = yellow
        ws.cell(ws.max_row, 10).fill = yellow
        for c in ws[ws.max_row]:
            c.alignment = wrap
    for col, w in zip("ABCDEFGHIJKLMNOPQ", [4, 7, 9, 12, 70, 80, 60, 40, 10, 24, 8, 8, 8, 8, 8, 8, 9]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "E2"


def _report_block(rec):
    s = "\n".join(f"  - {x}" for x in rec["strengths"])
    i = "\n".join(f"  - {x}" for x in rec["improvements"])
    acts = "\n".join(f"  {k + 1}. {x}" for k, x in enumerate(rec["actions"]))
    return f"- 강점\n{s}\n- 개선점\n{i}\n- 총평: {rec['summary']}\n- 코치 코멘트({rec['coach_writer']}): {rec['coaching']}\n- 연습 과제\n{acts}"


def write_md(out, recs, top, bottom, path):
    rows = {r["id"]: r for r in out["rows"]}
    L = [f"# AI 리포트 품질 측정 결과 — {out['date']}", "",
         f"- 지표: M-38~M-44 (PRD v0.13 확률론적 명세, HANDOFF 22-1) · 스크립트 `backend/tools/eval_reports.py`",
         f"- 제품 경로 모델: `{out['pipeline_model']}` (컨테이너 `LLM_MODEL`) · 판정 모델: `{out['judge_model']}` · 판정 프롬프트 `{out['judge_prompt']}`",
         *([f"- **주의 — 자기 평가 편향 가능성**: 판정 모델(`{out['judge_model']}`)이 리포트를 쓴 모델과 같다. 같은 모델은 자기 문체·판단을 후하게 볼 수 있어 M-39가 실제보다 높게 나올 수 있다. "
            "6GB GPU를 RAG 평가와 나눠 써서 qwen3:8b를 같이 올리지 못해(모델 교대로 둘 다 멈춤) 4b로 판정했다. 다음 실행은 qwen3:8b(집 PC) 또는 qwen3:14b로 다시 판정해 이 기준선과 비교한다"]
           if out["judge_model"] and str(out["judge_model"]) in str(out["pipeline_model"]) else []),
         f"- 가상 응시 {out['n_attempts']}건 = 직무 3(AI/AX·백엔드·데이터) × 답변 수준 7(우수·보통·미흡·짧음·주제 이탈·혼합A·혼합B), 질문 8개(인성2·이력서3·직무2·기업1), 꼬리질문 없음, 코딩테스트 미사용",
         "- 경로: 그래프 2 노드 `evaluate_blocks → compute → summarize → decide`(load·save 제외) → `report_mail._coach`(CrewAI `report_coach.write`, 실패 시 템플릿) → `report_mail.render`·`render_text`",
         f"- **DB 기록 없음**(db.one/all_/tx 차단, `_coach`의 캐시 UPDATE {len(out['blocked_db_writes'])}회는 실행하지 않고 기록만) · **메일 발송 없음**(mailer.send 차단, 본문 함수만 호출) · LangSmith 추적 끔",
         f"- 걸린 시간: 제품 경로 합계 {out['timing']['pipeline_sec_total']}초(응시당 평균 {out['timing']['pipeline_sec_mean']}초), 판정 합계 {out['timing']['judge_sec_total']}초. Ollama 호출은 순차(동시 1)",
         "- 통계: 평균 95% CI = 응시 단위 부트스트랩(B=5000), 비율 95% CI = Wilson. 표본이 21건이라 구간이 넓다",
         "- **이 실행은 첫 기준선**이다. 제품 코드·프롬프트는 고치지 않았다. 통과 기준 세 겹: ① 하드 제약(M-38 인용·M-41 금지 내용) 위반 0건 "
         "② 평균 목표 + 95% CI 하한(M-39 ≥4.0, 하한 ≥3.7) ③ 회귀 허용 폭 — 다음 실행부터 이 기준선 대비 점수 −0.2점·비율 −5%p", "",
         "## 1. 지표", "", "| ID | 지표 | 값 | 95% CI | n | 목표 | 판정 | 비고 |", "|---|---|---|---|---|---|---|---|"]
    for m in out["metrics"]:
        v = "-" if m["value"] is None else m["value"]
        ci = "-" if not m["ci"] or m["ci"][0] is None else f"[{m['ci'][0]}, {m['ci'][1]}]"
        p = "미측정(사람 평가 전)" if m["pass"] is None else ("통과" if m["pass"] else "**미달**")
        L.append(f"| {m['id']} | {m['name']} | {v} | {ci} | {m['n']} | {m['target']} | {p} | {m['note']} |")
    d = out["discrimination"]
    L += ["", "| ID | 기준선 값 | 회귀 허용 폭(다음 실행부터) |", "|---|---|---|"]
    for m in out["metrics"]:
        L.append(f"| {m['id']} | {'미측정' if m['value'] is None else m['value']} | {REGRESS.get(m['id'], '-')} |")
    L += ["", "## 2. 변별력 (답변 수준 → 리포트의 AI 면접 점수)", "",
          f"- 수준 서열(우수4·보통3·미흡2·짧음/주제 이탈1)과 AI 면접 점수의 Spearman ρ = **{d['spearman_level_vs_score']}** (혼합 제외, n={sum(1 for r in out['rows'] if r['ok'] and r['level'] in LEVEL_ORD)})",
          f"- 전체 평균 기준 단조 감소(우수 > 보통 > 미흡 > max(짧음, 주제 이탈)): **{'예' if d['monotone_overall'] else '아니오'}**", "",
          "| 답변 수준 | n | AI 면접 점수 평균 | 판정 루브릭 평균 |", "|---|---|---|---|"]
    for k, v in d["by_level"].items():
        L.append(f"| {k} | {v['n']} | {v['interview_mean']} | {v['judge_mean']} |")
    L += ["", "| 직무 | 수준별 점수 | 단조 |", "|---|---|---|"]
    for role, v in d["per_role"].items():
        L.append(f"| {role} | {', '.join(f'{k} {s}' for k, s in v['scores'].items())} | {'예' if v['monotone'] else '아니오'} |")
    L += ["", "혼합 응시(의도: 혼합A는 조직 적합성 > 직무 전문성, 혼합B는 반대):", ""]
    for m in d["mixed"]:
        L.append(f"- {m['id']} {LEVEL_KO[m['level']]}: 직무 전문성 {m['expertise']} · 조직 적합성 {m['fit']} → {'의도대로' if m['as_intended'] else '**의도와 다름**'}")
    L += ["", "## 3. 금지 내용(M-41) 발견 목록", ""]
    if out["m41_hits"]:
        L += ["| 응시 | 위치 | 유형 | 표현 | 문장 |", "|---|---|---|---|---|"]
        for h in out["m41_hits"]:
            L.append(f"| {h['id']} | {h['field']} | {h['type']} | {h['text']} | {h['sentence'].replace('|', '/')} |")
    else:
        L.append("없음.")
    L += ["", "## 4. 상위 예시 2개 (판정 루브릭 평균 기준)", ""]
    for r in top:
        L += [f"### {r['id']} · {r['role']} · {LEVEL_KO[r['level']]} — 루브릭 평균 {r['judge_mean']} · AI 면접 {r['interview_score']}", "",
              _report_block(recs[r["id"]]), "", f"- 판정 이유: {json.dumps(r['judge'].get('reasons', {}), ensure_ascii=False)}", ""]
    L += ["## 5. 하위 예시 2개", ""]
    for r in bottom:
        L += [f"### {r['id']} · {r['role']} · {LEVEL_KO[r['level']]} — 루브릭 평균 {r['judge_mean']} · AI 면접 {r['interview_score']}", "",
              _report_block(recs[r["id"]]), "", f"- 판정 이유: {json.dumps(r['judge'].get('reasons', {}), ensure_ascii=False)}",
              f"- 판정 issues: {'; '.join(r['judge'].get('issues', [])) or '-'}", ""]
    L += ["## 6. 사람 검토 묶음 (M-44용, 최대 10개 — 엑셀 `사람검토` 시트에 원문·채점 기준·노란 '사람 점수' 칸)", "",
          ("선정 규칙: ① AI 판정끼리 다른 항목(판정 2회 평균 차 ≥1.5 또는 한 기준이 2점 이상 차이, 코드 검사와 LLM 판정이 엇갈림) "
           "② 통과 기준 근처(판정 평균 3.5~4.4) ③ 자동 통과 항목에서 무작위 2~3개. "
           + ("판정 2회 실행함(1회차 temperature 0, 2회차 0.5)." if out["judge2_done"] else "**판정은 1회만 실행** — 판정 간 불일치 대신 코드-판정 엇갈림·근처·무작위로 골랐다.")), "",
          "| # | 응시 | 직무 | 답변 수준 | 선정 이유 | 사람 점수(1~5) |", "|---|---|---|---|---|---|"]
    for k, x in enumerate(out["human_review"], 1):
        r = rows[x["id"]]
        L.append(f"| {k} | {x['id']} | {r['role']} | {LEVEL_KO[r['level']]} | {x['reason']} |  |")
    L += ["", "리포트 원문은 `reports_" + out["date"] + ".json`의 `runs`(id로 찾기) 또는 엑셀 `실제 출력` 열.", "",
          "## 7. 미달 원인과 개선안 (기준선은 수정 전 코드로 잼 — 승인된 수정은 기준선 기록 뒤 반영)", ""]
    L += out.get("_analysis", ["(실행 후 작성)"])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")


def write_xlsx(out, recs, path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "평가"
    head = ["ID", "직무", "답변 수준", "입력", "기대 출력", "실제 출력"] + [f"점수: {RUBRIC_KO[k]}" for k in RUBRIC] + \
        ["점수: 평균", "AI 면접 점수", "합격", "불합격 사유", "사람 점수"]
    ws.append(head)
    bold, yellow = Font(bold=True), PatternFill("solid", fgColor="FFFF99")
    wrap = Alignment(wrap_text=True, vertical="top")
    for c in ws[1]:
        c.font = bold
    for r in out["rows"]:
        rec = recs[r["id"]]
        inp = "\n".join(f"[{q['seq']}] {q['text']}\n  답변: {q['answer'][:90]}{'…' if len(q['answer']) > 90 else ''}" for q in rec["questions"])
        if r["ok"]:
            inp += "\n항목 점수: " + ", ".join(f"{ev.ITEM_KO[k]} {v}" for k, v in rec["item_scores"].items())
            act = (f"[강점] " + " / ".join(rec["strengths"]) + "\n[개선점] " + " / ".join(rec["improvements"]) +
                   f"\n[요약] {rec['summary']}\n[코치 코멘트·{rec['coach_writer']}] {rec['coaching']}\n[연습 과제] " + " / ".join(rec["actions"]))
            reasons = []
            j = r["judge"]
            if r["judge_mean"] is None:
                reasons.append("판정 실패")
            elif r["judge_mean"] < 4.0:
                reasons.append(f"루브릭 평균 {r['judge_mean']} < 4.0")
            if r["forbidden"]:
                reasons.append("금지 내용 " + ", ".join(f"{h['type']}({h['text']})" for h in r["forbidden"]))
            if any(not q["ok"] for q in r["a4_quotes"] + r["report_quotes"]):
                reasons.append("인용 불일치")
            bad = [c for c in r["consistency"] if c["ok"] is False]
            if bad:
                reasons.append(f"점수-서술 불일치 {len(bad)}문장")
            if r["coach_template"]:
                reasons.append("코치 코멘트 템플릿 대체")
            if r["summary_template"]:
                reasons.append("요약 템플릿 대체")
            scores = [j[k] if j else None for k in RUBRIC] + [r["judge_mean"], r["interview_score"]]
        else:
            act, reasons, scores = f"생성 실패: {r['error']}", ["생성 실패"], [None] * 7
        ws.append([r["id"], r["role"], LEVEL_KO[r["level"]], inp, expected(rec) if r["ok"] else "-", act] + scores +
                  ["O" if not reasons else "X", "; ".join(reasons), None])
        ws.cell(ws.max_row, len(head)).fill = yellow
        for c in ws[ws.max_row]:
            c.alignment = wrap
    for col, w in zip("ABCDEFGHIJKLMNOP", [7, 9, 12, 70, 45, 80, 8, 8, 8, 8, 8, 8, 9, 5, 30, 10]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "D2"

    s = wb.create_sheet("요약", 0)
    s.append(["AI 리포트 품질 측정", out["date"]])
    s.append(["제품 경로 모델", out["pipeline_model"]])
    s.append(["판정 모델", out["judge_model"]])
    if out["judge_model"] and str(out["judge_model"]) in str(out["pipeline_model"]):
        s.append(["주의", "판정 모델 = 리포트 생성 모델(자기 평가 편향 가능성 — M-39가 높게 나올 수 있음). GPU 6GB 공유로 8b 대신 4b 사용"])
    s.append(["가상 응시 수", out["n_attempts"]])
    s.append([])
    s.append(["ID", "지표", "값", "95% CI 하한", "95% CI 상한", "n", "목표", "판정", "기준선 값", "회귀 허용 폭(다음 실행부터)", "비고"])
    for c in s[s.max_row]:
        c.font = bold
    for m in out["metrics"]:
        ci = m["ci"] or [None, None]
        s.append([m["id"], m["name"], m["value"], ci[0], ci[1], m["n"], m["target"],
                  "미측정(사람 평가 전)" if m["pass"] is None else ("통과" if m["pass"] else "미달"),
                  "미측정" if m["value"] is None else m["value"], REGRESS.get(m["id"], "-"), m["note"]])
    s.append([])
    d = out["discrimination"]
    s.append(["변별력", "Spearman(수준 서열 vs AI 면접 점수)", d["spearman_level_vs_score"], None, None, None, None,
              "단조" if d["monotone_overall"] else "비단조"])
    s.append(["답변 수준", "n", "AI 면접 점수 평균", "판정 루브릭 평균"])
    for c in s[s.max_row]:
        c.font = bold
    for k, v in d["by_level"].items():
        s.append([k, v["n"], v["interview_mean"], v["judge_mean"]])
    s.append([])
    s.append(["합격 행 수", sum(1 for row in ws.iter_rows(min_row=2, values_only=True) if row[13] == "O"), "/", out["n_attempts"]])
    s.append(["통과 기준", "① 하드 제약(M-38·M-41) 위반 0건 ② 평균 목표 + 95% CI 하한(M-39 ≥4.0 / 하한 ≥3.7) ③ 회귀: 다음 실행부터 기준선 대비 점수 −0.2점·비율 −5%p 넘게 나빠지면 실패"])
    s.append(["기준선", f"이 실행({out['date']})이 첫 기준선. 제품 코드·프롬프트는 이 실행에서 고치지 않았다"])
    s.append(["사람 점수(M-44)", "미측정(사람 평가 전). '사람검토' 시트 노란 칸(1~5 정수) — " + ", ".join(x["id"] for x in out["human_review"])])
    s.column_dimensions["A"].width = 14
    s.column_dimensions["B"].width = 38
    s.column_dimensions["K"].width = 90
    write_review_sheet(wb, out, recs)
    wb.save(path)


_ANALYSIS = []


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--out", default="/tmp/repeval/run.json")
    r.add_argument("--limit", type=int, default=0)
    j = sp.add_parser("judge")
    j.add_argument("--run", default="/tmp/repeval/run.json")
    j.add_argument("--out", default="/tmp/repeval/judged.json")
    j.add_argument("--model", default="qwen3:8b")
    j.add_argument("--timeout", type=int, default=600)
    j.add_argument("--second", action="store_true", help="2회차 판정(temperature 0.5)")
    rp = sp.add_parser("report")
    rp.add_argument("--dir", default="/tmp/repeval")
    rp.add_argument("--name", default="2026-11-12")
    rp.add_argument("--analysis", default="", help="7장 원인·개선안 md 조각 파일(선택)")
    a = p.parse_args()
    if a.cmd == "report" and a.analysis and os.path.exists(a.analysis):
        with open(a.analysis, encoding="utf-8") as f:
            _ANALYSIS[:] = f.read().splitlines()
    {"run": cmd_run, "judge": cmd_judge, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
