"""few-shot 검증 장치 2층 — 로컬 모델 질문 판정의 골든셋 일치도 측정(2026-11-20, 리드 GPU GO).

    docker exec -w /srv headhunterAI-api python -m tools.fewshot_judge_eval --out /tmp/l2.json

골든셋 = 사용자가 실제로 지적한 문장(탈락) + 사람이 통과로 본 문장·사용자가 직접 고쳐 준 문장(통과) + 현재 풀 표본(통과, Claude 판단).
판정 프롬프트 = prompts/evaluate/question_judge_l2.md(예문은 골든셋과 겹치지 않게 따로 씀). 모델 호출 = 문장당 1회, llm.chat_json만.
비교용으로 1층 규칙(question_rules·A18 문장 검사)의 판정도 같이 적는다(모델 호출 없음).
기준: Cohen's kappa ≥ 0.6이면 반영 후보, 미만이면 참고용(반영하지 않음).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import config, llm, prompts  # noqa: E402
from app.agents import internal_question_agent as IQ, question_rules as QR  # noqa: E402

CO = "헤드헌터랩_경기"
# (id, 종류, 기대 판정, 라벨 출처, 문장)
GOLDEN = [
    # ---- 탈락: 사용자가 실제로 지적한 문장 ----
    ("B01", "personality", "fail", "user:F1 attempt14 인성1", "최근 1년 동안 스스로 학습해서 익힌 기술이나 지식과 그 방법을 설명해 주세요."),
    ("B02", "personality", "fail", "user:F2 attempt14 인성2", "비전문가에게 기술적인 내용을 설명해야 했던 경험과 그때 쓴 방법을 설명해 주세요."),
    ("B03", "resume", "fail", "user:F3 attempt14 이력서3",
     "소비기한 인쇄 불량 검사 개선 프로젝트에서, 반사광으로 인해 정상 제품이 불량으로 분류되는 문제를 해결하기 위해 어떤 C++ 및 OpenCV 기술을 적용했는지 설명해 주세요."),
    ("B04", "job", "fail", "user:F8 attempt14 직무", "AI/AX 기술스을 분류할 때, 모델 계층의 정확도와 재현성이 충돌하는 경우 어떤 기준으로 판단하시겠습니까?"),
    ("B05", "company", "fail", "user:F9 attempt14 기업",
     "헤드헌터랩_경기에서 제품 이미지에서 외관 불량을 탐지·분류하는 비전 모델 개발 업무를 맡아, 현장에서 모호한 불량 기준을 데이터로 구체화할 때 어떤 접근을 취했는지 설명해 주세요."),
    ("B06", "job", "fail", "user:F9 A6 재작성",
     "헤드헌터랩_경기에서 비전 모델 서빙 환경에서 트래픽 가중치 합이 100이 아닌 경우 실패라는 설정이 적용되는 상황을 고려할 때, 해당 설정을 검증하기 위해 어떤 판단 과정을 거쳤는지 설명해 주세요."),
    ("B07", "job", "fail", "user:사고3 사내 용어",
     "AI/AX 프로젝트에서 '적용 가능성 검토' 단계에서 규칙 기반 처리, 전통 ML, LLM, RAG 또는 혼합 방식 중 무엇을 고르시겠습니까?"),
    ("B08", "job", "fail", "user:사고4", "LLM 응용에서 모델 답변의 근거 여부와 불확실성을 점검하는 상황이라면, 어떻게 하시겠습니까?"),
    ("B09", "personality", "fail", "user:2026-11-20 내부 도구",
     "헤드헌터랩_서울에서 내부 도구 개발을 맡는다면, 경험하지 않은 기술에 대해 동료가 판단을 요청할 때 신뢰할 수 있는 결정을 돕기 위해 답변을 어떻게 준비하시겠습니까? 예를 들어 '~를 확인했고, 그 결과 판단이 이렇게 달라졌다'처럼 설명해 주세요."),
    ("B10", "job", "fail", "user:사고2 비문+두 개",
     "데이터 준비 과정에서 원천 데이터 사용 승인을 맡는다면, 고객 내부 권한과 책임을 명확히 지정할 때 어떤 절차를 먼저 진행하고, 그 결과물로 무엇을 만들고 싶은가?"),
    ("B11", "company", "fail", "user:사고 6-6 처리 계층",
     "콜센터 AI 상담 시스템에서 AI 처리 계층이 답변을 생성할 때, 출처 정보가 부족할 경우 상담사에게 어떻게 유도하는지 설명해 주세요."),
    # ---- 통과: 사용자가 직접 고쳐 준 방향 · 사람이 통과로 본 문장 ----
    ("G01", "personality", "pass", "user:F1 고친 방향",
     "헤드헌터랩_경기에서 상담 기록을 자동으로 분류하는 프로젝트를 진행한다고 가정할 때, 어떤 기술과 방법으로 구현하시겠습니까? 예를 들어 '문의 유형 분류를 자동화했고, 그 결과 상담사의 처리 시간이 줄었다'처럼 설명해 주세요."),
    ("G02", "personality", "pass", "user:F2 고친 방향",
     "헤드헌터랩_경기에서 생산 라인 오검출 문제를 해결하는 중에 개발팀이 아닌 회계팀에 팀 업무를 브리핑해야 한다면, 무엇을 먼저 설명하시겠습니까?"),
    ("G03", "resume", "pass", "user:F3 고친 방향",
     "소비기한 인쇄 불량 검사를 헤드헌터랩_경기의 생산 라인에서 다시 진행한다고 가정할 때, 어떤 검사 방식과 기술 스택을 택하시겠습니까? 비슷한 경험이 있다면 그 경험을 바탕으로 답해 주셔도 좋습니다."),
    ("G04", "company", "pass", "user:F9 규칙(회사 + 가정형 허용)",
     "헤드헌터랩_경기에서 비전 모델 개발 업무를 맡는다면, 모호한 불량 기준을 데이터로 구체화하기 위해 무엇을 먼저 하시겠습니까?"),
    ("G05", "personality", "pass", "human:2026-11-19 표본 통과(모델 거짓양성)",
     "RFC 소유자가 초안을 작성한 후 검토 단계를 진행할 때, 영향받는 팀의 테크 리드와 같은 검토자에게 피드백을 전달하는 방식을 설명해 주세요."),
    ("G06", "job", "pass", "human:2026-11-19 표본 통과(모델 거짓양성)", "출퇴근 누락이 반복적으로 발생할 때, 어떤 점검을 통해 원인을 파악하시겠습니까?"),
    ("G07", "job", "pass", "human:2026-11-19 표본 통과", "출퇴근 기록이 두 시스템에서 30분 이상 차이가 나면, 무엇부터 확인하시겠습니까?"),
    ("G08", "job", "pass", "prompt:6-3 좋은 예",
     "사내 문서를 근거로 답하는 상담 챗봇이 문서에 없는 내용을 사실처럼 답한 것을 발견했다면, 근거 없는 답변을 걸러내기 위해 무엇을 확인하시겠습니까?"),
    ("G09", "company", "pass", "prompt:6-6 좋은 예",
     "상담사가 쓰는 답변 도우미를 만든다고 할 때, 사내 문서에서 근거를 찾지 못한 질문에 대해 시스템이 무엇을 하도록 설계하시겠습니까?"),
    ("G10", "job", "pass", "prompt:6 좋은 예",
     "AI/AX 프로젝트를 새로 맡는다면 규칙 기반 처리·전통 ML·LLM·RAG 중 무엇을 어떤 기준으로 고르시겠습니까? 관련된 업무 경험이나 프로젝트가 있다면 그것을 바탕으로 답해 주셔도 좋습니다."),
    # ---- 통과: 현재 풀(OpenAI 재생성 602) 표본 — Claude 판단, 사람 확인 전 ----
    ("P01", "job", "pass", "claude:풀 IQ-job-bed4d0fc51-1",
     "헤드헌터랩_서울에서 코사인 유사도가 0.6 이상인 문서만 반환하는 RAG 검색 기능을 구현한다면, 경계 점수에서 문서 포함 여부가 정확한지 확인하기 위한 테스트를 어떻게 설계하시겠습니까?"),
    ("P02", "job", "pass", "claude:풀 IQ-job-ab40922b14-1",
     "헤드헌터랩_서울에서 RAG 검색에 사용하는 임베딩 모델 버전을 바꾼다고 가정할 때, 색인 호환성 문제를 막기 위한 애플리케이션 시작 시 설정 검증을 어떻게 설계하시겠습니까?"),
    ("P03", "job", "pass", "claude:풀 IQ-job-be9aac304e-1",
     "헤드헌터랩_서울에서 벡터DB 검색 기능을 구현한다고 가정할 때, 검색 요청 중 타임아웃이 발생해도 검색 서비스가 일관되게 오류를 처리하도록 예외 변환을 어떻게 설계하시겠습니까?"),
]
LABEL = {"personality": "인성", "job": "직무 이해도", "company": "기업", "resume": "이력서 기반"}
SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["pass", "fail"]},
                                           "codes": {"type": "array", "items": {"type": "string"}},
                                           "reason": {"type": "string"}},
          "required": ["verdict", "codes", "reason"]}


def rule_verdict(cat, q):
    """1층(규칙)만의 판정 — 비교용. A17 문장 검사 + 이력서 규칙 + 깨진 낱말 + 회사 과거형."""
    company = "헤드헌터랩_서울" if "헤드헌터랩_서울" in q else CO
    why = []
    if not IQ._one_sentence(q):
        why.append("not_one_sentence")
    ok, reason = QR.has_malformed_phrasing(q, company=company)   # (통과 여부, 사유)
    if not ok:
        why.append(reason)
    iss = (QR.resume_issue(q, company) if cat == "resume" else QR.interview_issue(q, company))
    if iss:
        why.append(iss)
    if QR.ABSTRACT_EXPERIENCE.search(QR.core(q)):
        why.append("abstract_experience")
    return ("fail" if why else "pass"), why


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pa = sum(1 for a, _ in pairs if a == "fail") / n
    pb = sum(1 for _, b in pairs if b == "fail") / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


def confusion(pairs):
    c = {"TP(fail→fail)": 0, "FN(fail→pass)": 0, "FP(pass→fail)": 0, "TN(pass→pass)": 0}
    for gold, got in pairs:
        c[{("fail", "fail"): "TP(fail→fail)", ("fail", "pass"): "FN(fail→pass)",
           ("pass", "fail"): "FP(pass→fail)", ("pass", "pass"): "TN(pass→pass)"}[(gold, got)]] += 1
    return c


def summary(rows, key, subset=None):
    rs = [r for r in rows if (subset is None or subset(r)) and r.get(key) in ("pass", "fail")]
    pairs = [(r["gold"], r[key]) for r in rs]
    return {"n": len(pairs), "kappa": kappa(pairs), "accuracy": round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None,
            "confusion": confusion(pairs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/fewshot_l2.json")
    ap.add_argument("--model", default=None, help="기본 = config.LLM_MODEL")
    a = ap.parse_args()
    rows, calls = [], 0
    t0 = time.time()
    for gid, cat, gold, src, q in GOLDEN:
        rv, rwhy = rule_verdict(cat, q)
        row = {"id": gid, "category": cat, "gold": gold, "label_source": src, "question": q, "rule": rv, "rule_why": rwhy}
        system = prompts.load("evaluate/question_judge_l2", category_label=LABEL[cat])
        t = time.time()
        try:
            calls += 1
            out = llm.chat_json(system, "질문: " + q, SCHEMA, temperature=0.0, model=a.model)
            row.update({"model": out.get("verdict"), "codes": out.get("codes") or [], "reason": out.get("reason") or ""})
        except Exception as e:  # noqa: BLE001
            row.update({"model": "error", "codes": [], "reason": f"{type(e).__name__}: {e}"[:200]})
        row["sec"] = round(time.time() - t, 1)
        rows.append(row)
        print(f"{gid} gold={gold} model={row['model']} rule={rv} {row['sec']}s {row['codes']} | {row['reason'][:80]}", flush=True)
    human = lambda r: not r["label_source"].startswith("claude:")  # noqa: E731
    res = {"model": a.model or config.LLM_MODEL, "calls": calls, "elapsed_s": round(time.time() - t0, 1),
           "judge_all": summary(rows, "model"), "judge_human_labeled": summary(rows, "model", human),
           "rule_all": summary(rows, "rule"), "rule_human_labeled": summary(rows, "rule", human),
           "threshold": 0.6, "rows": rows}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
