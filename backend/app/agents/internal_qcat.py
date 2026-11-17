"""사내문서 조각·질문의 **질문 카테고리 축** `q_cat` — ADLC v0.8 6.19.3 (2026-11-17 사용자 지시).

사내문서(Chroma)가 기본 질문 8개의 **주 출처**가 되려면 조각·질문을 면접 질문 카테고리
(`personality` 인성 · `job` 직무 이해도 · `company` 기업 이해)로 가를 수 있어야 한다.
Redis 질문 은행은 `category` 태그로 그 일을 하는데(`vectorstore.py`), Chroma 조각에는 그 칸이 없었다.

**메타데이터를 Chroma `where` 조건으로 쓰지 않는다** (2026-11-17 사용자 지시 · 6.17.4 QO-02·QO-14 실측):
`where`를 걸면 870 ms, 걸지 않으면 19 ms인데 그 조건이 실제로 거르는 조각은 0개였다. 그래서 `q_cat`은
① **검색 뒤 앱에서 거르고**(`internal_search`) ② 재순위 가산점으로만 쓰며 ③ 질문은 카테고리별 컬렉션으로
물리 분리한다(`internal_store.QUESTION_BY_CAT`). Chroma 질의에는 `q_cat`을 넣지 않는다.

값은 **이미 저장된 `doc_type`에서 계산한다** — 그래서 148개 문서를 다시 임베딩하지 않아도 오늘부터 동작한다.
적재 에이전트(A13)는 앞으로 `q_cat` 메타를 함께 써 두고(`build_chunks`), 섹션 meta에 `qcat=`가 적혀 있으면
그 값이 이긴다(META_SPEC v0.4 후보). 둘 다 없으면 `doc_type` 매핑, 매핑에도 없으면 `company`.

매핑 기준: 2026-11-17 `internal_docs_public` 2,235조각의 `doc_type` 47종 전부를 배정했다(미배정 0종).
조각 수 = personality 553(17문서) · job 846(30문서) · company 953(37문서) — `project`만 job·company 양쪽.
"""

CATS = ("personality", "job", "company")
DEFAULT_CAT = "company"

# doc_type → 질문 카테고리(복수 가능). 문서 종류를 늘리면 여기에 한 줄 추가한다.
TYPE_MAP = {
    # ── personality: 인재상 · 행동지표 · 평가 기준 · 조직 생활 규범
    "talent": ("personality",),
    "hr": ("personality",),
    "evaluation": ("personality",),
    "assessment_standard": ("personality",),
    "casebook": ("personality",),
    "attendance": ("personality",),
    "meeting_rule": ("personality",),
    "hiring_guide": ("personality",),
    # ── job: 직무기술서 · 기술 카드 · 프로젝트 · 장애 · 개발 규범
    "jd": ("job",),
    "playbook": ("job",),
    "incident": ("job",),
    "unit_code": ("job",),
    "tech_card": ("job",),
    "handoff_process": ("job",),
    "open_challenge": ("job",),
    "triage_rule": ("job",),
    "retrospective": ("job",),
    "release_rule": ("job",),
    "review_report_standard": ("job",),
    "poc_standard": ("job",),
    "pre_review_guide": ("job",),
    "feasibility_checklist": ("job",),
    "project": ("job", "company"),          # 프로젝트 기록은 직무·사업 양쪽 질문의 재료
    # ── company: 사업 · 영업 · 계약 · 재무 · 결재
    "revops": ("company",),
    "approval": ("company",),
    "approval_authority": ("company",),
    "sales_standard": ("company",),
    "sales_playbook": ("company",),
    "sales_plan": ("company",),
    "contract_guide": ("company",),
    "proposal_guide": ("company",),
    "bid_process": ("company",),
    "bid_decision": ("company",),
    "discovery_guide": ("company",),
    "decision_guide": ("company",),
    "manpower_request": ("company",),
    "notice": ("company",),
    "pricing_model": ("company",),
    "foreign_tx": ("company",),
    "asset_registry": ("company",),
    "receivable": ("company",),
    "budget": ("company",),
    "costing": ("company",),
    "planning": ("company",),
    "revenue": ("company",),
    "closing": ("company",),
    "settlement": ("company",),
}


def pipe(values):
    """목록 → `|a|b|` (Chroma 메타데이터 값은 스칼라만 되므로. `internal_docs_agent._pipe`와 같은 형식)."""
    vs = [v for v in values if v]
    return "|" + "|".join(vs) + "|" if vs else ""


def unpipe(s):
    return [v for v in (s or "").split("|") if v]


def from_doc_type(doc_type):
    return list(TYPE_MAP.get((doc_type or "").strip(), (DEFAULT_CAT,)))


def from_meta_value(value):
    """섹션 meta `qcat=personality|job` 값 → 카테고리 목록(아는 값만)."""
    vals = [v.strip() for v in (value or "").replace("|", ",").split(",")]
    return [v for v in vals if v in CATS]


def qcat_of(meta):
    """조각 메타데이터 1개 → 질문 카테고리 목록. 저장된 `q_cat`이 있으면 그것, 없으면 `doc_type` 매핑."""
    stored = [v for v in unpipe((meta or {}).get("q_cat")) if v in CATS]
    if stored:
        return stored
    return from_doc_type((meta or {}).get("doc_type"))


def matches(meta, q_cat):
    """그 조각이 이 카테고리에 쓸 수 있나. `q_cat`이 None이면 항상 True(기존 동작)."""
    if not q_cat:
        return True
    return q_cat in qcat_of(meta)
