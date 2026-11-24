# -*- coding: utf-8 -*-
"""온톨로지 어휘 **초안 추출** — 지금 있는 질문 602개·조각 메타에서 통제 어휘 후보를 뽑는다. 모델 호출 0회.

왜 필요한가(2026-11-24 진단):
  · `competency`는 자유 서술이라 602문항에 **309종**(싱글톤 248)이고, `intent`는 **596종**이다.
    그런데 `dup_group`(세트 중복 검사)·커버리지 집계가 이 값을 열쇠로 쓴다 → 사실상 작동하지 않는다.
  · `dept_*`는 문항당 평균 2.8개가 켜져 있고(인성은 82개 중 70개가 5개 전부) 관문 1이 거의 걸러내지 못한다.
  · `doc_type`은 헤드헌터랩 전용 고정표(39종 실사용/47종 표)라 다른 기업 문서에 그대로 쓸 수 없다.
그래서 **행위 × 대상 × 산출물** 세 축의 닫힌 어휘를 만든다. 축 자체는 회사와 무관하고, 새 기업 코퍼스는
'이 어휘에 매핑'만 하면 된다(= 온보딩에서 doc_type 분류·dept 추정·임계값 산출 세 단계가 사라진다).

이 도구가 하는 일
  1) `competency`를 **수식어 + 머리 명사**로 쪼갠다(한국어 복합명사는 머리가 뒤에 온다).
  2) 머리 명사를 `ABILITY`(행위 축, 12개)로, 수식어를 `DOMAIN`(대상 축, 9개)으로 **동의어표**로 접는다.
  3) `doc_type`을 `ARTIFACT`(산출물 축, 7개)로 접는다.
  4) 역할(dept) → 축 노드 **가중치**를 데이터에서 센다(= role needs skill 엣지의 초기값).
  5) 어디에도 안 접히는 말(미매핑)을 **사람이 볼 목록**으로 남긴다 — 어휘는 사람이 확정한다.

실행:
  docker exec headhunterAI-api python -m tools.ontology_draft --out /tmp/ontology
"""
import argparse
import collections
import json
import os
import sys

from app import internal_store
from app.agents import internal_qcat

# ---------------- 축 1: 행위(ability) ----------------
# 열쇠 = 표준 이름, 값 = 머리 명사 동의어. 출처는 실제 `competency` 머리 명사 53종(2026-11-24 집계).
ABILITY = {
    "도메인이해": ["이해", "해석", "파악", "인지"],
    "의사결정": ["판단", "결정", "선정", "검토", "평가", "선택"],
    "설계": ["설계", "정의", "구조화", "모델링"],
    "검증": ["검증", "품질", "정확성", "정합성", "확인", "점검", "테스트"],
    "운영대응": ["운영", "복구", "대응", "모니터링", "안정성"],
    "분석": ["분석", "예측", "측정", "진단"],
    "협업소통": ["소통", "협업", "커뮤니케이션", "조율", "조정", "공유", "설명", "문서화"],
    "책임주도": ["책임감", "주도성", "신뢰", "성실성", "오너십"],
    "개선": ["개선", "재발방지", "회고", "최적화"],
    "관리": ["관리", "우선순위", "계획", "통제"],
    "문제해결": ["해결", "문제해결", "돌파"],
    "지원동기": ["동기", "가치", "가치관", "지향"],
}

# ---------------- 축 2: 대상(domain) ----------------
# 열쇠 = 표준 이름, 값 = 수식어에 나타나는 말. 실제 수식어 토큰 174종 중 상위 40개를 덮는다.
DOMAIN = {
    "고객·사업": ["고객", "사업", "영업", "계약", "가격", "제안", "수주", "매출", "거래", "기업"],
    "데이터": ["데이터", "지표", "적재", "파이프라인", "수요예측", "통계", "분포"],
    "모델·AI": ["AI", "모델", "학습", "추론", "성능", "정확도", "프롬프트"],
    "서비스운영": ["장애", "복구", "오류", "응답", "배포", "릴리스", "운영", "지연", "모니터링"],
    "시스템설계": ["API", "연동", "아키텍처", "구조", "인프라", "스키마", "설계"],
    "업무·조직": ["업무", "조직", "협업", "역할", "승인", "결재", "일정", "회의", "방식"],
    "위험·보안": ["위험", "보안", "규정", "준수", "감사", "권한"],
    "요구·범위": ["요구사항", "범위", "변경", "우선순위", "요건"],
    "문서·근거": ["문서", "기록", "정보", "근거", "이력", "보고"],
}

# ---------------- 축 3: 산출물(artifact) ----------------
# doc_type(기업마다 다른 이름)을 **회사 무관한 종류**로 접는다. 새 기업은 이 7개 중 하나로만 매핑하면 된다.
ARTIFACT = {
    "인재상·가치": ["talent"],
    "직무기술": ["jd", "manpower_request"],
    "프로젝트기록": ["project", "poc_standard", "feasibility_checklist"],
    "장애·회고": ["incident", "retrospective", "triage_rule"],
    "업무표준·가이드": ["playbook", "sales_playbook", "sales_standard", "review_report_standard",
                        "release_rule", "handoff_process", "pre_review_guide", "discovery_guide",
                        "contract_guide", "proposal_guide", "decision_guide"],
    "기술자료": ["unit_code", "tech_card", "asset_registry"],
    "사업·관리문서": ["revops", "sales_plan", "bid_process", "bid_decision", "pricing_model", "approval",
                      "approval_authority", "settlement", "revenue", "costing", "foreign_tx", "closing",
                      "budget", "receivable", "notice", "planning"],
}

