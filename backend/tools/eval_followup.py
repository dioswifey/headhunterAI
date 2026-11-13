"""그래프 1-B 꼬리질문(A3) 평가 — 판단 정확도 · 꼬리질문 품질 · 지연 (EV-55·56·57, M-50, 2026-11-12).

제품 코드는 고치지 않는다. `agents/question_graph.judge(att, q, answer)`를 **DB 없이** 부른다(att·q는 이 파일의 dict).
데이터는 아래 **가상 답변**만 쓴다(질문 4개 × 구체적·모호·짧음·주제 이탈 각 2개 = 32개 + 내용 없음 2개). 지원자 데이터 없음.
모든 모델 호출은 llm.chat_json(로컬 Ollama). 생성(A3) = 컨테이너 LLM_MODEL(qwen3:4b), 품질 판정 = --model(기본 qwen3:4b — 6GB GPU에서 8b를 같이 올리면 4b와 번갈아 적재돼 멈춤, 2026-11-12 조정자 결정. 자기 평가 편향 가능성은 결과 md에 적음).
호출은 한 번에 1개(순차) — GPU 6GB 경합 방지(R-17). GPU 실행은 다른 평가가 끝난 뒤에 한다.

    python -m tools.eval_followup check                                    # 모델 없이: 데이터셋·생략 규칙만 확인
    python -m tools.eval_followup run    --out /tmp/followup/run.json      # A3 판단 + 지연(원시 지연을 재려고 제한 시간 60초)
    python -m tools.eval_followup judge  --run /tmp/followup/run.json --out /tmp/followup/judged.json --model qwen3:4b
    python -m tools.eval_followup report --dir /tmp/followup --name followup_2026-11-12   # → <name>.json · <name>.md
    # 별도 부분집합 ideal(모범 답변·기대 키워드 → 판단 ②·③, 2026-11-13). 기준선과 파일·지표 키를 섞지 않는다.
    python -m tools.eval_followup run    --subset ideal                    # → /tmp/followup/ideal_run.json
    python -m tools.eval_followup judge  --subset ideal --model qwen3:4b   # → /tmp/followup/ideal_judged.json
    python -m tools.eval_followup report --subset ideal --name followup_ideal_2026-11-13   # 기준선 run.json이 있으면 지연 차이도 적음

라벨: 구체적 = 꼬리질문 불필요(False), 모호(일반론)·짧음·주제 이탈 = 필요(True) — INT-06 조건 ①③. 내용 없음은 규칙 생략(정확도에서 뺌).
지연: 운영 기준 P95 ≤ 5초(M-50). 개발 PC(qwen3:4b, GTX 1660 SUPER) 값은 참고값이며 FOLLOWUP_TIMEOUT 12초·5초 초과 비율을 함께 적는다.
"""
import argparse
import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, llm, questions as Q  # noqa: E402

