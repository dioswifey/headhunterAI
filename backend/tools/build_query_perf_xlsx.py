"""쿼리 성능 · Multi-Query+RRF 전후 비교 · 메타데이터 필터 분석 엑셀 만들기 (2026-11-16, 호스트에서 실행, openpyxl).

    python backend/tools/build_query_perf_xlsx.py --runs documents/03_QA_doc/eval_runs \
        --out documents/03_QA_doc/headhunterAI_쿼리성능테스트_2026-11-16.xlsx

입력(eval_runs/): query_perf_2026-11-16.json · mq_rrf_retrieval_2026-11-16.json · mq_rrf_fallback_2026-11-16.json ·
                  mq_rrf_pipeline_2026-11-16.json · meta_filter_2026-11-16.json ·
                  dual_rag_pipeline_2026-11-16.json · dual_rag_tests_2026-11-16.json (없는 파일은 '미측정'으로 적는다)
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eval_query_perf as EQ  # noqa: E402  (기존 시트 5개를 그대로 만든다)


def load(runs, name):
    p = os.path.join(runs, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def diff(a, b):
    if a is None or b is None:
        return None, None
    d = round(b - a, 4)
    return d, (round(d / a * 100, 1) if a else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import openpyxl
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    qp = os.path.join(a.runs, "query_perf_2026-11-16.json")
    EQ.excel(argparse.Namespace(json=qp, out=a.out))           # 개요 · 질의 세트 · 결과 요약 · 질의별 상세 · 조건 비교
    wb = openpyxl.load_workbook(a.out)
    head, hf = PatternFill("solid", fgColor="111111"), Font(bold=True, color="FFFFFF")
    note_f = Font(italic=True, color="555555")

    def sheet(title, header, rows, widths):
        ws = wb.create_sheet(title)
        ws.append(header)
        for c in ws[1]:
            c.fill, c.font = head, hf
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for r in rows:
            ws.append([json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in r])
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"
        return ws

    R = load(a.runs, "mq_rrf_retrieval_2026-11-16.json")
    F = load(a.runs, "mq_rrf_fallback_2026-11-16.json")
    P = load(a.runs, "mq_rrf_pipeline_2026-11-16.json")
    M = load(a.runs, "meta_filter_2026-11-16.json")

    # ---- A vs B 품질 ----
    rows, chart_rows = [], []
    if R:
        for key, s in R["summary"].items():
            variants = [v for v in ("A", "B5", "B16", "B") if v in s]
            base = s["A"]
            for v in variants:
                for m in ("recall@5", "mrr@5", "ndcg@5"):
                    d, pc = diff(base[m], s[v][m]) if v != "A" else (None, None)
                    rows.append((key, {"A": "A 단일 쿼리(기준선)", "B5": "B Multi-Query+RRF(쿼리당 Top-5)",
                                       "B16": "B Multi-Query+RRF(쿼리당 Top-16, 운영 설정)", "B": "B Multi-Query+RRF"}[v],
                                 m, s[v][m], d, pc, s["queries"], s["queries_with_relevant"], s["queries_no_relevant"], s["pool_mean"], "실측"))
            bv = "B16" if "B16" in s else "B"
            chart_rows.append((key, base["ndcg@5"], s[bv]["ndcg@5"], base["recall@5"], s[bv]["recall@5"]))
    else:
        rows.append(("-", "미측정", None, None, None, None, None, None, None, None, "미측정"))
    ws = sheet("AB_검색품질", ["대상", "방식", "지표(@5)", "값", "B−A 절대 차이", "B−A 개선율(%)", "질의 수", "관련 항목 있는 질의",
                              "관련 항목 없는 질의(평균 제외)", "후보 풀 평균 크기", "구분"], rows, [22, 40, 10, 9, 12, 12, 8, 12, 14, 12, 8])
    if chart_rows:
        c0 = ws.max_row + 3
        ws.cell(c0, 1, "차트 데이터").font = Font(bold=True)
        hdr = ["대상", "nDCG@5 A", "nDCG@5 B", "Recall@5 A", "Recall@5 B"]
        for j, h in enumerate(hdr, 1):
            ws.cell(c0 + 1, j, h)
        for i, r in enumerate(chart_rows):
            for j, v in enumerate(r, 1):
                ws.cell(c0 + 2 + i, j, v)
        ch = BarChart()
        ch.title, ch.y_axis.title = "A 단일 쿼리 vs B Multi-Query+RRF (@5)", "점수"
        data = Reference(ws, min_col=2, max_col=5, min_row=c0 + 1, max_row=c0 + 1 + len(chart_rows))
        cats = Reference(ws, min_col=1, min_row=c0 + 2, max_row=c0 + 1 + len(chart_rows))
        ch.add_data(data, titles_from_data=True)
        ch.set_categories(cats)
        ch.height, ch.width = 9, 20
        ws.add_chart(ch, f"H{c0}")

    # ---- 지연·호출 ----
    rows = []
    if R:
        L = R["latency"]
        names = {"embed_A": "임베딩 A(쿼리 1개, 요청 1회)", "embed_B": "임베딩 B(쿼리 3개 묶음, 요청 1회)",
                 "redis_A": "Redis KNN A(카테고리당 1회)", "redis_B16": "Redis B(카테고리당 3쿼리 병렬 + RRF, Top-16)",
                 "redis_B5": "Redis B(Top-5)", "chroma_A": "사내문서 A internal_search.search",
                 "chroma_B": "사내문서 B search_multi(3쿼리 병렬 + RRF)", "stage_A": "검색 단계 합계 A(임베딩+Redis 3카테고리+사내문서)",
                 "stage_B": "검색 단계 합계 B"}
        for k, lbl in names.items():
            x = L.get(k) or {}
            rows.append((lbl, x.get("n"), x.get("mean_ms"), x.get("p50_ms"), x.get("p95_ms"), "실측"))
        sa, sb = L["stage_A"], L["stage_B"]
        rows.append(("검색 단계 B−A (평균)", None, round(sb["mean_ms"] - sa["mean_ms"], 2), round(sb["p50_ms"] - sa["p50_ms"], 2),
                     round(sb["p95_ms"] - sa["p95_ms"], 2), "실측 차이"))
    if P:
        for m in ("A", "B"):
            s = P["summary"][m]
            rows.append((f"전체 질문 생성 파이프라인 {m} (그래프 1-A generate_set, {s['runs']}회)", s["runs"], s["seconds"]["mean_ms"],
                         s["seconds"]["p50_ms"], s["seconds"]["p95_ms"], "실측"))
    else:
        rows.append(("전체 질문 생성 파이프라인", None, None, None, None, "미측정"))
    sheet("AB_지연", ["구간", "n", "평균 ms", "P50 ms", "P95 ms", "구분"], rows, [56, 6, 12, 12, 12, 10])

    rows = []
    if R:
        cs = R["counts"]
        import statistics as st
        rows.append(("쿼리 수 / 응시", 1, round(st.mean([c["queries"] for c in cs]), 2), "B는 규칙 생성(LLM 0회)"))
        rows.append(("임베딩 HTTP 요청 / 응시(검색 쿼리용)", 1, 1, "B는 3개를 한 요청으로 묶음(llm.embed 배치)"))
        rows.append(("임베딩 입력 문장 수 / 응시(검색 쿼리용)", 1, round(st.mean([c["embed_inputs_B"] for c in cs]), 2), "이력서 참고 문항 임베딩(최대 3회)은 A·B 같음"))
        for cat in ("personality", "job", "company"):
            vals = [c.get(f"redis_{cat}") for c in cs if isinstance(c.get(f"redis_{cat}"), dict)]
            if vals:
                rows.append((f"Redis {cat}: 쿼리당 결과 수 합(중복 제거 전)", 16, round(st.mean([v["sum_before_dedupe"] for v in vals]), 2), "A는 쿼리 1개 × k16"))
                rows.append((f"Redis {cat}: 중복 제거 후 고유 문항(Top-16 RRF)", 16, round(st.mean([v["fused_unique_k16"] for v in vals]), 2), "RRF limit=k"))
            else:
                rows.append((f"Redis {cat}", None, None, "은행에 해당 문항 없음(backend·data_sci 직무 문항 0)"))
        rows.append(("사내문서: 쿼리당 결과 수 합(중복 제거 전, 하한·금지 용어 통과분)", round(st.mean([c["chroma"]["A_ranked"] for c in cs]), 2),
                     round(st.mean([c["chroma"]["B_sum_before_dedupe"] for c in cs]), 2), "k=12, MIN_SIM 0.55"))
        rows.append(("사내문서: 중복 제거 후 고유 조각", round(st.mean([c["chroma"]["A_ranked"] for c in cs]), 2),
                     round(st.mean([c["chroma"]["B_fused_unique"] for c in cs]), 2), ""))
        rows.append(("사내문서: 최종 근거 조각 수(TOP_N 4)", R["summary"]["chroma/internal"]["final_refs_mean"]["A"],
                     R["summary"]["chroma/internal"]["final_refs_mean"]["B"],
                     f"A·B 최종 조각이 같은 응시 {R['summary']['chroma/internal']['same_final_refs']}/{R['summary']['chroma/internal']['queries']}"))
    if P:
        for k, lbl in (("chat_calls_mean", "생성 모델(chat_json) 호출 / 응시"), ("embed_requests_mean", "임베딩 요청 / 응시(전체 파이프라인)"),
                       ("embed_inputs_mean", "임베딩 입력 문장 / 응시(전체 파이프라인)")):
            rows.append((lbl, P["summary"]["A"][k], P["summary"]["B"][k], "실측(호출 수 계측)"))
    rows.append(("LLM 토큰·비용", "미측정", "미측정", "llm.chat_json이 토큰 수를 돌려주지 않아 미측정. B의 쿼리 생성은 규칙이라 생성 모델 호출이 늘지 않음(위 호출 수로 확인). 로컬 모델이라 API 비용 0"))
    sheet("AB_호출수", ["항목", "A 단일 쿼리", "B Multi-Query+RRF", "비고"], rows, [56, 14, 18, 70])

    # ---- 통합·Fallback ----
    rows = []
    if F:
        for c in F["cases"]:
            rows.append(("Fallback·단위", c["case"], "통과" if c["pass"] else "실패", c["detail"]))
    if P:
        for r in P["runs"]:
            rows.append((f"통합 {r['mode']}", r["pid"], "통과" if (r["questions"] == 8 and not r["error"]) else "실패",
                         {"seconds": r["seconds"], "questions": r["questions"], "company_rag": r["company_rag"], "log": r["log"]}))
    sheet("통합_Fallback테스트", ["구분", "케이스", "결과", "상세"], rows or [("-", "미측정", None, None)], [14, 50, 8, 110])

    # ---- 메타데이터 필터 ----
    rows = []
    if M:
        mc = M["match_counts"]
        for k, v in M["chroma"].items():
            kind, cond = k.split("|")
            rows.append(("Chroma " + {"query_k12": "검색 k=12", "query_k50": "검색 k=50", "get_only": "get(where)만 — 벡터 검색 없음"}[kind],
                         cond, mc.get(cond), v["n"], v["mean_ms"], v["p50_ms"], v["p95_ms"]))
        for k, v in M["redis"].items():
            rows.append(("Redis KNN k=16", "사전 필터(role·category·translated)" if k == "filtered" else "필터 없음", None, v["n"], v["mean_ms"], v["p50_ms"], v["p95_ms"]))
    ws = sheet("메타필터_실측", ["저장소·방식", "조건", "조건에 맞는 조각 수", "반복", "평균 ms", "P50 ms", "P95 ms"], rows or [("미측정",)],
               [34, 28, 14, 8, 10, 10, 10])
    if M:
        ws.append([])
        ws.append(["같은 질의 상위 12 일치(where 있음/없음)", M["same_top12_with_without_where"]])
        ws.append(["측정 중 조각 수 변화", f"{M['public_count_before']} → {M['public_count_after']} (동시 쓰기 여부 확인용)"])
        ws.append(["chromadb 버전", M["chromadb"]])

    analysis = [
        ("1 필터 인덱스 부재", "아님(실측). chroma.sqlite3에 embedding_metadata(key, string_value) 부분 인덱스가 있고, 같은 조건의 SQLite 질의 계획이 이 인덱스를 씀(EXPLAIN QUERY PLAN). 같은 조건을 SQLite에 직접 물으면 2,205행 · P50 23.3 ms(읽기 전용 사본)", "실측"),
        ("2 필터 처리 위치", "Chroma: where를 Chroma에 넘김(internal_store.query_public → collection.query(where=…)) = DB 안 사전 필터. 앱 쪽 후처리는 하한·금지 용어와 4-B 재확인(B-09)뿐. Redis: KNN 질의 문자열 안 TAG 사전 필터(FT.SEARCH 하이브리드) = DB 안", "코드 확인"),
        ("3 실행 조건 불일치", "15:47 수치(19.1 vs 870.6 ms)는 같은 벡터 · k=12 · 적재 끝난 뒤 · 50회. 이번 재측정은 같은 벡터를 조건 순서를 섞어 반복(워밍업 3회), 측정 전후 조각 수로 동시 쓰기 없음 확인. 15:00 첫 측정(7.5 vs 508 ms)은 동시 적재 중 잠정치", "실측"),
        ("4 어느 DB에서 지연?", "Chroma만. Redis는 사전 필터 KNN이 약 1 ms로 필터 없음과 차이 없음(메타필터_실측 Redis 행). 지연은 전부 Chroma where 경로", "실측"),
        ("5 원인 추정", "메타데이터 SQLite 평가(약 23 ms)나 벡터 검색(약 16~19 ms)만으로는 870 ms가 설명되지 않음 → Chroma 로컬(1.5.9) where 실행 경로의 고정 비용(맞는 ID 집합을 만들어 HNSW에 넘기는 단계)으로 추정. get(where)만 잰 행과 좁은 필터(open 30조각·doc_id 1개) 행으로 '맞는 조각 수에 비례하는지'를 비교 — 수치는 메타필터_실측 시트. Chroma 로컬에는 실행 계획·프로파일 기능이 없어 내부 단계별 시간은 확인 불가(추정)", "추정(근거 실측)"),
        ("기존 보고와 코드의 차이", "'메타데이터 필터를 운영 검색 경로에 적용하지 않았다'는 사실과 다름: 운영 코드 internal_search.search가 where(disclosure=public AND status∈active·done·resolved)를 지금도 매 검색에 씀(안전 필터 B-09라 이번 작업에서 끄지 않음). 또 '검색 범위 감소 41.7%'는 where 필터 효과가 아니라 public/restricted 컬렉션 분리 효과(분리 안 했다면 상위 결과의 41.7%가 restricted)", "코드 확인"),
    ]
    sheet("메타필터_원인분석", ["확인 항목", "결과", "구분"], analysis, [22, 140, 14])

    # ---- 적용 / 미적용 ----
    rows = [
        ("Multi-Query(규칙 3쿼리) + RRF(k=60)", "구현 완료 · 기본값은 6.18.5 결정 참고", "config.RAG_MULTI_QUERY · question_graph._multi_queries/_query_vectors/_knn_fused · internal_search.search_multi · agents/rag_fusion.py"),
        ("단일 쿼리(기준선)", "유지(스위치 0 · 모든 실패 시 대체 경로)", "question_graph._redis_query_vector · internal_search.search"),
        ("검색 병렬화", "적용(B 경로만)", "rag_fusion.parallel(ThreadPoolExecutor) — 쿼리별 Redis KNN · Chroma 검색"),
        ("묶음 임베딩", "적용(B 경로)", "쿼리 3개를 llm.embed 한 번에"),
        ("HyDE", "적용 안 함(사용자 지시)", "ADLC QO-10 후보 유지"),
        ("LLM 쿼리 재작성", "적용 안 함", "B의 쿼리는 규칙 생성 — 생성 모델 호출 0"),
        ("Chroma where 제거·후처리 전환(QO-02)", "적용 안 함(원인 분석 우선, 안전 필터)", "운영 경로는 where 그대로"),
        ("Doc2Query(QO-11)", "적용 안 함(사용자 결정 대기)", ""),
        ("Dual RAG(직무 문항에도 사내문서 근거)", "구현 완료 · 기본값 RAG_DUAL_JOB=0(켤지는 사용자 결정)", "config.RAG_DUAL_JOB · questions.contextualize_job_rag · prompts/generate/job_question_rag.md · question_graph.retrieve_internal/generate/guard_disclosure"),
    ]
    sheet("적용_미적용", ["최적화", "상태", "위치·비고"], rows, [40, 40, 90])


    # ---- A/B/C Dual RAG (사용자 지시 2026-11-16, ADLC 6.18) ----
    D = load(a.runs, "dual_rag_pipeline_2026-11-16.json")
    T3 = load(a.runs, "dual_rag_tests_2026-11-16.json")
    names3 = {"A": "A 단일 쿼리 Redis RAG", "B": "B Multi-Query+RRF Redis RAG", "C": "C Multi-Query+RRF Redis + Chroma Dual RAG"}

    def g(d, *ks):
        for k in ks:
            if d is None:
                return None
            d = d.get(k) if isinstance(d, dict) else None
        return d

    def pct3(base, v, lower_better=False):
        if base in (None, 0) or v is None:
            return None
        return round((v - base) / base * 100 * (-1 if lower_better else 1), 1)

    rows = []
    metric_defs = []
    if R:
        for key in ("redis/personality", "redis/job", "redis/company"):
            s = R["summary"].get(key)
            if not s:
                continue
            for m in ("recall@5", "mrr@5", "ndcg@5"):
                metric_defs.append((f"검색 {key} {m}", s["A"][m], s["B16"][m], s["B16"][m], False, "검색 품질(Redis 정답 기준: 질문 은행 문항 0/1/2 풀링 판정)",
                                    "C의 Redis 검색은 B와 같은 코드"))
        s = R["summary"]["chroma/internal"]
        for m in ("recall@5", "mrr@5", "ndcg@5"):
            metric_defs.append((f"검색 chroma/internal {m}", None, None, s["B"][m], False,
                                "검색 품질(Chroma 정답 기준: 사내문서 조각 0/1/2 풀링 판정)",
                                f"A·B는 사내문서를 쓰지 않음. 참고: 단일 쿼리 Chroma {s['A'][m]}"))
    if D:
        S = D["summary"]
        for key, lbl, lb in (("search_ms", "전체 파이프라인 안 검색 노드(retrieve+retrieve_internal) ms", True),
                             ("total_seconds", "전체 질문 생성 시간 ms", True)):
            for st_ in ("mean_ms", "p50_ms", "p95_ms"):
                metric_defs.append((f"{lbl} {st_[:-3].upper()}", g(S, "A", key, st_), g(S, "B", key, st_), g(S, "C", key, st_), lb,
                                    "지연(실측, 그래프 1-A generate_set)", ""))
        for key, lbl in (("chat_calls_mean", "생성 모델 호출 / 응시"), ("embed_requests_mean", "임베딩 요청 / 응시"),
                         ("embed_inputs_mean", "임베딩 입력 문장 / 응시"), ("tokens_chat_in_mean", "LLM 입력 토큰 / 응시"),
                         ("tokens_chat_out_mean", "LLM 출력 토큰 / 응시"), ("tokens_embed_in_mean", "임베딩 입력 토큰 / 응시")):
            metric_defs.append((lbl, S["A"][key], S["B"][key], S["C"][key], True, "호출·토큰(실측, Ollama 응답 카운트)", ""))
        for key, lbl in (("job_relevance_all", "질문 직무 관련성(1~5, 이력서·직무·기업 문항)"),
                         ("resume_consistency_all", "이력서 정합성(1~5)")):
            metric_defs.append((lbl, S["A"][key], S["B"][key], S["C"][key], False, "최종 질문 품질(로컬 qwen3:4b 판정)", ""))
        for c in ("job", "company"):
            metric_defs.append((f"직무 관련성 — {c} 문항", S["A"]["job_relevance"][c], S["B"]["job_relevance"][c], S["C"]["job_relevance"][c], False,
                                "최종 질문 품질", ""))
        metric_defs.append(("Chroma 근거 충실도(1~5, 사내문서 근거 문항만)", None, None, S["C"]["doc_faithfulness"], False, "최종 질문 품질",
                            f"C 사내문서 근거 문항 {S['C']['doc_questions']}개"))
        for key, lbl in (("fabricated_experience", "허위 경험 전제(판정 true 건수)"), ("rule_leaks", "사내문서 정보 노출(금지 용어·마스킹 표식, 규칙 검사 건수)"),
                         ("guard_blocked", "7-B 관문 차단 건수")):
            metric_defs.append((lbl, S["A"][key], S["B"][key], S["C"][key], True, "안전", ""))
        for key, lbl in (("questions_ok", "8문항 완성 응시 수"), ("errors", "오류 응시 수")):
            metric_defs.append((lbl, S["A"][key], S["B"][key], S["C"][key], key == "errors", "안정성", f"응시 {S['A']['runs']}회씩"))
    for (lbl, va, vb, vc, lb, grp, note) in metric_defs:
        rows.append((grp, lbl, va, vb, vc, pct3(va, vb, lb), pct3(va, vc, lb), pct3(vb, vc, lb), note))
    sheet("ABC_비교", ["구분", "지표", "A 단일 Redis", "B MQ+RRF Redis", "C Dual(Redis+Chroma)", "B 대비 A 개선율(%)", "C 대비 A 개선율(%)",
                      "C 대비 B 개선율(%)", "비고"], rows or [("미측정",)], [26, 50, 12, 14, 16, 12, 12, 12, 50])
    wsn = wb["ABC_비교"]
    wsn.append([])
    wsn.append(["개선율 부호", "높을수록 좋은 지표 = (새 값 − 기준)/기준 × 100. 낮을수록 좋은 지표(지연·호출·토큰·건수) = 줄어든 비율이 양수. 빈 칸 = 기준이 0이거나 해당 방식에서 측정 대상 아님"])

    if D:
        S = D["summary"]
        ws = wb.create_sheet("ABC_차트")
        ws.append(["지표", "A", "B", "C"])
        qrows = [("직무 관련성", S["A"]["job_relevance_all"], S["B"]["job_relevance_all"], S["C"]["job_relevance_all"]),
                 ("이력서 정합성", S["A"]["resume_consistency_all"], S["B"]["resume_consistency_all"], S["C"]["resume_consistency_all"])]
        for r in qrows:
            ws.append(list(r))
        ch = BarChart()
        ch.title, ch.y_axis.title = "최종 질문 품질 (1~5, 로컬 판정)", "점수"
        ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=1, max_row=1 + len(qrows)), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=1 + len(qrows)))
        ch.height, ch.width = 8, 16
        ws.add_chart(ch, "F1")
        r0 = ws.max_row + 2
        ws.cell(r0, 1, "지표")
        for j, h in enumerate(["A", "B", "C"], 2):
            ws.cell(r0, j, h)
        lrows = [("검색 노드 평균 ms", S["A"]["search_ms"]["mean_ms"], S["B"]["search_ms"]["mean_ms"], S["C"]["search_ms"]["mean_ms"]),
                 ("검색 노드 P95 ms", S["A"]["search_ms"]["p95_ms"], S["B"]["search_ms"]["p95_ms"], S["C"]["search_ms"]["p95_ms"])]
        for i, r in enumerate(lrows, 1):
            for j, v in enumerate(r, 1):
                ws.cell(r0 + i, j, v)
        ch = BarChart()
        ch.title, ch.y_axis.title = "검색 노드 지연 (ms)", "ms"
        ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=r0, max_row=r0 + len(lrows)), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=r0 + 1, max_row=r0 + len(lrows)))
        ch.height, ch.width = 8, 16
        ws.add_chart(ch, "F18")
        r1 = ws.max_row + 2
        ws.cell(r1, 1, "지표")
        for j, h in enumerate(["A", "B", "C"], 2):
            ws.cell(r1, j, h)
        trow = ("전체 질문 생성 평균 초", round(S["A"]["total_seconds"]["mean_ms"] / 1000, 1), round(S["B"]["total_seconds"]["mean_ms"] / 1000, 1),
                round(S["C"]["total_seconds"]["mean_ms"] / 1000, 1))
        for j, v in enumerate(trow, 1):
            ws.cell(r1 + 1, j, v)
        ch = BarChart()
        ch.title, ch.y_axis.title = "전체 질문 생성 시간 (초)", "초"
        ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=r1, max_row=r1 + 1), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=r1 + 1, max_row=r1 + 1))
        ch.height, ch.width = 8, 16
        ws.add_chart(ch, "F35")
        if R:
            r2 = ws.max_row + 2
            hdr = ["대상(nDCG@5)", "A 단일", "B·C MQ+RRF"]
            for j, h in enumerate(hdr, 1):
                ws.cell(r2, j, h)
            rr = [(k, R["summary"][k]["A"]["ndcg@5"], R["summary"][k].get("B16", R["summary"][k].get("B"))["ndcg@5"]) for k in R["summary"]]
            for i, r in enumerate(rr, 1):
                for j, v in enumerate(r, 1):
                    ws.cell(r2 + i, j, v)
            ch = BarChart()
            ch.title, ch.y_axis.title = "검색 품질 nDCG@5 (Redis 3카테고리 · Chroma)", "nDCG@5"
            ch.add_data(Reference(ws, min_col=2, max_col=3, min_row=r2, max_row=r2 + len(rr)), titles_from_data=True)
            ch.set_categories(Reference(ws, min_col=1, min_row=r2 + 1, max_row=r2 + len(rr)))
            ch.height, ch.width = 8, 16
            ws.add_chart(ch, "F52")

        # 트레이드오프
        rows = []
        for m in ("A", "B", "C"):
            s_ = S[m]
            rows.append((names3[m], s_["search_ms"]["mean_ms"], s_["search_ms"]["p95_ms"], round(s_["total_seconds"]["mean_ms"] / 1000, 1),
                         s_["chat_calls_mean"], s_["tokens_chat_in_mean"], s_["job_relevance_all"], s_["resume_consistency_all"],
                         s_["job_rag_questions"] + s_["company_rag_questions"]))
        sheet("ABC_트레이드오프", ["방식", "검색 노드 평균 ms", "검색 노드 P95 ms", "전체 생성 평균 초", "LLM 호출/응시", "LLM 입력 토큰/응시",
                                  "직무 관련성", "이력서 정합성", "사내문서 근거 문항 수(합)"], rows, [40, 14, 14, 14, 12, 14, 12, 12, 16])

        # Chroma로 더 얻은 업무 맥락
        rows = []
        for r in D["runs"]:
            if r["mode"] != "C":
                continue
            for q in r["questions_out"]:
                if q["category"] in ("job", "company"):
                    j = q.get("judge") or {}
                    rows.append((r["pid"], r["role"], q["category"], "사내문서 근거" if q.get("used_doc") else "공고·은행만",
                                 (q.get("gen_method") or "").split("|internal:")[-1] if q.get("used_doc") else "", q["text"],
                                 j.get("job_relevance"), j.get("resume_consistency"), j.get("doc_faithfulness") or None, j.get("fabricated_experience")))
        sheet("ABC_Chroma맥락", ["응시", "직무", "문항", "근거", "출처 조각 ID", "최종 질문(가상 데이터)", "직무 관련성", "이력서 정합성",
                                "근거 충실도", "허위 경험"], rows or [("미측정",)], [7, 9, 8, 12, 30, 80, 10, 10, 10, 9])
        rows = []
        for m in ("A", "B", "C"):
            s_ = S[m]
            rows.append((names3[m], s_["dual_modes"], s_["job_rag_questions"], s_["company_rag_questions"], s_["guard_blocked"]))
        sheet("ABC_RAG경로", ["방식", "응시별 경로(dual·redis_only·chroma_only·safe)", "직무 문항 사내문서 근거", "기업 문항 사내문서 근거",
                             "7-B 차단"], rows, [40, 50, 16, 16, 10])

    # 평가 환경·데이터셋
    env_rows = [
        ("측정 PC", "회사 PC, GTX 1660 SUPER 6GB, Docker api 컨테이너 안 격리 복사본(/tmp/mq) — 운영 중 서버 코드는 바꾸지 않음"),
        ("모델", f"생성·판정 {g(D, 'meta', 'llm_model') or 'qwen3:4b'} (Ollama, 로컬) · 임베딩 {g(D, 'meta', 'embed_model') or 'qwen3-embedding:0.6b'}"),
        ("평가 데이터셋", "가상 지원자 20명(sim_applicants_2026-11-16/gen.json: 공고 9개 · 확인 완료 이력서 스냅샷 · A5 결과). 검색 품질 = 20명 전원, 전체 파이프라인 A/B/C = "
                       + ", ".join(g(D, "meta", "pids") or ["미측정"])),
        ("검색 정답 — Redis", "질의마다 A·B 상위 5개 합집합의 질문 은행 문항을 qwen3:4b가 0/1/2 판정(풀링). 카테고리(인성·직무·기업)별 따로"),
        ("검색 정답 — Chroma", "질의마다 단일·Multi-Query 상위 5개 합집합의 사내문서 조각을 같은 방식으로 판정. Redis와 정답 집합을 섞지 않음"),
        ("질문 품질 판정", "생성 결과의 이력서·직무·기업 문항을 qwen3:4b(temperature 0)가 직무 관련성·이력서 정합성·허위 경험·근거 충실도로 판정. 생성 모델과 같은 모델이라 자기 평가 편향 가능 — 사람 정답 아님"),
        ("검색 품질 측정 시각", g(R, "meta", "measured_at") or "미측정"),
        ("파이프라인 측정 시각", g(D, "meta", "measured_at") or "미측정"),
        ("방식 정의", "A: RAG_MULTI_QUERY=0 · INTERNAL_DOCS_RAG=0 · RAG_DUAL_JOB=0 / B: 1·0·0 / C: 1·1·1 (C는 사내문서 근거를 직무 2·기업 1 문항에)"),
        ("HyDE · 메타 필터", "HyDE 미적용. Chroma where(disclosure·status) 안전 필터는 기존 운영 코드 그대로(새로 켜거나 끄지 않음) — 지연 원인은 메타필터 시트"),
    ]
    sheet("평가환경_데이터셋", ["항목", "내용"], env_rows, [24, 140])

    # Dual 단위·통합 시험
    rows = []
    if T3:
        for suite, cases in T3.items():
            for name, ok in cases:
                rows.append((suite, name, "통과" if ok else "실패"))
    if D:
        for r in D["runs"]:
            rows.append((f"통합 {r['mode']}", r["pid"], "통과" if (r["questions"] == 8 and not r["error"]) else "실패"))
    sheet("Dual_시험결과", ["묶음", "케이스", "결과"], rows or [("미측정",)], [24, 90, 8])

    ws = wb["개요"]
    ws.append(("추가 시트(2026-11-16 Multi-Query+RRF)", "AB_검색품질 · AB_지연 · AB_호출수 · 통합_Fallback테스트 · 메타필터_실측 · 메타필터_원인분석 · 적용_미적용"))
    ws.append(("A/B 평가 방법", "A = 기존 단일 쿼리, B = Multi-Query(규칙 3쿼리) + RRF(k=60). 같은 질의 세트(가상 지원자 20명의 그래프 1-A 입력). 정답 = 질의마다 A·B 상위 5개 합집합을 로컬 qwen3:4b가 0/1/2로 판정한 풀링 정답(사람 정답 아님). Recall@5 = 풀 안 관련 항목 대비. 실측과 추정은 '구분' 열로 나눔"))
    ws.append(("추가 시트(2026-11-16 Dual RAG)", "ABC_비교 · ABC_차트 · ABC_트레이드오프 · ABC_Chroma맥락 · ABC_RAG경로 · 평가환경_데이터셋 · Dual_시험결과"))
    ws.append(("A/B/C 정의", "A = 단일 쿼리 Redis RAG, B = Multi-Query+RRF Redis RAG, C = Multi-Query+RRF Redis + Chroma Dual RAG(직무 2·기업 1 문항에 사내문서 근거). 검색 품질과 최종 질문 품질은 따로 판정"))
    for row in ws.iter_rows(min_row=ws.max_row - 3):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        row[0].font = Font(bold=True)
    wb.save(a.out)
    print("저장:", a.out, wb.sheetnames)


if __name__ == "__main__":
    main()