TAIL = ("과", "와", "및", "의", "·")      # "협업과 소통"처럼 붙어 오는 접속 꼬리


def _rev(table):
    out = {}
    for node, words in table.items():
        for w in words:
            out[w] = node
    return out


ABILITY_OF, DOMAIN_OF, ARTIFACT_OF = _rev(ABILITY), _rev(DOMAIN), _rev(ARTIFACT)


def split_competency(text):
    """'고객 요구 검증' → (['고객','요구'], '검증'). 꼬리 조사는 떼고 본다."""
    toks = [t for t in (text or "").replace("·", " ").split() if t]
    if not toks:
        return [], ""
    head = toks[-1]
    for t in TAIL:
        if head.endswith(t) and len(head) > len(t):
            head = head[: -len(t)]
    return toks[:-1], head


def map_ability(head):
    if head in ABILITY_OF:
        return ABILITY_OF[head]
    for w, node in ABILITY_OF.items():          # '이해도' 처럼 뒤에 붙은 꼴
        if head.startswith(w) or w in head:
            return node
    return None


def map_domain(mods):
    for m in mods:
        if m in DOMAIN_OF:
            return DOMAIN_OF[m]
    for m in mods:
        for w, node in DOMAIN_OF.items():
            if w in m:
                return node
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="결과를 담을 폴더")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    rows = []
    for cat in internal_qcat.CATS:
        for r in internal_store.questions_all(cat):
            m = r["metadata"]
            rows.append({"id": r["id"], "cat": cat, "competency": (m.get("competency") or "").strip(),
                         "doc_type": m.get("doc_type") or "",
                         "title": (m.get("doc_title") or "") + " " + (m.get("sec_title") or ""),
                         "depts": [k[5:] for k in m if k.startswith("dept_") and m[k]]})
    print("질문", len(rows), "개")

    stat = {"n": len(rows), "ability": collections.Counter(), "domain": collections.Counter(),
            "artifact": collections.Counter(), "pair": collections.Counter(),
            "unmapped_head": collections.Counter(), "unmapped_mod": collections.Counter(),
            "unmapped_doc_type": collections.Counter(), "domain_src": collections.Counter()}
    role_edge = collections.defaultdict(collections.Counter)     # dept -> ability
    role_dom = collections.defaultdict(collections.Counter)      # dept -> domain
    mapped_both = 0
    for r in rows:
        mods, head = split_competency(r["competency"])
        ab = map_ability(head)
        dm = map_domain(mods)
        src_dm = "competency" if dm else None
        if not dm:
            # 2순위: 출처 문서·섹션 제목에서 찾는다 — 대상(도메인)은 원래 **문서가 말해 주는 축**이고,
            # `competency`에 수식어가 없는 문항("문제 해결"처럼 행위만 있는 것)이 적지 않다.
            dm = map_domain([t for t in r["title"].replace("·", " ").split() if t])
            src_dm = "title" if dm else None
        stat["domain_src"][src_dm or "(없음)"] += 1
        ar = ARTIFACT_OF.get(r["doc_type"])
        stat["ability"][ab or "(미매핑)"] += 1
        stat["domain"][dm or "(미매핑)"] += 1
        stat["artifact"][ar or "(미매핑)"] += 1
        if ab and dm:
            mapped_both += 1
            stat["pair"][ab + " × " + dm] += 1
        if not ab and head:
            stat["unmapped_head"][head] += 1
        if not dm:
            for m2 in mods:
                stat["unmapped_mod"][m2] += 1
        if not ar and r["doc_type"]:
            stat["unmapped_doc_type"][r["doc_type"]] += 1
        for d in r["depts"]:
            if ab:
                role_edge[d][ab] += 1
            if dm:
                role_dom[d][dm] += 1

    cov = {"ability": round(1 - stat["ability"]["(미매핑)"] / len(rows), 3),
           "domain": round(1 - stat["domain"]["(미매핑)"] / len(rows), 3),
           "artifact": round(1 - stat["artifact"]["(미매핑)"] / len(rows), 3),
           "pair": round(mapped_both / len(rows), 3)}
    print("덮는 비율:", cov)
    print("\n행위 축:", stat["ability"].most_common())
    print("\n대상 축:", stat["domain"].most_common())
    print("\n산출물 축:", stat["artifact"].most_common())
    print("\n대상 축 출처:", stat["domain_src"].most_common())
    print("짝(행위 × 대상) 종류:", len(stat["pair"]), " 상위 12:", stat["pair"].most_common(12))
    print("역할 → 행위 가중치(데이터에서 센 엣지 초기값):")
    for d in sorted(role_edge):
        print(f"  {d:<10} " + ", ".join(f"{k} {v}" for k, v in role_edge[d].most_common(4)))
    print("\n미매핑 머리 명사 상위:", stat["unmapped_head"].most_common(15))
    print("미매핑 수식어 상위:", stat["unmapped_mod"].most_common(15))
    print("미매핑 doc_type:", stat["unmapped_doc_type"].most_common())

    out = {"coverage": cov,
           "axes": {"ability": ABILITY, "domain": DOMAIN, "artifact": ARTIFACT},
           "counts": {k: dict(v) for k, v in stat.items() if isinstance(v, collections.Counter)},
           "role_ability": {d: dict(c) for d, c in role_edge.items()},
           "role_domain": {d: dict(c) for d, c in role_dom.items()}}
    with open(os.path.join(a.out, "ontology_draft.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n저장:", os.path.join(a.out, "ontology_draft.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
