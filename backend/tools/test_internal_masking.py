"""사내문서 공개용 사본 마스킹(A13 2-B, v3 규칙 형식) 단위 검사 (2026-11-13). 모델·Redis·Chroma 없이 호스트에서 돈다.

    cd backend && python -m tools.test_internal_masking

규칙 파일은 시험용 임시 폴더에 만든다(실제 `_masking/mask_rules.json`을 읽지 않음). 마지막 R0은 실제 규칙 파일이
로드·컴파일되는지만 본다(쓰기 없음).
  M1  규칙 파일: 없음·깨짐·대체어에 숫자·표식 불일치·drop_sections 형식·정규식 오류 → MaskRulesError
  M2  R1 고객사 가명: 정렬 순서 · 띄어 쓰기 변형 · 일반 명사(주소)는 가명 대상 아님
  M3  R7 id_map·incident_map · R8 문서 참조(파일 정규식 + 말뭉치 doc_id): 섹션 표기 제거 · 조사 보정 · 단어 경계
  M4  R3 금액(한 덩어리 alternation — '1억5,000만 원'·'1억~5억 원') · 오탐 아님(1원칙·2원화·3원인·L3 조건부)
  M5  R4·R11 키워드 문장의 %·점·배 / R14 보상 문장 / R15 법정 수치 예외 문장은 그대로
  M6  R5 공수·규모 / R6 연락처 삭제 / R9·R18 미래 일정만 [일정]
  M7  R12 표식뿐인 표 행 삭제·머리글만 남은 표 삭제 · R13 가명+상업 표식 문장 삭제 · 문장 분리 결정성
  M8  mask_doc: MSK-NN·public·forbid·related 비움·제목 마스킹·제외 섹션 본문만 비움(순번 유지)·멱등
  M9  drop_sections idx·title 불일치 → 그 사본 안 만듦 · 규칙에 없는 비공개 문서 → 사본 안 만듦(unlisted)
  M10 사본 해시: 규칙 파일·고객사 목록이 바뀌면 바뀌고, 상관없는 문서가 바뀌면 그대로
  M11 R16 표식 밀도: warn = 조각 유지 + 경고 / drop = 그 조각 제외
  M12 적재 통합(사본 68개 규모): sync 증분 0회 · prune 대상 0 · audit 통과 · rag_ready
  M13 audit critical: mask_residual(금액·r12·r13) · mask_dropped_left · public_meta_forbid · mask_source
  M14 규칙 파일 사고: 사본 안 만듦 · prune이 MSK-* 보류 · audit mask_rules_unavailable
  M15 공개 목록에서 일부러 뺀 문서 → prune이 MSK gone 삭제(25% 상한 아래)
  M16 masked_hits · mask_markers_in · NARROW(접두어 → 상세판 ID)
  R0  실제 규칙 파일 로드·컴파일(읽기만)
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tools import test_internal_docs_ingest as T  # noqa: E402  (가짜 Redis·Chroma·embed, langsmith 대체)
from app.agents import internal_docs_agent as A, internal_forbid as F, internal_masking as M  # noqa: E402
from app import internal_store as S  # noqa: E402

check, quiet, FAILS = T.check, T.quiet, T.FAILS
FOLLOW = r"(?=[\s,.)·|~/:;\]\-]|$|이|을|은|의|에|으로|과|와|도|만|씩|대|정도|이하|이상|미만|초과|내외)"
RULES = {
    "rules_version": "test-v3", "as_of": "2026-11-13",
    "publish": {"PRJ-CC-DET": {"drop_sections": [{"idx": 1, "title": "대한전자 및 계약 정보"}]}, "DSC-01": {}},
    "placeholders": ["[금액]", "[비율]", "[점수]", "[공수]", "[규모]", "[일정]", "[고객 정보]"],
    "alias": {"customer_prefix": "고객사", "vendor_prefix": "업체", "scope": "customer"},
    "regex": {
        "R3_money": [r"\d[\d,.]*\s?(조|억|천만|백만|만)?\s?원" + FOLLOW,
                     r"\d[\d,.]*\s?억(\s?\d[\d,]*\s?(천만|만))?\s?원?" + FOLLOW,
                     r"\$\s?\d[\d,.]*", r"\d[\d,.]*\s?(달러|USD)"],
        "R4_number": [r"\d+(\.\d+)?\s?%", r"\d+\s?점", r"\d+(\.\d+)?\s?배"],
        "R4_keywords": ["단가", "할인", "가중치", "배점", "연봉", "성과급"],
        "R5_mm": r"\d+(\.\d+)?\s?M/M", "R5_scale": [r"상담사\s?\d[\d,]*\s?명", r"매장\s?\d[\d,]*\s?개"],
        "R6_delete": [r"0\d{1,2}-\d{3,4}-\d{4}", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"],
        "R8_docref": r"(?<![A-Za-z0-9-])[A-Z]{2,5}-\d{2}(?![A-Za-z0-9-])((#s\d+)|(\s?s\d+))?((·|, ?)s\d+)*(\s?참조)?",
        "R9_date": [r"20\d\d년(\s?\d{1,2}월)?(\s?\d{1,2}일)?", r"20\d\d년\s?(상|하)반기", r"20\d\d년\s?\d분기"],
        "R15_exempt_keywords": ["세율", "부가가치세", "법정"],
    },
    "id_map": {"PRJ-CC-DET": "콜센터 AI 상담 프로젝트", "ISS-01": "데이터 연동 안정화 과제"},
    "incident_map": {"INC-AI-01": "콜센터 AI 응답 오류 장애", "INC-AI-02": "출처 표시 불일치 장애"},
    "R8_replacement": "관련 사내 기준",
    "order": list(M.ORDER),
    "drop_sentences": [{"id": "R13", "pattern": r"고객사[A-Z][^.\n]*\[(금액|비율|공수|규모)\]|\[(금액|비율|공수|규모)\][^.\n]*고객사[A-Z]"}],
    "table_row_drop": True,
    "density_guard": {"max_marked_sentence_ratio": 0.5, "mode": "warn"},
}


def doc(doc_id, disclosure, sections, title=None, status="active"):
    """섹션 = (본문, forbid[, 제목])."""
    lines = [f"# {title or doc_id}",
             f"<!--meta: doc={doc_id}; doc_type=project; tier=1; topic=T; disclosure={disclosure}; status={status}-->", ""]
    for n, sec in enumerate(sections, 1):
        lines += [f"## {sec[2] if len(sec) > 2 else f'섹션{n}'}",
                  f"<!--meta: sec=s{n}; dept=AIAX|BE; level=주니어; ct_band=중; related=TAL-01#s1-->"]
        if sec[1]:
            lines.append(f"<!--forbid: {sec[1]}-->")
        lines += [sec[0], ""]
    return "\n".join(lines) + "\n"


def write_rules(env, rules):
    os.makedirs(os.path.join(env.dir, "_masking"), exist_ok=True)
    with open(os.path.join(env.dir, "_masking", "mask_rules.json"), "wb") as f:
        f.write(json.dumps(rules, ensure_ascii=False, indent=1).encode("utf-8"))


def setup(env, rules=RULES):
    write_rules(env, rules)
    env.put("08_det.md", doc("PRJ-CC-DET", "internal", [
        ("대한전자 계약 금액 3억 원, 담당 연락처 02-123-4567.", "대한전자|PRJ-CC", "대한전자 및 계약 정보"),
        ("대한 전자 콜센터 상담사 120명 대상. 검색 정확도는 92%였다. PRJ-CC-DET#s3를 참고한다.", "대한전자|INC"),
        ("2026년 12월 확장 예정. 2026년 9월 첫 도입. DSC-01로 넘긴다. INC-AI-01을 보면 원인이 나온다.", ""),
    ], title="콜센터 AI 상담 — 대한전자 상세판"))
    env.put("51_dsc.md", doc("DSC-01", "internal", [("초기 미팅은 DSC-01를 따른다. 할인은 15%까지. 12.5 M/M.", "")]))
    env.put("07_pub.md", doc("PRJ-CC-PUB", "public", [("공개판 본문 사건 요약.", "PRJ-CC")]))
    env.put("05_tal.md", doc("TAL-01", "public", [("고객 사례 없음. API 주소 같은 일반 문장.", "한빛유통|주소")]))


def comp_for(rules=RULES, cust=("대한전자", "한빛유통"), ids=("DSC-01", "TAL-01", "CODE-RAG-01")):
    return M.Compiled(rules, M.aliases(sorted(cust)), list(ids))


def t_rules():
    print("M1 규칙 파일")
    env = T.Env()
    try:
        path = os.path.join(env.dir, "_masking", "mask_rules.json")
        cases = (("없음", None, None), ("깨진 JSON", None, b"{bad"),
                 ("대체어에 숫자", {**RULES, "R8_replacement": "기준 2"}, None),
                 ("incident_map 값에 숫자", {**RULES, "incident_map": {"INC-AI-01": "장애 1"}}, None),
                 ("표식 불일치", {**RULES, "placeholders": RULES["placeholders"] + ["[돈]"]}, None),
                 ("drop_sections 형식", {**RULES, "publish": {"DSC-01": {"drop_sections": ["제목만"]}}}, None),
                 ("정규식 오류", {**RULES, "regex": {**RULES["regex"], "R5_mm": "("}}, None),
                 ("모르는 order 단계", {**RULES, "order": ["R99"]}, None),
                 ("as_of 형식", {**RULES, "as_of": "11/13"}, None))
        for name, rules, raw in cases:
            if os.path.exists(path):
                os.remove(path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if raw is not None:
                open(path, "wb").write(raw)
            elif rules is not None:
                write_rules(env, rules)
            try:
                M.load_rules(env.dir); ok = False
            except M.MaskRulesError:
                ok = True
            check(f"M1 {name} → MaskRulesError", ok)
        write_rules(env, RULES)
        r, h = M.load_rules(env.dir)
        check("M1 정상 로드 + 해시", r["rules_version"] == "test-v3" and len(h) == 64)
    finally:
        env.close()


def t_text():
    print("M2~M7 규칙별 변환")
    c = comp_for()
    t = lambda s: c.text(s, {})  # noqa: E731
    check("M2 가명 순서 = 정렬", t("대한전자와 한빛유통") == "고객사A와 고객사B", t("대한전자와 한빛유통"))
    check("M2 띄어 쓰기 변형", t("대한 전자 콜센터") == "고객사A 콜센터")
    env = T.Env()
    try:
        setup(env)
        docs, _ = A.load_docs(mask=False)
        check("M2 가명 대상 = 고유명사만(주소 제외)", M.customer_terms(docs) == ["대한전자", "한빛유통"], M.customer_terms(docs))
    finally:
        env.close()
    for src, want in (("PRJ-CC-DET#s3를 참고", "콜센터 AI 상담 프로젝트를 참고"),
                      ("INC-AI-01을 보면", "콜센터 AI 응답 오류 장애를 보면"),
                      ("유사 장애 사례 INC-AI-02", "유사 장애 사례 출처 표시 불일치 장애"),
                      ("DSC-01를 따른다", "관련 사내 기준을 따른다"),
                      ("DSC-01로 넘긴다", "관련 사내 기준으로 넘긴다"),
                      ("PRP-03#s5 참조", "관련 사내 기준"),
                      ("CODE-RAG-01 모듈", "관련 사내 기준 모듈"),
                      ("xDSC-01 DSC-012", "xDSC-01 DSC-012")):
        check(f"M3 '{src}'", t(src) == want, t(src))
    for src in ("3억 5천만 원", "5,000원을", "$300", "100 USD", "1억5,000만 원", "1억~5억 원", "1,200만 원 이하"):
        out = t(src)
        check(f"M4 금액 '{src}'", "[금액]" in out and not any(ch.isdigit() for ch in out), out)
    for src in ("1원칙", "2원화 결제", "3원인 분석", "L3 조건부"):
        check(f"M4 오탐 아님 '{src}'", t(src) == src, t(src))
    check("M5 키워드 문장의 % → [비율]", t("할인은 15%까지 허용한다.") == "할인은 [비율]까지 허용한다.")
    check("M5 배 → [비율]", t("단가는 1.5배로 올린다.") == "단가는 [비율]로 올린다.", t("단가는 1.5배로 올린다."))
    check("M5 기술 지표 %는 그대로", t("검색 정확도는 92%였다.") == "검색 정확도는 92%였다.")
    check("M5 배점 문장의 점", t("배점이 25점으로 정해진다.") == "배점이 [점수]으로 정해진다.", t("배점이 25점으로 정해진다."))
    check("M5 R14 보상 문장의 '만' 수치", t("연봉은 4,200만으로 정한다.") == "연봉은 [금액]으로 정한다.", t("연봉은 4,200만으로 정한다."))
    check("M5 R15 법정 수치 예외(부가가치세 10%·금액 그대로)", t("부가가치세는 공급가액의 10%로 1,000원이다.") == "부가가치세는 공급가액의 10%로 1,000원이다.")
    check("M6 공수", t("12.5 M/M 투입") == "[공수] 투입")
    check("M6 규모", t("상담사 120명 대상") == "상담사 [규모] 대상", t("상담사 120명 대상"))
    check("M6 기술 수치(평가셋 300건)는 그대로", t("평가셋 300건") == "평가셋 300건")
    check("M6 연락처 삭제", "02-123-4567" not in t("연락처 02-123-4567") and "@" not in t("메일 a.b@x.com"))
    check("M6 미래 일정만", t("2026년 12월 확장, 2026년 9월 도입") == "[일정] 확장, 2026년 9월 도입", t("2026년 12월 확장, 2026년 9월 도입"))
    check("M6 반기·분기·ISO", t("2026년 하반기 · 2027년 상반기 · 2027년 1분기 · 2027-03-01") == "2026년 하반기 · [일정] · [일정] · [일정]",
          t("2026년 하반기 · 2027년 상반기 · 2027년 1분기 · 2027-03-01"))
    tbl = "| 구분 | 금액 | 비고 |\n| --- | --- | --- |\n| 전결 | 5,000만 원 | 1억 원 |\n| 설명 | 팀장 승인 | 문서 |"
    check("M7 R12 표식뿐인 행 삭제", t(tbl) == "| 구분 | 금액 | 비고 |\n| --- | --- | --- |\n| 설명 | 팀장 승인 | 문서 |", t(tbl))
    tbl2 = "앞 문장.\n| 구분 | 금액 |\n| --- | --- |\n| 전결 | 3억 원 |\n뒤 문장."
    check("M7 R12 머리글만 남은 표 삭제", t(tbl2) == "앞 문장.\n뒤 문장.", t(tbl2))
    check("M7 R13 가명+상업 표식 문장 삭제", t("대한전자 계약은 3억 원이다. 다음 문장.") == "다음 문장.", t("대한전자 계약은 3억 원이다. 다음 문장."))
    check("M7 R13 가명만 있는 문장은 유지", t("대한전자 콜센터를 구축했다.") == "고객사A 콜센터를 구축했다.")
    s = "첫 문장. 둘째 문장!\n| a | b |\n셋째 다. 넷째"
    check("M7 문장 분리 결정성(이어 붙이면 원문)", "".join(a + b for a, b in M.sentences(s)) == s
          and [x for x, _ in M.sentences(s)] == ["첫 문장.", "둘째 문장!", "| a | b |", "셋째 다.", "넷째"])


def t_mask_doc():
    print("M8~M11 mask_doc · 해시 · 밀도")
    env = T.Env()
    try:
        setup(env)
        env.put("09_new.md", doc("NEW-01", "internal", [("규칙 파일에 없는 비공개 문서", "")]))
        docs, _ = A.load_docs()
        mk = {d["doc_id"]: d for d in docs if d.get("masked")}
        check("M8 사본 = publish 두 개(대상 목록은 규칙 파일에서만)", sorted(mk) == ["MSK-08", "MSK-51"], sorted(mk))
        check("M9 규칙에 없는 비공개 문서 → 사본 안 만듦 + unlisted", A.MASK_STATE["unlisted"] == ["NEW-01"], A.MASK_STATE["unlisted"])
        m8 = mk["MSK-08"]
        src = next(d for d in docs if d["doc_id"] == "PRJ-CC-DET")
        check("M8 등급 public · 원본은 그대로", m8["disclosure"] == "public" and src["disclosure"] == "internal")
        check("M8 섹션 수 유지 · 제외 섹션 본문만 비움", len(m8["sections"]) == len(src["sections"]) and m8["sections"][0]["body"] == [])
        check("M8 forbid·related 비움", all(not s["forbid"] and "related" not in s["meta"] for s in m8["sections"]))
        check("M8 제목 마스킹", "대한전자" not in m8["doc_title"] and "고객사A" in m8["doc_title"], m8["doc_title"])
        body = "\n".join(sum((s["body"] for s in m8["sections"]), []))
        check("M8 본문에 원문 없음", not any(x in body for x in ("대한", "PRJ-CC", "3억", "02-123", "DSC-01", "INC-AI")), body)
        c = A.MASK_STATE["compiled"]
        check("M8 멱등", c.text(body, {}) == body)
        check("M8 조각 메타에 원본 doc_id 없음", all("PRJ-CC" not in str(list(r["meta"].values())) for r in A.build_chunks(m8)))

        bad = copy.deepcopy(RULES)
        bad["publish"]["PRJ-CC-DET"]["drop_sections"] = [{"idx": 2, "title": "대한전자 및 계약 정보"}]
        write_rules(env, bad)
        docs2, _ = A.load_docs()
        check("M9 idx·title 불일치 → 사본 안 만듦", "MSK-08" not in {d["doc_id"] for d in docs2}
              and A.MASK_STATE["drop_mismatch"] == ["PRJ-CC-DET"], A.MASK_STATE.get("drop_mismatch"))

        h0 = m8["doc_hash"]
        write_rules(env, {**RULES, "rules_version": "test-v3b"})
        docs3, _ = A.load_docs()
        check("M10 규칙 파일이 바뀌면 사본 해시 바뀜", next(d for d in docs3 if d["doc_id"] == "MSK-08")["doc_hash"] != h0)
        write_rules(env, RULES)
        env.put("99_x.md", doc("X-99", "internal", [("다른 문서", "새고객")]))
        docs4, _ = A.load_docs()
        check("M10 고객사 목록이 바뀌면 사본 해시 바뀜", next(d for d in docs4 if d["doc_id"] == "MSK-08")["doc_hash"] != h0)
        env.rm("99_x.md")
        env.put("98_y.md", doc("Y-98", "internal", [("상관없는 문서", "")]))
        docs5, _ = A.load_docs()
        check("M10 상관없는 문서가 바뀌면 그대로", next(d for d in docs5 if d["doc_id"] == "MSK-08")["doc_hash"] == h0)
        env.rm("98_y.md"); env.rm("09_new.md")

        dense = "단가 1.5배. 할인 15%. 단가 20%. 일반 문장."
        env.put("51_dsc.md", doc("DSC-01", "internal", [(dense, "")]))
        docs6, _ = A.load_docs()
        d6 = next(d for d in docs6 if d["doc_id"] == "MSK-51")
        check("M11 R16 warn: 조각 유지 + 경고", len(A.build_chunks(d6)) == 1 and d6["density_flags"] == ["000"], d6.get("density_flags"))
        write_rules(env, {**RULES, "density_guard": {"max_marked_sentence_ratio": 0.5, "mode": "drop"}})
        docs7, _ = A.load_docs()
        d7 = next(d for d in docs7 if d["doc_id"] == "MSK-51")
        check("M11 R16 drop: 그 조각 제외", A.build_chunks(d7) == [])
        env.put("51_dsc.md", doc("DSC-01", "internal", [("단가 15%. 일반 문장.", "")]))
        docs8, _ = A.load_docs()
        d8 = next(d for d in docs8 if d["doc_id"] == "MSK-51")
        check("M11 R16 비율 0.5와 같으면 통과", len(A.build_chunks(d8)) == 1)
    finally:
        env.close()


def big_setup(env, n=68):
    """사본 68개 규모: 비공개 n개 + 공개 2개."""
    pub = {}
    for i in range(n):
        num = f"{i + 10:02d}"
        did = f"D{i:02d}-01"
        env.put(f"{num}_d.md", doc(did, "internal" if i % 3 else "confidential",
                                   [(f"문서 {did} 본문. 단가 {i}% 적용.", "대한전자" if i == 1 else ""), ("두 번째 섹션 일반 문장.", "")]))
        pub[did] = {}
    env.put("01_p.md", doc("PUB-01", "public", [("공개 본문.", "")]))
    env.put("02_p.md", doc("PUB-02", "public", [("공개 본문 둘.", "")]))
    write_rules(env, {**RULES, "publish": pub})
    return pub


def t_ingest_audit():
    print("M12~M15 적재 · audit · prune (사본 68개 규모)")
    env = T.Env()
    try:
        pub = big_setup(env)
        rep, _ = quiet(A.run, "ingest")
        st = env.store
        msk = {m["doc_id"] for _, m in st.c[S.PUBLIC].values() if m["doc_id"].startswith("MSK-")}
        check("M12 사본 68개 public", len(msk) == 68, len(msk))
        check("M12 원본 68개는 restricted 그대로", len({m["doc_id"] for _, m in st.c[S.RESTRICTED].values()}) == 68)
        check("M12 audit 통과 · rag_ready", rep["ok"] and F.rag_ready()[0], rep["critical"])
        T.EMBED["calls"] = 0
        quiet(A.run, "sync")
        check("M12 두 번째 sync 임베딩 0회", T.EMBED["calls"] == 0, T.EMBED["calls"])
        docs, sk = A.load_docs()
        plan, hold = A.plan_prune(docs, sk)
        check("M12 prune 대상 0 · 보류 0", plan == [] and hold == [], (plan, hold))

        k = sorted(st.ids(S.PUBLIC, "MSK-10"))[0]
        orig = st.c[S.PUBLIC][k]
        for name, txt, upd in (("mask_residual", orig[0] + " 3억 원", {}),
                               ("mask_residual", orig[0] + "\n| 항목 | [금액] |", {}),
                               ("mask_residual", orig[0] + " 고객사A 계약은 [금액]이다.", {}),
                               ("mask_dropped_left", orig[0], {"sec_title": "x"}),
                               ("public_meta_forbid", orig[0], {"doc_title": "대한전자"}),
                               ("mask_source", orig[0], {"source_file": "x/99_other.md"})):
            st.c[S.PUBLIC][k] = (txt, {**orig[1], **upd})
            if name == "mask_dropped_left":
                next(d for d in docs if d["doc_id"] == "MSK-10")["dropped_titles"] = ["x"]
            if name == "public_meta_forbid":
                env.r.h[F.KEY]["대한전자"] = "|X|"
            rep, _ = quiet(A.audit, docs, sk, record=False)
            check(f"M13 {name} critical", name in rep["critical"], rep["critical"])
            st.c[S.PUBLIC][k] = orig
        docs, sk = A.load_docs()
        quiet(A.build_forbid, docs)

        path = os.path.join(env.dir, "_masking", "mask_rules.json")
        saved = open(path, "rb").read()
        open(path, "wb").write(b"{broken")
        docs, sk = A.load_docs()
        n0 = len(st.ids(S.PUBLIC))
        res, _ = quiet(A.prune, docs, sk, force=True)
        check("M14 규칙 파일 사고 → 사본 안 만듦 · prune 보류(안 지움)", not any(d.get("masked") for d in docs)
              and len(st.ids(S.PUBLIC)) == n0 and len(res["hold"]) == 68, (len(res["hold"]), res.get("refused")))
        rep, _ = quiet(A.audit, docs, sk, record=False)
        check("M14 audit mask_rules_unavailable", "mask_rules_unavailable" in rep["critical"])
        open(path, "wb").write(saved)

        cut = dict(list(pub.items())[:-5])
        write_rules(env, {**RULES, "publish": cut})
        docs, sk = A.load_docs()
        res, _ = quiet(A.prune, docs, sk)
        gone = sorted(p[1] for p in res["deleted"])
        check("M15 공개 목록에서 뺀 5개 → MSK gone 삭제", len(gone) == 5 and all(g.startswith("MSK-") for g in gone)
              and res["refused"] is None, res.get("refused"))
        check("M15 뺀 문서는 unlisted 경고", len(A.MASK_STATE["unlisted"]) == 5)
    finally:
        env.close()


def t_markers():
    print("M16 표식 · 접두어")
    check("M16 masked_hits", F.masked_hits("고객사A의 [금액] 견적과 업체B, MSK-08") == ["[금액]", "고객사A", "업체B", "MSK-08"])
    check("M16 masked_hits 일반 문장", F.masked_hits("고객사의 요구와 금액 산정") == [])
    check("M16 mask_markers_in", F.mask_markers_in("규모는 [규모]이고 고객사A와") == ["[규모]", "alias"])
    m = F.collect([{"doc_id": "D", "sections": [{"forbid": ["INC", "PRJ-CC", "대한전자"]}]}])
    check("M16 NARROW", set(m) == {"INC-AI-DET", "INC-SV-DET", "PRJ-CC-DET", "대한전자"}, m)
    check("M16 공개 사건 번호는 사전에 안 걸림", F.find_terms("INC-AI-01 요약", sorted(m)) == [])


def t_real_rules():
    print("R0 실제 규칙 파일(읽기만)")
    try:
        r, h = M.load_rules(A.DOCS_DIR)
        check("R0 로드·컴파일", r["rules_version"].startswith("v3") and len(r["publish"]) == 68, (r["rules_version"], len(r["publish"])))
    except M.MaskRulesError as e:
        check("R0 로드·컴파일", False, str(e))


def main():
    real_dir = A.DOCS_DIR
    t_rules()
    t_text()
    t_mask_doc()
    t_ingest_audit()
    t_markers()
    A.DOCS_DIR = real_dir
    t_real_rules()
    print(f"\n{'모두 통과' if not FAILS else f'실패 {len(FAILS)}건: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