QUESTIONS = {
    "q1": ("ai_ax", "최근 프로젝트에서 RAG 검색 품질을 어떻게 평가하고 개선했는지 설명해 주세요."),
    "q2": ("backend", "트래픽이 갑자기 늘었을 때 API 응답 지연을 줄인 경험을 설명해 주세요."),
    "q3": ("data_sci", "데이터에 결측치와 이상치가 많을 때 어떻게 처리했는지 설명해 주세요."),
    "q4": ("common", "팀원과 의견이 크게 달랐을 때 어떻게 합의에 이르렀는지 설명해 주세요."),
}
# (id, 질문, 종류, 라벨(꼬리질문 필요), 답변) — 모두 가상 답변
ANSWERS = [
    ("a01", "q1", "specific", False, "사내 규정 질문 120개로 평가 세트를 만들고 정답 문서가 상위 5개에 드는 비율을 쟀습니다. 처음 0.62였는데 조항 단위로 자르고 제목을 본문에 붙여 0.81로 올렸고, 재순위 모델을 붙인 뒤 답변 근거 표시 정확도도 함께 확인했습니다."),
    ("a02", "q1", "specific", False, "검색 실패 사례 40건을 직접 분류해 보니 절반이 약어 때문이었습니다. 약어 사전을 만들어 질의를 확장했고, 같은 평가 세트에서 Recall@4가 0.70에서 0.84로 올랐습니다. 변경 전후를 같은 질의로 비교해 회귀가 없는지도 봤습니다."),
    ("a03", "q1", "generic", True, "검색 품질은 정말 중요하다고 생각합니다. 그래서 항상 꼼꼼하게 평가하고, 문제가 있으면 개선하려고 노력했습니다. 팀원들과도 많이 소통했습니다."),
    ("a04", "q1", "generic", True, "RAG는 검색이 핵심이라서 여러 방법을 시도해 봤습니다. 좋은 임베딩 모델을 쓰고 청크도 잘 나누면 대체로 품질이 좋아집니다. 결과적으로 성능이 좋아졌습니다."),
    ("a05", "q1", "short", True, "네, 평가해 봤습니다."),
    ("a06", "q1", "short", True, "임베딩 모델을 바꿨습니다."),
    ("a07", "q1", "offtopic", True, "저는 주말마다 등산을 하면서 체력을 기르고 있습니다. 꾸준함이 제 장점이라고 생각하고, 산 정상에 오르면 성취감이 큽니다."),
    ("a08", "q1", "offtopic", True, "프론트엔드에서는 React 상태 관리가 어렵습니다. Redux 대신 Zustand를 써 보니 코드가 짧아져서 만족스러웠습니다."),
    ("a09", "q2", "specific", False, "주문 API p95가 1.8초까지 올라 원인을 보니 상품 조회 쿼리가 매번 조인을 했습니다. 인덱스를 추가하고 인기 상품은 Redis에 5분 캐시해 p95를 320ms로 낮췄고, 부하 테스트로 초당 800건까지 확인했습니다."),
    ("a10", "q2", "specific", False, "트래픽이 세 배로 늘었을 때 DB 커넥션 풀이 고갈돼 지연이 생겼습니다. 풀 크기를 20에서 50으로 늘리고 느린 외부 호출은 비동기 큐로 뺐더니 타임아웃 오류가 하루 300건에서 10건 아래로 줄었습니다."),
    ("a11", "q2", "generic", True, "트래픽이 늘면 서버가 힘들어지기 때문에 성능 최적화가 필요합니다. 캐시나 스케일 아웃 같은 방법을 적절히 쓰면 해결됩니다."),
    ("a12", "q2", "generic", True, "그런 상황에서는 침착하게 원인을 찾는 것이 중요하다고 생각합니다. 여러 지표를 보고 문제를 해결했고 결국 잘 마무리되었습니다."),
    ("a13", "q2", "short", True, "캐시를 썼습니다."),
    ("a14", "q2", "short", True, "서버를 늘렸습니다."),
    ("a15", "q2", "offtopic", True, "저는 대학교 때 동아리 회장을 맡아 행사를 기획했습니다. 사람들을 모으고 예산을 관리하는 경험이 많았습니다."),
    ("a16", "q2", "offtopic", True, "요즘 머신러닝 공부를 하고 있습니다. 선형 회귀부터 시작해서 신경망까지 배우는 중입니다."),
    ("a17", "q3", "specific", False, "센서 데이터에 결측치가 12% 있었는데, 연속 구간이 짧은 곳은 선형 보간하고 30분 넘게 빈 구간은 제외했습니다. 이상치는 IQR 1.5배 밖을 표시만 하고 장비 로그와 대조해 실제 고장인 경우만 지웠더니 모델 정확도가 0.81에서 0.86으로 올랐습니다."),
    ("a18", "q3", "specific", False, "설문 데이터에서 소득 항목 결측이 20%였는데 무작위 결측이 아니어서 결측 여부를 별도 변수로 두고 중앙값으로 채웠습니다. 이상치는 업무 담당자와 기준을 정해 상위 0.5%를 상한값으로 잘랐습니다."),
    ("a19", "q3", "generic", True, "결측치와 이상치는 데이터 분석에서 늘 나오는 문제입니다. 상황에 맞게 적절히 처리하는 것이 중요하고, 저도 그렇게 해 왔습니다."),
    ("a20", "q3", "generic", True, "데이터 품질이 좋아야 모델도 좋아지기 때문에 전처리에 시간을 많이 씁니다. 다양한 방법으로 정제해서 좋은 결과를 얻었습니다."),
    ("a21", "q3", "short", True, "평균으로 채웠습니다."),
    ("a22", "q3", "short", True, "이상치는 지웠어요."),
    ("a23", "q3", "offtopic", True, "저는 시각화 도구 중에서 Tableau를 가장 좋아합니다. 대시보드를 예쁘게 만드는 데 자신이 있습니다."),
    ("a24", "q3", "offtopic", True, "회사를 고를 때 저는 성장 가능성을 가장 중요하게 봅니다. 배울 점이 많은 곳에서 일하고 싶습니다."),
    ("a25", "q4", "specific", False, "API 설계에서 팀원은 REST를, 저는 GraphQL을 주장했습니다. 두 방식으로 같은 화면 3개를 시제품으로 만들어 요청 수와 개발 시간을 비교했고, 데이터상 REST가 우리 규모에 맞아 제가 의견을 접고 문서로 결정 이유를 남겼습니다."),
    ("a26", "q4", "specific", False, "배포 주기를 두고 QA 담당과 의견이 갈렸습니다. 지난 3개월 장애 기록을 같이 보니 금요일 배포 장애가 40%라서, 목요일까지만 배포하고 대신 기능 플래그를 쓰는 안으로 합의했습니다."),
    ("a27", "q4", "generic", True, "의견이 다를 때는 서로 존중하는 태도가 가장 중요하다고 생각합니다. 충분히 대화하면 대부분 좋은 방향으로 합의할 수 있습니다."),
    ("a28", "q4", "generic", True, "저는 갈등을 싫어하는 편이라 최대한 상대방 이야기를 들으려고 합니다. 그렇게 하면 결국 잘 해결됩니다."),
    ("a29", "q4", "short", True, "대화로 풀었습니다."),
    ("a30", "q4", "short", True, "제가 양보했습니다."),
    ("a31", "q4", "offtopic", True, "저는 Python과 Java를 다룰 수 있고, 최근에는 Go도 공부하고 있습니다. 언어를 빨리 배우는 편입니다."),
    ("a32", "q4", "offtopic", True, "이전 회사에서는 연봉이 낮아서 이직을 결심했습니다. 이번에는 보상이 좋은 곳에서 일하고 싶습니다."),
    ("a33", "q1", "no_content", None, "모르겠습니다."),
    ("a34", "q2", "no_content", None, "패스"),
]
# ---- 별도 부분집합 ideal (2026-11-13): 모범 답변·기대 키워드가 있는 은행 문항 경로(판단 ②·③) ----
# 기준선 34건(ANSWERS)과 지표를 섞지 않는다(run/judge/report --subset ideal → ideal_*.json, 지표 키 끝에 '-ideal').
# 실제 경로는 Redis 은행 문항(gen_method=redis:…)의 expected_keywords·model_answer를 bank_reference가 읽는다.
# 평가는 DB·Redis 없이 돌려야 하므로 이 프로세스 안에서만 bank_reference를 아래 값으로 바꿔 끼운다(제품 코드 무수정).
# 그래서 Redis 조회 시간(REF_TIMEOUT 0.3초 상한)은 M-50-ideal에 들어가지 않는다 — 늘어난 프롬프트·specificity 필드 몫만 잰다.
IDEAL_REF = {
    "q1": ("평가 세트|골든셋|정답셋, Recall@k|Recall|재현율|hit rate, 청크|chunk|분할, 재순위|rerank|리랭크",
           "정답 문서가 정해진 평가 질의 세트를 만들고 Recall@k 같은 검색 지표로 기준선을 잰 뒤, 실패 사례를 분류해 청크 분할·질의 확장·재순위 등 "
           "원인별로 고치고 같은 세트로 변경 전후를 비교해 회귀가 없는지 확인한다."),
    "q2": ("캐시|cache|Redis, 인덱스|index, 부하 테스트|부하테스트|load test, p95|p99|응답 시간|지연 시간",
           "p95 같은 지표로 병목 지점(DB 쿼리·커넥션 풀·외부 호출)을 먼저 찾고, 인덱스·캐시·비동기 처리·수평 확장 중 원인에 맞는 조치를 한 뒤 "
           "부하 테스트로 개선 폭을 수치로 확인한다."),
    "q3": ("결측|missing, 보간|대체|imputation|채움|채워, 이상치|outlier, IQR|z-score|사분위",
           "결측의 비율과 발생 원인(무작위 여부)을 먼저 확인하고 삭제·보간·대체·결측 표시 변수 중 근거를 갖고 고르며, 이상치는 IQR이나 업무 기준으로 찾아 "
           "실제 오류인지 확인한 뒤 처리하고, 처리 전후 모델 성능 변화를 비교한다."),
    "q4": ("데이터|근거|지표, 시제품|프로토타입|실험|비교, 문서|문서화|회고",
           "의견 차이의 원인을 정리하고 데이터·시제품 같은 객관적 근거로 대안을 비교한 뒤 정한 기준에 따라 합의하고, 결정 이유를 문서로 남겨 "
           "이후 결과를 함께 점검한다."),
}
# (id, 질문, 종류, 라벨, 답변, 기대 경로) — 가상 답변. 기대 경로: None(꼬리질문 없음) / '③'(키워드는 있으나 구체성 낮음) / '②'(키워드 0개)
# ideal_kw_abstract는 ②가 안 걸리게 키워드를 일부러 넣었다 → ③(또는 일반론)이 잡아야 한다. 이게 ③ 경로를 따로 보는 케이스.
IDEAL_ANSWERS = [
    ("i01", "q1", "ideal_specific", False, "사내 FAQ 질의 150개로 평가 세트를 만들고 Recall@5를 기준선으로 쟀더니 0.58이었습니다. 실패 60건을 분류하니 표가 잘린 경우가 많아 청크를 표 단위로 다시 나눴고, 재순위 모델을 붙여 같은 세트에서 0.79까지 올린 뒤 기존에 맞던 질의가 틀어지지 않았는지 전후 비교했습니다.", None),
    ("i02", "q1", "ideal_kw_abstract", True, "평가 세트를 만들고 Recall도 확인했습니다. 청크도 조정하고 재순위도 적용해 보면서 여러 가지로 개선했고 결과적으로 좋아졌습니다.", "③"),
    ("i03", "q2", "ideal_specific", False, "결제 API p95가 2.1초로 오른 날 슬로 쿼리 로그를 보니 주문 내역 조회가 전체 스캔을 하고 있었습니다. 복합 인덱스를 추가하고 자주 보는 요약은 Redis에 1분 캐시해 p95를 410ms로 낮췄고, 부하 테스트로 초당 1,200건에서도 오류가 없음을 확인했습니다.", None),
    ("i04", "q2", "ideal_kw_abstract", True, "캐시를 도입하고 인덱스도 추가했습니다. 부하 테스트도 해 보고 p95 같은 지표도 보면서 전반적으로 응답 시간을 줄였습니다.", "③"),
    ("i05", "q3", "ideal_specific", False, "매장 매출 데이터에서 결측이 8%였는데 휴무일에 몰려 있어 무작위 결측이 아니었습니다. 휴무 여부 변수를 따로 만들고 나머지는 같은 요일 중앙값으로 보간했고, 이상치는 IQR로 찾은 뒤 실제 대량 주문 기록과 대조해 입력 오류 37건만 지웠더니 예측 오차가 14%에서 11%로 줄었습니다.", None),
    ("i06", "q3", "ideal_kw_abstract", True, "결측은 적절히 보간하거나 대체했고 이상치는 IQR 같은 방법으로 처리했습니다. 상황에 맞게 잘 정제했습니다.", "③"),
    ("i07", "q4", "ideal_specific", False, "로그 저장소를 두고 팀원은 Elasticsearch를, 저는 Loki를 주장했습니다. 일주일 치 로그로 두 시제품을 띄워 비교해 보니 Loki 비용이 3분의 1이었지만 우리가 자주 쓰는 전문 검색이 10배 느려, 그 데이터를 근거로 Elasticsearch로 정하고 결정 이유를 문서로 남겼습니다.", None),
    ("i08", "q4", "ideal_kw_abstract", True, "데이터와 근거를 바탕으로 서로 비교해 보고 합의했습니다. 결정한 내용은 문서로 남겼고 결과적으로 잘 마무리되었습니다.", "③"),
    ("i09", "q1", "ideal_kw_zero", True, "검색은 정말 중요해서 팀원들과 자주 이야기하며 품질을 높이려고 노력했습니다. 사용자 반응도 좋아졌습니다.", "②"),
    ("i10", "q2", "ideal_kw_zero", True, "서버가 느려졌을 때 팀원들과 원인을 찾아서 하나씩 해결했습니다. 결국 문제없이 서비스를 운영할 수 있었습니다.", "②"),
]
JUDGE_SCHEMA = {"type": "object", "properties": {
    "targets_gap": {"type": "integer", "minimum": 1, "maximum": 5}, "on_intent": {"type": "integer", "minimum": 1, "maximum": 5},
    "no_leading": {"type": "integer", "minimum": 1, "maximum": 5}, "answerable": {"type": "integer", "minimum": 1, "maximum": 5},
    "unbiased": {"type": "integer", "minimum": 1, "maximum": 5}, "reason": {"type": "string"}},
    "required": ["targets_gap", "on_intent", "no_leading", "answerable", "unbiased", "reason"]}
RUBRIC = ("targets_gap", "on_intent", "no_leading", "answerable", "unbiased")
RUBRIC_KO = {"targets_gap": "빠진 부분 구체화", "on_intent": "원 질문 의도 유지", "no_leading": "정답 유도 없음",
             "answerable": "한 문장·60초 답변 가능", "unbiased": "비편향·인적 정보 없음"}
JUDGE_SYSTEM = """너는 1차 AI 면접의 꼬리질문 품질을 채점하는 심사위원이다. 원래 질문, 지원자 답변, 시스템이 만든 꼬리질문을 보고 각 항목을 1~5 정수로 매긴다.
- targets_gap: 답변에서 빠지거나 모호한 부분(사례·수치·과정·질문과의 연결)을 정확히 짚어 더 구체적으로 묻는가
- on_intent: 원래 질문이 확인하려던 역량을 그대로 겨냥하는가(엉뚱한 주제로 새지 않는가)
- no_leading: 지원자가 말하지 않은 정답·키워드를 꼬리질문이 알려 주지 않는가
- answerable: 한국어 한 문장이고 60초 안에 답할 수 있는 범위인가
- unbiased: 나이·가족·학벌·출신 등 인적 정보를 묻지 않고, 추궁·비난하지 않는가
reason에는 가장 낮은 항목의 이유를 한 문장으로 쓴다."""


def _att():
    return {"id": 0, "followups_used": 0}


def _q(qid):
    return {"id": 0, "parent_id": None, "text": QUESTIONS[qid][1], "resume_ref": None, "seq": 1, "eval_items": []}


def _q_ideal(qid):
    # gen_method 표시는 기록용(bank_reference는 아래 _fake_ref로 바꿔 끼우므로 Redis를 읽지 않음)
    return {**_q(qid), "gen_method": f"eval-ideal:{qid}", "_qid": qid}


def _fake_ref(G):
    def ref(q):
        qid = q.get("_qid")
        if qid not in IDEAL_REF:
            return {"keywords": [], "ideal": ""}
        kw, ideal = IDEAL_REF[qid]
        return {"keywords": G.keyword_groups(kw), "ideal": ideal}
    return ref


SUBSETS = {"base": ANSWERS, "ideal": IDEAL_ANSWERS}


def _rows_of(subset):
    return [x[:5] for x in SUBSETS[subset]]


def _paths(subset, d="/tmp/followup"):
    p = "" if subset == "base" else subset + "_"
    return os.path.join(d, p + "run.json"), os.path.join(d, p + "judged.json")


def _pct(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def wilson(k, n, z=1.96):
    if not n:
        return None, None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(p, 4), round(max(0, c - h), 4), round(min(1, c + h), 4)


def boot_mean(xs, b=5000, seed=7):
    if not xs:
        return None, None, None
    rng = random.Random(seed)
    ms = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(b))
    return round(sum(xs) / len(xs), 4), round(ms[int(0.025 * b)], 4), round(ms[int(0.975 * b) - 1], 4)


def cmd_check(a):
    from app import evaluation
    kinds = {}
    for aid, qid, kind, label, text in ANSWERS:
        kinds[kind] = kinds.get(kind, 0) + 1
        assert qid in QUESTIONS, aid
    skip = [aid for aid, qid, kind, label, text in ANSWERS if evaluation.NO_CONTENT.match(text) or len(text.strip()) < 5]
    bad_skip = [aid for aid, qid, kind, label, text in ANSWERS if (aid in skip) != (kind == "no_content")]
    print(json.dumps({"answers": len(ANSWERS), "by_kind": kinds, "rule_skip": skip, "rule_skip_mismatch": bad_skip,
                      "labeled": sum(1 for x in ANSWERS if x[3] is not None), "llm_model": config.LLM_MODEL,
                      "followup_timeout_env": config.FOLLOWUP_TIMEOUT}, ensure_ascii=False))
    # ideal 부분집합: 모델 없이 판단 ②(코드 키워드 대조)·데이터 형식만 확인
    from app.agents import question_graph as G
    ik = {}
    for aid, qid, kind, label, text, exp in IDEAL_ANSWERS:
        ik[kind] = ik.get(kind, 0) + 1
        assert qid in IDEAL_REF and len(text.strip()) >= 5 and not evaluation.NO_CONTENT.match(text), aid
    info = {"ideal_answers": len(IDEAL_ANSWERS), "ideal_by_kind": ik, "ids_unique": len({x[0] for x in ANSWERS + IDEAL_ANSWERS}) == len(ANSWERS) + len(IDEAL_ANSWERS)}
    if not hasattr(G, "judge_detail"):
        info["new_judge"] = False
        info["note"] = "컨테이너 코드가 판단 ②③ 이전(judge_detail 없음) — 재빌드 후 다시 check"
        print(json.dumps(info, ensure_ascii=False))
        return 1 if bad_skip else 0
    info["new_judge"] = True
    # 기준선이 바뀌지 않는지: 기준선 질문(gen_method 없음)은 키워드·모범 답변이 비어 있어야 기존 프롬프트 그대로
    info["base_ref_empty"] = all(G.bank_reference(_q(q)) == {"keywords": [], "ideal": ""} for q in QUESTIONS)
    kw_bad = []
    hits = {}
    for aid, qid, kind, label, text, exp in IDEAL_ANSWERS:
        groups = G.keyword_groups(IDEAL_REF[qid][0])
        h = G.keyword_hits(groups, text)
        hits[aid] = f"{len(h)}/{len(groups)}"
        if (exp == "②") != (not h):          # ② 기대 = 키워드 0개, 나머지 = 1개 이상
            kw_bad.append(aid)
    info.update(keyword_hits=hits, keyword_expect_mismatch=kw_bad)
    print(json.dumps(info, ensure_ascii=False))
    return 0 if not (bad_skip or kw_bad or not info["base_ref_empty"] or not info["ids_unique"]) else 1


def cmd_run(a):
    """judge를 그대로 부르되 llm.chat_json을 감싸 원시 지연·시간 초과를 기록한다(제품 코드 무수정)."""
    from app.agents import question_graph as G
    config.FOLLOWUP_TIMEOUT = a.timeout                      # 원시 지연을 재려고 넉넉히(판정은 5초·12초 기준으로 따로 셈)
    orig = llm.chat_json
    rec = {}

    def wrapped(*args, **kw):
        t = time.time()
        try:
            out = orig(*args, **kw)
            rec.update(sec=time.time() - t, error=None, raw=out)
            return out
        except llm.LLMError as e:
            rec.update(sec=time.time() - t, error=str(e)[:120], raw=None)
            raise
    G.llm.chat_json = wrapped
    ideal = a.subset == "ideal"
    orig_ref = getattr(G, "bank_reference", None)
    if ideal:
        if not hasattr(G, "judge_detail"):
            G.llm.chat_json = orig
            print("판단 ②③ 이전 코드(judge_detail 없음) — 재빌드 후 실행", file=sys.stderr)
            return 1
        G.bank_reference = _fake_ref(G)                     # 이 프로세스 안에서만(제품 코드 무수정)
    done = {}
    if os.path.exists(a.out):
        done = {r["id"]: r for r in json.load(open(a.out, encoding="utf-8"))["rows"]}
    rows = []
    try:
        for item in SUBSETS[a.subset]:
            aid, qid, kind, label, text = item[:5]
            if aid in done:
                rows.append(done[aid])
                continue
            rec.clear()
            t = time.time()
            if ideal:
                det = G.judge_detail(_att(), _q_ideal(qid), text)
                res = det["followup"]
            else:
                det, res = None, G.judge(_att(), _q(qid), text)
            row = {"id": aid, "qid": qid, "role": QUESTIONS[qid][0], "kind": kind, "label": label, "answer": text,
                   "followup": res[0] if res else None, "reason": res[1] if res else None, "proposed": bool(res),
                   "llm_sec": round(rec["sec"], 3) if "sec" in rec else None, "total_sec": round(time.time() - t, 3),
                   "llm_called": "sec" in rec, "llm_error": rec.get("error"), "pre": rec.get("raw")}
            if ideal:
                row.update(expect_path=item[5], keyword_hits=det["keyword_hits"], specificity=det["specificity"])
            rows.append(row)
            print(aid, kind, "→", "꼬리질문" if res else "없음", rows[-1]["llm_sec"], flush=True)
            json.dump({"meta": {"llm_model": config.LLM_MODEL, "timeout": a.timeout, "date": time.strftime("%Y-%m-%d"), "subset": a.subset},
                       "rows": rows}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    finally:
        G.llm.chat_json = orig
        if ideal:
            G.bank_reference = orig_ref
    return 0


def cmd_judge(a):
    run = json.load(open(a.run, encoding="utf-8"))
    out = json.load(open(a.out, encoding="utf-8")) if os.path.exists(a.out) else {"meta": {"judge_model": a.model}, "judged": {}}
    for r in run["rows"]:
        if not r["followup"] or r["id"] in out["judged"]:
            continue
        user = f"원래 질문: {QUESTIONS[r['qid']][1]}\n지원자 답변: {r['answer']}\n꼬리질문: {r['followup']}"
        t = time.time()
        try:
            j = llm.chat_json(JUDGE_SYSTEM, user, JUDGE_SCHEMA, temperature=0, model=a.model, timeout=a.timeout_judge)
            out["judged"][r["id"]] = {"ok": True, "scores": {k: j[k] for k in RUBRIC}, "reason": j.get("reason"), "sec": round(time.time() - t, 1)}
        except llm.LLMError as e:
            out["judged"][r["id"]] = {"ok": False, "error": str(e)[:120], "sec": round(time.time() - t, 1)}
        print(r["id"], out["judged"][r["id"]].get("scores"), flush=True)
        json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


PATH_OF = {"기대 키워드 0개": "②", "모범 답변 대비 구체성 2점 이하": "③"}


def cmd_report_ideal(a):
    """ideal 부분집합 전용 보고 — 기준선 34건 지표와 섞지 않는다(키 끝 '-ideal', 파일 따로)."""
    rp, jp = _paths("ideal", a.dir)
    run = json.load(open(rp, encoding="utf-8"))
    jd = json.load(open(jp, encoding="utf-8")) if os.path.exists(jp) else {"meta": {}, "judged": {}}
    rows = run["rows"]
    correct = [r for r in rows if r["proposed"] == r["label"]]
    acc = wilson(len(correct), len(rows))
    by_kind = {k: wilson(sum(1 for r in rows if r["kind"] == k and r["proposed"] == r["label"]), sum(1 for r in rows if r["kind"] == k))
               for k in ("ideal_specific", "ideal_kw_abstract", "ideal_kw_zero")}
    for r in rows:
        r["path"] = (PATH_OF.get(r["reason"], "기타(" + r["reason"] + ")") if r["reason"] else None)
    path_match = wilson(sum(1 for r in rows if r["path"] == r["expect_path"]), len(rows))
    paths = {}
    for r in rows:
        paths[str(r["path"])] = paths.get(str(r["path"]), 0) + 1
    spec = {k: [r["specificity"] for r in rows if r["kind"] == k] for k in by_kind}
    spec_missing = sum(1 for r in rows if r["llm_called"] and not r["llm_error"] and r["specificity"] is None)
    lat = [r["llm_sec"] for r in rows if r["llm_sec"] is not None]
    p50, p95 = _pct(lat, 0.5), _pct(lat, 0.95)
    base_rp = _paths("base", a.dir)[0]
    base = None
    if os.path.exists(base_rp):
        bl = [r["llm_sec"] for r in json.load(open(base_rp, encoding="utf-8"))["rows"] if r["llm_sec"] is not None]
        base = {"p50": _pct(bl, 0.5), "p95": _pct(bl, 0.95), "n": len(bl)}
    js = [v for v in jd["judged"].values() if v.get("ok")]
    per = [sum(v["scores"].values()) / len(RUBRIC) for v in js]
    q_mean = boot_mean(per)
    leak = sum(1 for v in js if v["scores"].get("no_leading", 5) <= 2)
    d50 = round(p50 - base["p50"], 3) if base and p50 is not None and base["p50"] is not None else None
    d95 = round(p95 - base["p95"], 3) if base and p95 is not None and base["p95"] is not None else None
    metrics = {
        "EV-55-ideal": {"value": acc[0], "ci95": [acc[1], acc[2]], "n": len(rows), "pass": None,
                        "note": "종류별 " + ", ".join(f"{k} {v[0]}" for k, v in by_kind.items())
                                + f" · 기대 경로 일치 {path_match[0]} · 경로 분포 {paths} · specificity 누락 {spec_missing}"},
        "EV-56-ideal": {"mean": q_mean[0], "ci95": [q_mean[1], q_mean[2]], "n": len(per), "pass": None,
                        "note": f"정답 유도 없음 ≤2점 {leak}건(모범 답변이 프롬프트에 들어가는 경로라 힌트 누출을 따로 봄)"},
        "M-50-ideal": {"value": round(p95, 3) if p95 is not None else None, "p50": round(p50, 3) if p50 is not None else None, "n": len(lat),
                       "pass": (p95 <= 5) if p95 is not None else None,
                       "note": f"P50 {p50 and round(p50, 2)}초 · P95 {p95 and round(p95, 2)}초 · 기준선 대비 P50 {d50:+}초 · P95 {d95:+}초" if d50 is not None and d95 is not None
                               else f"P50 {p50 and round(p50, 2)}초 · P95 {p95 and round(p95, 2)}초 · 기준선 run.json 없음(비교 불가)"},
    }
    result = {"name": a.name, "subset": "ideal", "date": run["meta"].get("date"), "llm_model": run["meta"].get("llm_model"),
              "judge_model": jd["meta"].get("judge_model"), "metrics": metrics, "by_kind": by_kind, "paths": paths,
              "specificity": spec, "latency": {"p50": p50, "p95": p95, "n": len(lat), "base": base, "delta_p50": d50, "delta_p95": d95},
              "rows": rows, "judged": jd["judged"]}
    json.dump(result, open(os.path.join(a.dir, a.name + ".json"), "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    md = [f"# 꼬리질문(A3) 평가 — ideal 부분집합 {a.name}", "",
          f"- 모범 답변·기대 키워드가 있는 은행 문항 경로(판단 ②·③). 기준선 34건과 **따로** 본다. 생성 {result['llm_model']} · 판정 {result['judge_model']} · 가상 답변 {len(rows)}개",
          "- 평가에서는 bank_reference를 프로세스 안에서 고정값으로 바꿔 끼웠다 → Redis 조회 시간(최대 0.3초)은 지연에 들어가지 않는다.",
          "- 판정 모델이 생성 모델과 같으면 EV-56-ideal은 참고값(R-16).", "",
          "| 지표 | 값 | 95% CI | n | 비고 |", "|---|---|---|---|---|"]
    for k, m in metrics.items():
        md.append(f"| {k} | {m.get('value', m.get('mean'))} | {m.get('ci95', '-')} | {m['n']} | {m['note']} |")
    md += ["", "## 답변별", "", "| ID | 종류 | 라벨 | 기대 경로 | 실제 경로 | 키워드 | 구체성 | 꼬리질문 | 지연(초) |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['id']} | {r['kind']} | {r['label']} | {r['expect_path']} | {r['path']} | {r['keyword_hits']} | {r['specificity']} | "
                  f"{(r['followup'] or '—').replace('|', '/')} | {r['llm_sec']} |")
    open(os.path.join(a.dir, a.name + ".md"), "w", encoding="utf-8", newline="\n").write("\n".join(md) + "\n")
    print(json.dumps(metrics, ensure_ascii=False, indent=1))
    return 0


def cmd_report(a):
    if a.subset == "ideal":
        return cmd_report_ideal(a)
    run = json.load(open(os.path.join(a.dir, "run.json"), encoding="utf-8"))
    jp = os.path.join(a.dir, "judged.json")
    jd = json.load(open(jp, encoding="utf-8")) if os.path.exists(jp) else {"meta": {}, "judged": {}}
    rows = run["rows"]
    lab = [r for r in rows if r["label"] is not None]
    correct = [r for r in lab if r["proposed"] == r["label"]]
    tp = sum(1 for r in lab if r["proposed"] and r["label"])
    fp = sum(1 for r in lab if r["proposed"] and not r["label"])
    fn = sum(1 for r in lab if not r["proposed"] and r["label"])
    acc = wilson(len(correct), len(lab))
    by_kind = {k: wilson(sum(1 for r in lab if r["kind"] == k and r["proposed"] == r["label"]), sum(1 for r in lab if r["kind"] == k))
               for k in ("specific", "generic", "short", "offtopic")}
    rule_ok = all(not r["llm_called"] and not r["proposed"] for r in rows if r["kind"] == "no_content")
    lat = [r["llm_sec"] for r in rows if r["llm_sec"] is not None]
    p50, p95 = _pct(lat, 0.5), _pct(lat, 0.95)
    over5 = wilson(sum(1 for x in lat if x > 5), len(lat))
    over12 = wilson(sum(1 for x in lat if x > 12), len(lat))
    sentences = [r for r in rows if r["followup"]]
    rule_q = {"one_sentence": wilson(sum(1 for r in sentences if Q.one_sentence(r["followup"])), len(sentences)),
              "blocklist_hits": sum(1 for r in sentences if Q.BLOCKLIST.search(r["followup"]))}
    js = [v for v in jd["judged"].values() if v.get("ok")]
    per = [sum(v["scores"].values()) / len(RUBRIC) for v in js]
    q_mean = boot_mean(per)
    q_by = {k: boot_mean([v["scores"][k] for v in js]) for k in RUBRIC}
    metrics = {
        "EV-55": {"value": acc[0], "ci95": [acc[1], acc[2]], "n": len(lab), "pass": None,
                  "note": f"정밀도 {round(tp / (tp + fp), 3) if tp + fp else None} · 재현율 {round(tp / (tp + fn), 3) if tp + fn else None} · 종류별 "
                          + ", ".join(f"{k} {v[0]}" for k, v in by_kind.items()) + f" · 내용 없음 규칙 생략 {rule_ok}"},
        "EV-56": {"mean": q_mean[0], "ci95": [q_mean[1], q_mean[2]], "n": len(per), "pass": None,
                  "note": "항목별 " + ", ".join(f"{RUBRIC_KO[k]} {v[0]}" for k, v in q_by.items()) + f" · 한 문장 {rule_q['one_sentence'][0]} · 차단어 {rule_q['blocklist_hits']}"},
        "M-50": {"value": round(p95, 3) if p95 is not None else None, "p50": round(p50, 3) if p50 is not None else None, "n": len(lat),
                 "pass": (p95 <= 5) if p95 is not None else None,
                 "note": f"A3 판단 P50 {p50 and round(p50, 2)}초 · P95 {p95 and round(p95, 2)}초 · 5초 초과 {over5[0]} · 12초 초과 {over12[0]} "
                         f"({run['meta'].get('llm_model')}, 개발 PC 참고값 — 운영 목표 P95 ≤ 5초)"},
    }
    result = {"name": a.name, "date": run["meta"].get("date"), "llm_model": run["meta"].get("llm_model"), "judge_model": jd["meta"].get("judge_model"),
              "metrics": metrics, "by_kind": by_kind, "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": len(lab) - tp - fp - fn},
              "latency": {"p50": p50, "p95": p95, "over5": over5, "over12": over12, "n": len(lat)}, "rule": rule_q, "rows": rows, "judged": jd["judged"]}
    os.makedirs(a.dir, exist_ok=True)
    json.dump(result, open(os.path.join(a.dir, a.name + ".json"), "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    same = result["llm_model"] and result["llm_model"] == result["judge_model"]
    bias = ("- **자기 평가 편향 주의**: 꼬리질문을 만든 모델과 품질을 채점한 모델이 같다"
            f"({result['judge_model']}). 같은 모델은 자기 출력을 후하게 볼 수 있어(R-16) EV-56 품질 점수는 **참고값**이다. "
            "6GB GPU에서 qwen3:8b를 함께 올리면 4b와 번갈아 적재돼 멈추므로 4b로 판정했다(2026-11-12 조정자 결정). "
            "사람 검토 일치도(M-44) 확인 전에는 통과·미달 판정에 쓰지 않는다. 판단 정확도(EV-55)·지연(EV-57)은 라벨·시간 측정이라 이 편향과 무관하다."
            if same else f"- 판정 모델 {result['judge_model']}은 생성 모델과 다르다. 그래도 같은 qwen3 계열이라 편향 가능성은 남는다(R-16).")
    md = [f"# 꼬리질문(A3) 평가 — {a.name}", "", f"- 생성 모델 {result['llm_model']} · 판정 모델 {result['judge_model']} · 가상 답변 {len(rows)}개(라벨 {len(lab)})", bias, "",
          "| EV | 지표 | 값 | 95% CI | n | 비고 |", "|---|---|---|---|---|---|"]
    for k, m in metrics.items():
        md.append(f"| {k} | {'판단 정확도' if k == 'EV-55' else ('꼬리질문 품질 루브릭' if k == 'EV-56' else 'A3 지연 P95(초)')} | "
                  f"{m.get('value', m.get('mean'))} | {m.get('ci95', '-')} | {m['n']} | {m['note']} |")
    md += ["", "## 답변별", "", "| ID | 종류 | 라벨 | 꼬리질문 | 지연(초) |", "|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['id']} | {r['kind']} | {r['label']} | {(r['followup'] or '—').replace('|', '/')} | {r['llm_sec']} |")
    open(os.path.join(a.dir, a.name + ".md"), "w", encoding="utf-8", newline="\n").write("\n".join(md) + "\n")
    print(json.dumps(metrics, ensure_ascii=False, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("check")
    r = sp.add_parser("run")
    r.add_argument("--subset", choices=sorted(SUBSETS), default="base", help="base = 기준선 34건, ideal = 모범 답변·키워드 경로(별도)")
    r.add_argument("--out", default=None, help="기본 /tmp/followup/run.json (ideal은 ideal_run.json)")
    r.add_argument("--timeout", type=float, default=60.0)
    j = sp.add_parser("judge")
    j.add_argument("--subset", choices=sorted(SUBSETS), default="base")
    j.add_argument("--run", default=None)
    j.add_argument("--out", default=None)
    j.add_argument("--model", default="qwen3:4b")
    j.add_argument("--timeout-judge", type=int, default=600)
    p = sp.add_parser("report")
    p.add_argument("--subset", choices=sorted(SUBSETS), default="base")
    p.add_argument("--dir", default="/tmp/followup")
    p.add_argument("--name", default="followup_2026-11-12")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        a.out = a.out or _paths(a.subset)[0]
    if a.cmd == "judge":
        a.run = a.run or _paths(a.subset)[0]
        a.out = a.out or _paths(a.subset)[1]
    if a.cmd in ("run", "judge"):
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    return {"check": cmd_check, "run": cmd_run, "judge": cmd_judge, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
