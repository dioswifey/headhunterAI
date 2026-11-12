"""사내문서 RAG 골든셋 자동 생성 — G-검색 · G-질문 (2026-11-12, 사람 확인 없이 자동).

제품 코드는 고치지 않는다. 사내문서 public 컬렉션(`internal_store.PUBLIC`)만 읽는다(가상 회사 헤드헌터랩 문서, 지원자 데이터 없음).
생성(검색 질의·G-질문)만 OpenAI `llm.openai_chat_json`(모델 = .env OPENAI_MODEL, 2026-11-12 사용자 결정 — 가상 사내문서 public 조각과
지시문만 보내고 지원자 데이터는 보내지 않는다). 판정(모호성·질문 품질)은 로컬 `llm.chat_json`(--model, 기본 qwen3:4b),
임베딩은 로컬 `llm.embed`(qwen3-embedding:0.6b). `--gen local`이면 생성도 로컬. 호출은 한 번에 1개(순차) — GPU 경합 방지.
OpenAI 사용량은 openai_usage.jsonl에 호출마다 기록(USD = OPENAI_PRICE_IN/OUT_PER_M, 0이면 토큰만).

    python -m tools.build_golden_sets retrieval --out /tmp/golden [--per-doc 5] [--nq 2] [--limit 10] [--model qwen3:4b]
    python -m tools.build_golden_sets questions --out /tmp/golden [--per-doc 3] [--model qwen3:4b]
    python -m tools.build_golden_sets metrics   --out /tmp/golden
    python -m tools.build_golden_sets report    --out /tmp/golden --date 2026-11-12
    python -m tools.build_golden_sets sample    --out /tmp/golden --date 2026-11-12      # G_set 대표 표본 md

G-검색 (retrieval)
  조각마다 "이 조각이 정답인 검색 질의" 3개(문장형 2 · 키워드형 1)를 만들고 자동 필터 2개를 거친다.
  ① 너무 쉬움: 질의 문자 3-gram 중 조각에 있는 비율 > OVERLAP_MAX, 또는 조각과 공백 뺀 공통 연속 문자열 ≥ LCS_MAX → 탈락
  ② 모호함: public 전체 조각과 코사인 유사도를 재서, 정답 아닌 최상위 조각의 유사도 ≥ 정답 유사도 − AMBIG_MARGIN 이면
     판정 모델에 정답 조각·경쟁 조각을 A/B(순서 무작위)로 보여 주고 "어느 조각이 질의의 답인가"를 묻는다.
     정답 조각만 고르면 유지, 경쟁 조각·둘 다·둘 다 아님이면 탈락.
  조각 순서는 문서별 라운드로빈 → --limit로 줄여도 문서가 고르게 들어간다.

G-질문 (questions)
  public 문서마다 서로 떨어진 조각 per-doc개를 골라, 조각 하나당 이상적인 회사 맞춤 면접 질문 1개를 만든다.
  judge_question_quality.md [quality] 루브릭 5항목 평균 ≥ 4.5 그리고 grounded=true 만 남긴다.

metrics: 최종 G-검색으로 ① 원시 벡터 검색(k=12) Recall@1·@4·MRR ② 제품 파이프라인 internal_search.search(재순위·문서당 2·상위 4)
  Recall@4·MRR ③ MIN_SIM 0.45~0.70 표(Recall@4 · 빈 결과율 · 노이즈 비율 · 무관 질의 통과율).
단계마다 파일에 쓰고, 이미 처리한 조각·문서는 건너뛴다(중단 후 이어서 실행 가능).
"""
import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from app import config, internal_store, llm  # noqa: E402
from app.agents import internal_search  # noqa: E402

PROMPT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "prompts", "evaluate",
                           "judge_question_quality.md")
ROLE_NAME = {"ai_ax": "AI/AX 엔지니어", "backend": "백엔드 개발자", "data_sci": "데이터 사이언티스트",
             "consult": "AI 컨설턴트", "sales": "영업"}
RUBRIC = ("clarity", "job_relevance", "answerability", "one_sentence", "unbiased")

OVERLAP_MAX = 0.70     # 필터 ①: 질의 3-gram이 조각에 들어 있는 비율 상한
LCS_MAX = 14           # 필터 ①: 공백 뺀 공통 연속 문자열 길이 상한(이 이상이면 원문 베끼기)
AMBIG_MARGIN = 0.03    # 필터 ②: 경쟁 조각 유사도가 정답 − 이 값 이상이면 판정 모델 확인
Q_KEEP = 4.5           # G-질문 루브릭 평균 하한
MIN_SIM_GRID = [round(0.45 + 0.05 * i, 2) for i in range(6)]

# 무관 질의(사내문서와 관계없는 주제) — MIN_SIM별 '노이즈 통과율' 측정용 고정 목록
NEGATIVES = [
    "주말에 가기 좋은 서울 근교 캠핑장 추천", "김치찌개 맛있게 끓이는 방법", "고양이 사료 고르는 기준",
    "제주도 3박 4일 여행 일정", "아파트 전세 계약할 때 확인할 것", "마라톤 풀코스 준비 훈련 계획",
    "기타 코드 잡는 법 초보", "겨울철 실내 식물 물 주기", "프로야구 한국시리즈 일정", "중학생 수학 방정식 문제 풀이",
    "피아노 독학 교재", "자동차 엔진오일 교체 주기", "다이어트 식단 일주일", "유럽 배낭여행 숙소 예약",
    "강아지 산책 시간 얼마나", "결혼식 축의금 적정 금액", "등산화 사이즈 고르는 법", "커피 원두 로스팅 단계",
    "초등학생 독서 습관 기르기", "반려견 예방접종 일정",
]

QUERY_SCHEMA = {"type": "object", "properties": {
    "queries": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "object", "properties": {
        "query": {"type": "string"}, "style": {"type": "string", "enum": ["sentence", "keywords"]},
        "role": {"type": "string"}}, "required": ["query", "style", "role"]}}},
    "required": ["queries"]}
AMBIG_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string", "enum": ["A", "B", "both", "neither"]}, "reason": {"type": "string"}},
    "required": ["answer", "reason"]}
QGEN_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}, "role": {"type": "string"}},
               "required": ["question", "role"]}
QUALITY_SCHEMA = {"type": "object", "properties": {
    **{k: {"type": "integer", "minimum": 1, "maximum": 5} for k in RUBRIC},
    "grounded": {"type": "boolean"}, "ungrounded_facts": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}},
    "required": list(RUBRIC) + ["grounded", "ungrounded_facts", "reason"]}

QUERY_SYS = """너는 회사 사내문서 검색 시스템의 평가용 질의를 만드는 사람이다.
상황: AI 면접 시스템이 지원자의 '지원 직무·채용 공고·이력서 키워드'를 바탕으로 회사 사내문서를 검색해 회사 맞춤 면접 질문의 근거를 찾는다.
아래 [조각]이 정답이 되는 검색 질의를 만든다.

규칙
- 한국어. 질의 [[nq]]개: style=sentence [[ns]]개(자연스러운 한 문장, 25~60자), style=keywords 1개(문장이 아니라 공고·이력서에서 뽑은 듯한 명사 키워드 4~7개를 띄어쓰기로만 나열, 조사·어미 없음).
- 이 조각에만 있는 구체적인 내용(특정 절차·기준·사례·수치의 주제)을 겨냥해, 같은 회사의 다른 문서가 아니라 이 조각이 가장 잘 맞도록 만든다.
- 조각의 문장을 그대로 베끼지 않는다. 같은 뜻을 다른 말로 바꾸고, 8글자 이상 연속으로 같은 표현을 쓰지 않는다. 문서 제목·섹션 제목을 그대로 넣지 않는다.
- 지원자나 면접관이 궁금해할 만한 관점(직무에서 무엇을 하는지, 어떤 기준으로 일하는지, 어떤 사례가 있었는지)으로 쓴다.
- role: 이 질의를 던질 지원 직무 코드 하나 — 다음 중에서만: [[roles]]
- 출력: JSON {"queries":[{"query":..., "style":..., "role":...}, ...]}"""

AMBIG_SYS = """너는 검색 정답 판정관이다. 검색 질의 하나와 사내문서 조각 A·B가 주어진다.
질의가 찾으려는 정보가 어느 조각에 들어 있는지 판정한다.
- A만 답이면 "A", B만 답이면 "B"
- 두 조각 모두 질의에 똑같이 잘 답하면 "both"(질의가 모호함)
- 둘 다 답이 아니면 "neither"
엄격하게 판정한다. 한 조각이 질의의 핵심(구체적 절차·기준·사례)을 담고 다른 조각은 주제만 비슷하면 핵심을 담은 쪽 하나만 고른다.
출력: JSON {"answer": "A"|"B"|"both"|"neither", "reason": 한 줄}"""

QGEN_SYS = """너는 헤드헌터랩의 1차 AI 면접 질문 설계자다. 아래 사내문서 조각(공개 가능한 자료)을 근거로,
[[role_name]] 지원자에게 던질 이상적인 '기업(회사 맞춤) 질문' 1개를 만든다.
- 한국어 한 문장, 물음표로 끝남, 40~110자.
- 조각에 나온 회사의 실제 업무 방식·기준·상황을 한 가지 짚고, 지원자가 자기 경험으로 90초 안에 답하게 묻는다.
- 조각에 없는 회사 사실·수치·프로젝트명을 지어내지 않는다. 조각의 문장을 길게 베끼지 않는다.
- 나이·성별·가족·결혼·종교·출신·병역·외모·학교/회사 서열을 묻지 않는다. 압박·유도·함정 질문 금지.
- role: 직무 코드(입력으로 받은 값 그대로)
출력: JSON {"question": ..., "role": ...}"""


# ---------------- 공통 ----------------
def _prompt(section, **vals):
    text = open(PROMPT_PATH, encoding="utf-8").read()
    m = re.search(rf"^## \[{section}\]\s*$(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    body = m.group(1).strip()
    for k, v in vals.items():
        body = body.replace(f"[[{k}]]", str(v))
    return body


def _fill(tpl, **vals):
    for k, v in vals.items():
        tpl = tpl.replace(f"[[{k}]]", str(v))
    return tpl


def _call(system, user, schema, model, temperature=0.0, num_ctx=6144):
    t = time.time()
    err = ""
    for _ in range(2):
        try:
            r = llm.chat_json(system, user, schema, timeout=400, temperature=temperature, model=model, num_ctx=num_ctx)
            return r, round(time.time() - t, 1)
        except llm.LLMError as e:
            err = str(e)
    return {"error": err}, round(time.time() - t, 1)


def _gen(a, system, user, schema, temperature):
    """생성 호출. --gen openai면 OpenAI(사용량 기록), local이면 판정과 같은 로컬 모델."""
    if a.gen == "local":
        return _call(system, user, schema, a.model, temperature=temperature)
    t = time.time()
    err = ""
    for _ in range(2):
        try:
            r, u = llm.openai_chat_json(system, user, schema, timeout=300, temperature=temperature, max_tokens=4000)
            _jsonl_append(os.path.join(a.out, "openai_usage.jsonl"), {**u, "ok": True, "ts": int(time.time())})
            return r, round(time.time() - t, 1)
        except llm.LLMError as e:
            err = str(e)
            u = getattr(e, "usage", None)
            if u:
                _jsonl_append(os.path.join(a.out, "openai_usage.jsonl"), {**u, "ok": False, "ts": int(time.time())})
            if "OPENAI_API_KEY" in err or " 401" in err or " 403" in err or " 429" in err:
                raise SystemExit(f"OpenAI 호출 중단: {err}")
    return {"error": err}, round(time.time() - t, 1)


def _gen_model(a):
    return f"OpenAI {config.OPENAI_MODEL}" if a.gen == "openai" else a.model


def _jsonl_read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _jsonl_append(path, row):
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _jsonl_write(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _load_public():
    g = internal_store.collection(internal_store.PUBLIC).get(include=["documents", "metadatas", "embeddings"])
    ids, docs, metas = g["ids"], g["documents"], g["metadatas"]
    E = np.asarray(g["embeddings"], dtype=np.float32)
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-12
    return ids, docs, metas, E


def _roles(meta):
    return [r for r in (meta.get("dept") or "").split("|") if r in ROLE_NAME] or ["ai_ax"]


def _body(doc):
    """조각 앞머리 '# 문서 > ## 섹션' 줄을 뗀 본문."""
    return re.sub(r"^#[^\n]*\n+", "", doc, count=1).strip()


def _norm(s):
    return re.sub(r"\s+", "", s)


def _grams(s, n=3):
    s = _norm(s)
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _lcs_len(a, b):
    """공백 뺀 최장 공통 연속 문자열 길이(DP, 질의가 짧아 충분히 빠름)."""
    a, b = _norm(a), _norm(b)
    best, prev = 0, [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _overlap(query, chunk):
    q = _grams(query)
    return round(len(q & _grams(chunk)) / len(q), 3) if q else 1.0


def _embed1(text):
    v = np.asarray(llm.embed([text])[0], dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-12)


def _meta(out, **kv):
    path = os.path.join(out, "run_meta.json")
    m = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    m.update(kv)
    json.dump(m, open(path, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)


def _spread(ids, metas, docs, k):
    """문서마다 문서 안 순서로 고르게 떨어진 조각 k개(300자 미만 조각 제외) → 문서별 라운드로빈 순서."""
    by_doc = defaultdict(list)
    for n, m in enumerate(metas):
        by_doc[m["doc_id"]].append(n)
    picks = {}
    for d, lst in by_doc.items():
        lst = sorted(lst, key=lambda n: (metas[n].get("chunk_index", 0), ids[n]))
        lst = [n for n in lst if len(_body(docs[n])) >= 300] or lst
        kk = min(k, len(lst))
        picks[d] = [ids[lst[round(x * (len(lst) - 1) / max(kk - 1, 1))]] for x in range(kk)] if kk > 1 else [ids[lst[0]]]
    order, dl = [], sorted(picks)
    for r in range(k):
        for d in dl:
            if r < len(picks[d]):
                order.append(picks[d][r])
    return order


def _round_robin(ids, metas):
    by_doc = defaultdict(list)
    for i, m in zip(ids, metas):
        by_doc[m["doc_id"]].append(i)
    order, docs = [], sorted(by_doc)
    while any(by_doc[d] for d in docs):
        for d in docs:
            if by_doc[d]:
                order.append(by_doc[d].pop(0))
    return order


# ---------------- G-검색 ----------------
def cmd_retrieval(a):
    os.makedirs(a.out, exist_ok=True)
    cand_path = os.path.join(a.out, "retrieval_candidates.jsonl")
    log_path = os.path.join(a.out, "retrieval_progress.log")
    ids, docs, metas, E = _load_public()
    idx = {cid: i for i, cid in enumerate(ids)}
    done = {r["chunk_id"] for r in _jsonl_read(cand_path)}
    order = _spread(ids, metas, docs, a.per_doc) if a.per_doc else _round_robin(ids, metas)
    if a.gen == "openai" and not config.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY 없음 — 중단")
    _meta(a.out, model=a.model, judge_model=a.model, gen_model=_gen_model(a), retrieval_per_doc=a.per_doc, nq=a.nq,
          retrieval_sample_note=(f" (전체 {len(ids)}조각 중 문서마다 고르게 떨어진 {a.per_doc}개 표본 — 순차 호출·GPU 경합으로 "
                                 f"전수는 30시간 이상 걸려 줄임)" if a.per_doc else ""))
    if a.limit:
        order = order[:a.limit]
    rng = random.Random(a.seed)
    t0 = time.time()
    todo = [c for c in order if c not in done]
    for n, cid in enumerate(todo, 1):
        i = idx[cid]
        m, body = metas[i], _body(docs[i])
        roles = _roles(m)
        user = (f"[문서] {m.get('doc_title')} (doc_type={m.get('doc_type')})\n[섹션] {m.get('sec_title')}\n[조각]\n{body[:1800]}")
        g, gsec = _gen(a, _fill(QUERY_SYS, roles=", ".join(roles), nq=a.nq, ns=a.nq - 1), user, QUERY_SCHEMA, temperature=0.4)
        rows = []
        for q in (g.get("queries") or [])[:a.nq]:
            text = (q.get("query") or "").strip()
            if not text:
                continue
            role = q.get("role") if q.get("role") in roles else roles[0]
            row = {"chunk_id": cid, "doc_id": m["doc_id"], "sec": m.get("sec"), "doc_type": m.get("doc_type"),
                   "query": text, "style": q.get("style"), "role_hint": role, "gen_sec": gsec}
            ov, lcs = _overlap(text, body), _lcs_len(text, body)
            row.update({"overlap3": ov, "lcs": lcs})
            if ov > OVERLAP_MAX or lcs >= LCS_MAX:
                row.update({"verdict": "drop_easy"})
                rows.append(row)
                continue
            try:
                v = _embed1(text)
            except llm.LLMError as e:
                row.update({"verdict": "drop_error", "error": str(e)})
                rows.append(row)
                continue
            sims = E @ v
            rank = int((sims > sims[i]).sum()) + 1
            sims_o = sims.copy()
            sims_o[i] = -9
            j = int(sims_o.argmax())
            row.update({"gold_sim": round(float(sims[i]), 4), "gold_rank": rank,
                        "rival_id": ids[j], "rival_sim": round(float(sims[j]), 4)})
            if sims[j] >= sims[i] - AMBIG_MARGIN:
                gold_is_a = rng.random() < 0.5
                ca, cb = (body, _body(docs[j])) if gold_is_a else (_body(docs[j]), body)
                juser = f"[질의]\n{text}\n\n[조각 A]\n{ca[:1100]}\n\n[조각 B]\n{cb[:1100]}"
                r, jsec = _call(AMBIG_SYS, juser, AMBIG_SCHEMA, a.model, temperature=0.0, num_ctx=4096)
                ans = r.get("answer")
                picked = {"A": "gold" if gold_is_a else "rival", "B": "rival" if gold_is_a else "gold"}.get(ans, ans)
                row.update({"ambig_judge": picked, "ambig_reason": r.get("reason") or r.get("error"), "judge_sec": jsec})
                row["verdict"] = "keep" if picked == "gold" else "drop_ambiguous"
            else:
                row["verdict"] = "keep"
            rows.append(row)
        if not rows:
            rows.append({"chunk_id": cid, "doc_id": m["doc_id"], "verdict": "drop_gen_error", "error": g.get("error"), "gen_sec": gsec})
        for r in rows:
            _jsonl_append(cand_path, r)
        el = time.time() - t0
        msg = (f"[{time.strftime('%H:%M:%S')}] {n}/{len(todo)} {cid} gen={gsec}s "
               f"verdicts={Counter(r['verdict'] for r in rows)} elapsed={el:.0f}s eta={el / n * (len(todo) - n):.0f}s")
        print(msg, flush=True)
        with open(log_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(msg + "\n")
    _finalize_retrieval(a.out)


def _finalize_retrieval(out):
    rows = _jsonl_read(os.path.join(out, "retrieval_candidates.jsonl"))
    keep = [{"query": r["query"], "gold_chunk_ids": [r["chunk_id"]], "doc_id": r["doc_id"], "role_hint": r["role_hint"],
             "style": r.get("style"), "gold_sim": r.get("gold_sim"), "gold_rank_raw": r.get("gold_rank")}
            for r in rows if r["verdict"] == "keep"]
    for k, r in enumerate(keep, 1):
        r["id"] = f"GR-{k:04d}"
    keep = [{"id": r.pop("id"), **r} for r in keep]
    _jsonl_write(os.path.join(out, "golden_retrieval_v1.jsonl"), keep)
    print(f"golden_retrieval_v1.jsonl: {len(keep)}개 / 후보 {len(rows)}개 {dict(Counter(r['verdict'] for r in rows))}")


# ---------------- G-질문 ----------------
def cmd_questions(a):
    os.makedirs(a.out, exist_ok=True)
    cand_path = os.path.join(a.out, "questions_candidates.jsonl")
    log_path = os.path.join(a.out, "questions_progress.log")
    ids, docs, metas, _ = _load_public()
    by_doc = defaultdict(list)
    for i, m in enumerate(metas):
        by_doc[m["doc_id"]].append(i)
    done = {r["chunk_id"] for r in _jsonl_read(cand_path)}
    jobs = []
    for d in sorted(by_doc):
        lst = sorted(by_doc[d], key=lambda i: (metas[i].get("chunk_index", 0), ids[i]))
        lst = [i for i in lst if len(_body(docs[i])) >= 300] or lst      # 너무 짧은 조각(머리말 등)은 근거로 약함
        k = min(a.per_doc, len(lst))
        picks = [lst[round(x * (len(lst) - 1) / max(k - 1, 1))] for x in range(k)] if k > 1 else lst[:1]
        jobs.extend(dict.fromkeys(picks))
    if a.gen == "openai" and not config.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY 없음 — 중단")
    _meta(a.out, model=a.model, judge_model=a.model, gen_model=_gen_model(a), per_doc=a.per_doc)
    t0 = time.time()
    todo = [i for i in jobs if ids[i] not in done]
    for n, i in enumerate(todo, 1):
        m, body = metas[i], _body(docs[i])
        roles = [r for r in _roles(m) if r in CORE_ROLES] or _roles(m)
        role = roles[(n + len(done)) % len(roles)]
        user = f"[문서] {m.get('doc_title')} > {m.get('sec_title')}\n[직무 코드] {role}\n[조각]\n{body[:1800]}"
        g, gsec = _gen(a, _fill(QGEN_SYS, role_name=ROLE_NAME[role]), user, QGEN_SCHEMA, temperature=0.5)
        row = {"chunk_id": ids[i], "doc_id": m["doc_id"], "sec": m.get("sec"), "sec_title": m.get("sec_title"),
               "doc_title": m.get("doc_title"), "doc_type": m.get("doc_type"),
               "role_hint": role, "question": (g.get("question") or "").strip(), "gen_sec": gsec}
        if not row["question"]:
            row.update({"verdict": "drop_gen_error", "error": g.get("error")})
        else:
            juser = (f"[근거 자료 1: 사내문서 조각 — {m.get('doc_title')} > {m.get('sec_title')}]\n{body[:1800]}\n\n"
                     f"[근거 자료 2: 공고]\n헤드헌터랩 {ROLE_NAME[role]} 채용\n\n[채점할 질문]\n{row['question']}")
            r, jsec = _call(_prompt("quality", role_name=ROLE_NAME[role], category_label="기업(회사 맞춤) 질문"),
                            juser, QUALITY_SCHEMA, a.model, temperature=0.0, num_ctx=4096)
            row.update({"judge": r, "judge_sec": jsec})
            if "error" in r:
                row["verdict"] = "drop_judge_error"
            else:
                row["rubric_mean"] = round(sum(r[k] for k in RUBRIC) / len(RUBRIC), 2)
                row["verdict"] = ("keep" if row["rubric_mean"] >= Q_KEEP and r.get("grounded") else
                                  "drop_ungrounded" if not r.get("grounded") else "drop_low_score")
            row.update(_chroma_check(row["question"], ids[i]))
        _jsonl_append(cand_path, row)
        el = time.time() - t0
        msg = (f"[{time.strftime('%H:%M:%S')}] {n}/{len(todo)} {ids[i]} {row['verdict']} mean={row.get('rubric_mean')} "
               f"elapsed={el:.0f}s eta={el / n * (len(todo) - n):.0f}s")
        print(msg, flush=True)
        with open(log_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(msg + "\n")
    _finalize_questions(a.out)


CORE_ROLES = ("ai_ax", "backend", "data_sci")


def _chroma_check(question, gold_id):
    """질문 문장으로 Chroma public을 검색(제품과 같은 안전 필터) → 근거 조각의 유사도·순위. 실패해도 생성 결과는 남긴다."""
    where = {"$and": [{"disclosure": {"$eq": "public"}}, {"status": {"$in": ["active", "done"]}}]}
    try:
        v = llm.embed([question])[0]
        hits = internal_store.query_public(v, k=12, where=where)
    except Exception as e:
        return {"chroma_error": type(e).__name__}
    hid = [h["id"] for h in hits]
    out = {"chroma_top1": hid[0] if hid else None, "chroma_top1_sim": round(hits[0]["sim"], 4) if hits else None}
    if gold_id in hid:
        out.update({"chroma_rank": hid.index(gold_id) + 1, "chroma_sim": round(hits[hid.index(gold_id)]["sim"], 4)})
    else:
        out.update({"chroma_rank": None, "chroma_sim": None})
    return out


def _finalize_questions(out):
    rows = _jsonl_read(os.path.join(out, "questions_candidates.jsonl"))
    keep = [r for r in rows if r["verdict"] == "keep"]
    outrows = [{"id": f"GQ-{k:03d}", "question": r["question"], "doc_id": r["doc_id"], "gold_chunk_ids": [r["chunk_id"]],
                "sec_title": r.get("sec_title"), "doc_type": r.get("doc_type"),
                "role_hint": r["role_hint"], "rubric_mean": r["rubric_mean"],
                "scores": {x: r["judge"][x] for x in RUBRIC},
                "chroma_rank": r.get("chroma_rank"), "chroma_sim": r.get("chroma_sim")} for k, r in enumerate(keep, 1)]
    _jsonl_write(os.path.join(out, "golden_questions_v1.jsonl"), outrows)
    print(f"golden_questions_v1.jsonl: {len(outrows)}개 / 후보 {len(rows)}개 {dict(Counter(r['verdict'] for r in rows))}")


# ---------------- 정답 여러 개로 확장 (조정자 지시 2026-11-12) ----------------
EXTRA_JUDGE = 3          # 경쟁 조각 외에 더 확인할 근접 조각 수(정답 유사도 − AMBIG_MARGIN 이상인 것 중 상위)
MAX_GOLD = 3             # 정답이 이 개수를 넘고 그중 다른 문서 조각이 있으면 '너무 일반적인 질의'로 버림
YESNO_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}, "reason": {"type": "string"}},
                "required": ["answer", "reason"]}
YESNO_SYS = """너는 검색 정답 판정관이다. 검색 질의 하나와 사내문서 조각 하나가 주어진다.
이 조각이 질의가 찾으려는 정보(구체적 절차·기준·사례)를 직접 담고 있으면 "yes", 주제만 비슷하거나 관련이 없으면 "no".
엄격하게 판정한다. 출력: JSON {"answer": "yes"|"no", "reason": 한 줄}"""


def _expand(out, model):
    """후보 질의를 '정답 여러 개' 규칙으로 다시 정리해 golden_retrieval_v1.jsonl을 다시 쓴다.

    - 모호성 판정이 안 걸린 질의(경쟁 조각이 멀리 있음) → 정답 1개 그대로
    - 판정이 걸린 질의: 기존 A/B 판정(gold/rival/both/neither)을 다시 쓰고, 근접 조각 EXTRA_JUDGE개를 하나씩 yes/no로 더 확인
      · neither → 버림(질의 자체가 원래 조각에도 안 맞음)
      · 원래 조각 + '답'으로 판정된 조각 = gold_chunk_ids
      · 정답 > MAX_GOLD 이고 다른 문서 조각이 섞이면 → 너무 일반적인 질의로 버림
    판정 결과는 retrieval_expand.jsonl에 저장(중단 후 이어서 실행 가능).
    """
    rows = _jsonl_read(os.path.join(out, "retrieval_candidates.jsonl"))
    exp_path = os.path.join(out, "retrieval_expand.jsonl")
    done = {(r["chunk_id"], r["query"]): r for r in _jsonl_read(exp_path)}
    ids, docs, metas, E = _load_public()
    idx = {cid: i for i, cid in enumerate(ids)}
    final = []
    for r in rows:
        if r["verdict"] not in ("keep", "drop_ambiguous"):
            continue
        key = (r["chunk_id"], r["query"])
        if "ambig_judge" not in r:
            final.append({**r, "gold": [r["chunk_id"]], "gold_type": "single", "exp_verdict": "keep"})
            continue
        if key not in done:
            gi = idx[r["chunk_id"]]
            v = _embed1(r["query"])
            sims = E @ v
            near = [int(j) for j in np.argsort(-sims) if j != gi and sims[j] >= sims[gi] - AMBIG_MARGIN]
            near = [j for j in near if ids[j] != r.get("rival_id")][:EXTRA_JUDGE]
            extra = {}
            if r["ambig_judge"] != "neither":
                for j in near:
                    res, _ = _call(YESNO_SYS, f"[질의]\n{r['query']}\n\n[조각]\n{_body(docs[j])[:1100]}", YESNO_SCHEMA, model,
                                   temperature=0.0, num_ctx=4096)
                    extra[ids[j]] = res.get("answer", "error")
            rec = {"chunk_id": r["chunk_id"], "query": r["query"], "extra": extra, "near_n": len(near)}
            _jsonl_append(exp_path, rec)
            done[key] = rec
            print(f"expand {len(done)} {r['chunk_id']} judge={r['ambig_judge']} extra={extra}", flush=True)
        rec = done[key]
        if r["ambig_judge"] == "neither":
            final.append({**r, "gold": [], "gold_type": "-", "exp_verdict": "drop_neither"})
            continue
        gold = [r["chunk_id"]]
        if r["ambig_judge"] in ("rival", "both") and r.get("rival_id"):
            gold.append(r["rival_id"])
        gold += [c for c, ans in rec["extra"].items() if ans == "yes"]
        other_doc = any(metas[idx[c]]["doc_id"] != r["doc_id"] for c in gold)
        if len(gold) > MAX_GOLD and other_doc:
            final.append({**r, "gold": gold, "gold_type": "multi", "exp_verdict": "drop_generic"})
            continue
        final.append({**r, "gold": gold, "gold_type": "multi" if len(gold) > 1 else "single", "exp_verdict": "keep"})
    keep = [f for f in final if f["exp_verdict"] == "keep"]
    outrows = [{"id": f"GR-{k:04d}", "query": f["query"], "gold_chunk_ids": f["gold"], "doc_id": f["doc_id"],
                "gold_doc_ids": sorted({metas[idx[c]]["doc_id"] for c in f["gold"]}), "role_hint": f["role_hint"],
                "style": f.get("style"), "gold_type": f["gold_type"], "source_chunk_id": f["chunk_id"],
                "gold_sim": f.get("gold_sim"), "gold_rank_raw": f.get("gold_rank")} for k, f in enumerate(keep, 1)]
    _jsonl_write(os.path.join(out, "golden_retrieval_v1.jsonl"), outrows)
    stats = dict(Counter(f["exp_verdict"] for f in final))
    stats.update({"single": sum(o["gold_type"] == "single" for o in outrows), "multi": sum(o["gold_type"] == "multi" for o in outrows),
                  "extra_judged": sum(len(d["extra"]) for d in done.values()),
                  "extra_yes": sum(1 for d in done.values() for x in d["extra"].values() if x == "yes")})
    json.dump(stats, open(os.path.join(out, "expand_stats.json"), "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print("expand:", stats)
    return outrows


def cmd_expand(a):
    _expand(a.out, a.model)


# ---------------- 지표 ----------------
def _rr(rank):
    return 1.0 / rank if rank else 0.0


def cmd_metrics(a):
    gold = _expand(a.out, a.model)         # 정답 여러 개 규칙 적용(이미 판정한 것은 건너뜀)
    ids, docs, metas, E = _load_public()
    idx = {cid: i for i, cid in enumerate(ids)}
    t0 = time.time()
    vecs = []
    for b in range(0, len(gold), 16):
        vecs.extend(llm.embed([g["query"] for g in gold[b:b + 16]]))
    neg_vecs = llm.embed(NEGATIVES)
    raw, pipe = [], {t: [] for t in MIN_SIM_GRID}
    pipe_cur = []
    for g, v in zip(gold, vecs):
        vn = np.asarray(v, dtype=np.float32)
        vn /= np.linalg.norm(vn) + 1e-12
        sims = E @ vn
        gs = set(g["gold_chunk_ids"])
        order = np.argsort(-sims)
        r = next(k for k, j in enumerate(order, 1) if ids[j] in gs)       # 정답 중 가장 높은 순위
        raw.append({"rank": r, "style": g.get("style"), "type": g["gold_type"]})
        res = internal_search.search(v, role_code=g["role_hint"], min_sim=-1.0)   # 하한 없이 한 번 → 아래에서 하한별로 다시
        for t in MIN_SIM_GRID:
            out = internal_search.search(v, role_code=g["role_hint"], min_sim=t)
            hids = [h["id"] for h in out["hits"]]
            rank = next((k for k, h in enumerate(hids, 1) if h in gs), 0)
            noise = sum(1 for h in out["hits"] if h["id"] not in gs and h["metadata"]["doc_id"] not in g["gold_doc_ids"])
            pipe[t].append({"rank": rank, "n": len(hids), "noise": noise})
        hids = [h["id"] for h in res["hits"]]
        pipe_cur.append({"rank": next((k for k, h in enumerate(hids, 1) if h in gs), 0), "type": g["gold_type"]})
    neg = {t: [] for t in MIN_SIM_GRID}
    neg_top = []
    for v in neg_vecs:
        vn = np.asarray(v, dtype=np.float32)
        vn /= np.linalg.norm(vn) + 1e-12
        neg_top.append(float((E @ vn).max()))
        for t in MIN_SIM_GRID:
            neg[t].append(len(internal_search.search(v, min_sim=t)["hits"]))

    N = len(gold)

    def raw_stats(rows):
        n = len(rows) or 1
        return {"n": len(rows), "recall@1": round(sum(r["rank"] == 1 for r in rows) / n, 4),
                "recall@4": round(sum(r["rank"] <= 4 for r in rows) / n, 4),
                "recall@12": round(sum(r["rank"] <= 12 for r in rows) / n, 4),
                "mrr@12": round(sum(_rr(r["rank"]) if r["rank"] <= 12 else 0 for r in rows) / n, 4)}

    table = []
    for t in MIN_SIM_GRID:
        P = pipe[t]
        tot = sum(p["n"] for p in P)
        table.append({"min_sim": t, "recall@4": round(sum(p["rank"] > 0 for p in P) / N, 4),
                      "mrr@4": round(sum(_rr(p["rank"]) for p in P) / N, 4),
                      "empty_rate": round(sum(p["n"] == 0 for p in P) / N, 4),
                      "avg_hits": round(tot / N, 2),
                      "noise_ratio": round(sum(p["noise"] for p in P) / tot, 4) if tot else None,
                      "neg_pass_rate": round(sum(x > 0 for x in neg[t]) / len(NEGATIVES), 4),
                      "neg_avg_hits": round(sum(neg[t]) / len(NEGATIVES), 2)})
    M = {"n_gold": N, "raw_vector": raw_stats(raw),
         "raw_by_style": {s: raw_stats([r for r in raw if r["style"] == s]) for s in ("sentence", "keywords")},
         "raw_by_type": {t: raw_stats([r for r in raw if r["type"] == t]) for t in ("single", "multi")},
         "pipeline_no_floor": {"recall@4": round(sum(r["rank"] > 0 for r in pipe_cur) / N, 4),
                               "mrr@4": round(sum(_rr(r["rank"]) for r in pipe_cur) / N, 4),
                               "by_type": {t: {"n": sum(r["type"] == t for r in pipe_cur),
                                               "recall@4": round(sum(r["rank"] > 0 for r in pipe_cur if r["type"] == t) / max(1, sum(r["type"] == t for r in pipe_cur)), 4),
                                               "mrr@4": round(sum(_rr(r["rank"]) for r in pipe_cur if r["type"] == t) / max(1, sum(r["type"] == t for r in pipe_cur)), 4)}
                                           for t in ("single", "multi")}},
         "current_min_sim": internal_search.MIN_SIM, "min_sim_table": table,
         "neg_top_sim": {"min": round(min(neg_top), 4), "mean": round(float(np.mean(neg_top)), 4), "max": round(max(neg_top), 4)},
         "gold_sim": {"p10": round(float(np.percentile([g["gold_sim"] for g in gold], 10)), 4),
                      "median": round(float(np.median([g["gold_sim"] for g in gold])), 4)},
         "seconds": round(time.time() - t0, 1)}
    json.dump(M, open(os.path.join(a.out, "metrics.json"), "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(json.dumps(M, ensure_ascii=False, indent=1))


# ---------------- 보고서 ----------------
def cmd_report(a):
    rc = _jsonl_read(os.path.join(a.out, "retrieval_candidates.jsonl"))
    qc = _jsonl_read(os.path.join(a.out, "questions_candidates.jsonl"))
    M = json.load(open(os.path.join(a.out, "metrics.json"), encoding="utf-8"))
    meta = json.load(open(os.path.join(a.out, "run_meta.json"), encoding="utf-8")) if os.path.exists(os.path.join(a.out, "run_meta.json")) else {}
    wall = {}
    wp = os.path.join(a.out, "wall.txt")
    if os.path.exists(wp):
        for line in open(wp, encoding="utf-8"):
            k, v = line.split()
            wall[k] = int(v)
    if {"retrieval_start", "retrieval_end"} <= wall.keys():
        meta["retrieval_minutes"] = round((wall["retrieval_end"] - wall["retrieval_start"]) / 60, 1)
    if {"retrieval_end", "questions_end"} <= wall.keys():
        meta["questions_minutes"] = round((wall["questions_end"] - wall["retrieval_end"]) / 60, 1)
    model_sec = sum((r.get("gen_sec") or 0) + (r.get("judge_sec") or 0) for r in rc) + \
        sum((r.get("gen_sec") or 0) + (r.get("judge_sec") or 0) for r in qc)
    cost = _usage(a.out)
    ex = json.load(open(os.path.join(a.out, "expand_stats.json"), encoding="utf-8")) if os.path.exists(os.path.join(a.out, "expand_stats.json")) else {}
    rv, qv = Counter(r["verdict"] for r in rc), Counter(r["verdict"] for r in qc)
    chunks = len({r["chunk_id"] for r in rc})
    amb = [r for r in rc if "ambig_judge" in r]
    gk = _jsonl_read(os.path.join(a.out, "golden_retrieval_v1.jsonl"))
    qk = _jsonl_read(os.path.join(a.out, "golden_questions_v1.jsonl"))
    L = [f"# 사내문서 RAG 골든셋 v1 — {a.date}", "",
         "자동 생성(사람 확인 없음). 스크립트 `backend/tools/build_golden_sets.py`. 원본 = Chroma `internal_docs_public`"
         f"({internal_store.count(internal_store.PUBLIC)}조각 · 12문서, 가상 회사 헤드헌터랩, 지원자 데이터 없음).", "",
         "## 1. 사용 모델·시간", "",
         "| 항목 | 값 |", "|---|---|",
         f"| 생성 모델(검색 질의·G-질문) | `{meta.get('gen_model', '?')}` (`llm.openai_chat_json`, 2026-11-12 사용자 결정, 가상 사내문서 public 조각만 전송) |",
         f"| 판정 모델(모호성·질문 품질) | `{meta.get('judge_model', '?')}` (로컬 Ollama, `llm.chat_json`, 순차 호출 1개) |",
         f"| OpenAI 사용량 | {cost['text']} |",
         f"| 임베딩 | `{config.EMBED_MODEL}` (로컬, `llm.embed`) |",
         f"| G-검색 걸린 시간 | {meta.get('retrieval_minutes', '?')}분 |",
         f"| G-질문 걸린 시간 | {meta.get('questions_minutes', '?')}분 |",
         f"| 지표 계산 | {M['seconds']}초 |",
         f"| 모델 호출 시간 합(조각당 생성 시간이 질의마다 중복 기록돼 약간 과대) | 약 {model_sec / 60:.0f}분 |", "",
         "## 2. G-검색 (`golden_retrieval_v1.jsonl`)", "",
         f"- 대상 조각: {chunks}개{meta.get('retrieval_sample_note', '')}. 조각마다 질의 {meta.get('nq', 2)}개(문장형 {meta.get('nq', 2) - 1} · 키워드형 1), role_hint = 조각 `dept` 중 하나.",
         "- 4B 생성으로 먼저 돌린 14조각·8B 파일럿 1조각은 생성 모델을 하나로 맞추려고 버렸다(결과에 없음).",
         f"- 필터 ① 너무 쉬움: 질의 문자 3-gram 중 조각에 있는 비율 > {OVERLAP_MAX} 또는 공백 뺀 공통 연속 문자열 ≥ {LCS_MAX}자.",
         f"- 필터 ② 모호함: public 전체와 코사인 비교, 정답 외 최상위 조각 유사도 ≥ 정답 − {AMBIG_MARGIN}이면 판정 모델에 A/B(순서 무작위) 블라인드로 묻는다. "
         f"그다음 근접 조각(정답 − {AMBIG_MARGIN} 이상) {EXTRA_JUDGE}개를 하나씩 yes/no로 더 확인한다(조정자 지시 2026-11-12 — 모호 질의는 버리지 않고 **정답 여러 개**로 남김).",
         f"  · '둘 다 아님' → 탈락 · 경쟁 조각·근접 조각 중 '답'인 것은 gold_chunk_ids에 추가 · 정답이 {MAX_GOLD}개를 넘고 다른 문서 조각이 섞이면 '너무 일반적'으로 탈락.",
         "  · Recall·MRR은 정답 중 하나라도 맞으면 맞은 것(가장 높은 순위의 정답 기준).", "",
         "| 단계 | 개수 |", "|---|---|",
         f"| 생성된 질의 | {sum(v for k, v in rv.items() if k != 'drop_gen_error')} |",
         f"| 생성 실패(조각) | {rv.get('drop_gen_error', 0)} |",
         f"| 필터 ① 탈락 | {rv.get('drop_easy', 0)} |",
         f"| 필터 ② 판정 대상 | {len(amb)} (정답 {sum(r['ambig_judge'] == 'gold' for r in amb)} · 경쟁 {sum(r['ambig_judge'] == 'rival' for r in amb)} · "
         f"둘 다 {sum(r['ambig_judge'] == 'both' for r in amb)} · 둘 다 아님 {sum(r['ambig_judge'] == 'neither' for r in amb)} · 오류 {sum(r['ambig_judge'] not in ('gold', 'rival', 'both', 'neither') for r in amb)}) |",
         f"| 필터 ② 정답 여러 개로 전환 | {ex.get('multi', 0)} (근접 조각 추가 판정 {ex.get('extra_judged', 0)}회 중 '답' {ex.get('extra_yes', 0)}) |",
         f"| 필터 ② 탈락 — 둘 다 아님 | {ex.get('drop_neither', 0)} |",
         f"| 필터 ② 탈락 — 너무 일반적(정답 > {MAX_GOLD}, 다른 문서 포함) | {ex.get('drop_generic', 0)} |",
         f"| 임베딩 오류 | {rv.get('drop_error', 0)} |",
         f"| **최종** | **{len(gk)}** (정답 1개 {ex.get('single', 0)} · 정답 여러 개 {ex.get('multi', 0)} · 문장형 {sum(g.get('style') == 'sentence' for g in gk)} · 키워드형 {sum(g.get('style') == 'keywords' for g in gk)} · "
         f"문서 {len({g['doc_id'] for g in gk})}개) |", "",
         "### 2.1 검색 지표 (최종 골든셋 기준)", "",
         "| 방식 | Recall@1 | Recall@4 | MRR |", "|---|---|---|---|",
         f"| 원시 벡터 검색(public 전체, 코사인) | {M['raw_vector']['recall@1']} | {M['raw_vector']['recall@4']} | {M['raw_vector']['mrr@12']} (@12) |",
         f"| 제품 파이프라인 `internal_search.search`(재순위·문서당 2·상위 4, 하한 없음) | — | {M['pipeline_no_floor']['recall@4']} | {M['pipeline_no_floor']['mrr@4']} (@4) |",
         *[f"| 원시 · {s} | {v['recall@1']} | {v['recall@4']} | {v['mrr@12']} | " for s, v in M["raw_by_style"].items()],
         *[f"| 원시 · 정답 {'1개' if t == 'single' else '여러 개'} (n={v['n']}) | {v['recall@1']} | {v['recall@4']} | {v['mrr@12']} | " for t, v in M["raw_by_type"].items()],
         *[f"| 파이프라인 · 정답 {'1개' if t == 'single' else '여러 개'} (n={v['n']}) | — | {v['recall@4']} | {v['mrr@4']} | " for t, v in M["pipeline_no_floor"]["by_type"].items()],
         "", f"정답 조각 유사도 p10 {M['gold_sim']['p10']} · 중앙값 {M['gold_sim']['median']}. "
         f"무관 질의 {len(NEGATIVES)}개의 최고 유사도 최소 {M['neg_top_sim']['min']} · 평균 {M['neg_top_sim']['mean']} · 최대 {M['neg_top_sim']['max']}.", "",
         "### 2.2 MIN_SIM 후보별 (제품 파이프라인, 현재값 " + str(M["current_min_sim"]) + ")", "",
         "| MIN_SIM | Recall@4 | MRR@4 | 빈 결과율 | 평균 조각 수 | 노이즈 비율 | 무관 질의 통과율 |", "|---|---|---|---|---|---|---|",
         *[f"| {r['min_sim']} | {r['recall@4']} | {r['mrr@4']} | {r['empty_rate']} | {r['avg_hits']} | {r['noise_ratio']} | {r['neg_pass_rate']} |"
           for r in M["min_sim_table"]], "",
         "- 노이즈 비율 = 반환 조각 중 정답 문서가 아닌 문서의 조각 비율(다른 문서 조각이 꼭 틀린 것은 아니므로 상한 추정치).",
         "- 무관 질의 통과율 = 사내문서와 관계없는 고정 질의 20개 중 조각이 1개라도 나온 비율.", "",
         "## 3. G-질문 (`golden_questions_v1.jsonl`)", "",
         f"- public 문서 12개마다 서로 떨어진 조각 {meta.get('per_doc', 3)}개(300자 이상)를 골라 조각당 회사 맞춤 질문 1개 생성 → "
         f"`judge_question_quality.md` [quality] 루브릭 5항목 평균 ≥ {Q_KEEP} 그리고 grounded=true만 유지.", "",
         "| 단계 | 개수 |", "|---|---|",
         f"| 후보 | {len(qc)} |", f"| 생성 실패 | {qv.get('drop_gen_error', 0)} |", f"| 판정 오류 | {qv.get('drop_judge_error', 0)} |",
         f"| 근거 없음(grounded=false) | {qv.get('drop_ungrounded', 0)} |", f"| 점수 미달(< {Q_KEEP}) | {qv.get('drop_low_score', 0)} |",
         f"| **최종** | **{len(qk)}** (문서 {len({q['doc_id'] for q in qk})}개) |", "",
         "## 4. 한계", "",
         f"- 생성(`{meta.get('gen_model', '?')}`)과 판정(`{meta.get('judge_model', '?')}`)을 다른 모델로 나눠 자기 평가 편향을 줄였다. "
         "다만 판정이 소형 4B라 루브릭 구분력이 낮을 수 있고(후한 점수·모호성 판정 오류), 4.5 하한 통과율은 판정 모델이 바뀌면 달라진다. 사람 검토 없음.",
         "- 생성 모델이 더 큰 모델이라 질의가 제품 실제 질의(공고·이력서 키워드)보다 정제돼 있을 수 있다.",
         "- 필터 ②는 경쟁 조각이 가까운 질의만 판정하므로, 경쟁 조각이 정답보다 확실히 높게 나와도 판정이 '정답'이면 남는다(검색 실패 사례로 골든셋에 포함).",
         f"  근접 조각 확인은 상위 {EXTRA_JUDGE}개까지만이라, 그 밖의 '답이 되는 조각'은 정답에 빠져 있을 수 있다(Recall은 보수적).",
         "- 제품의 실제 질의는 공고·이력서 키워드로 만든 벡터(그래프 1-A `retrieve`)이고, 이 셋의 질의는 사람이 쓴 듯한 검색어다. MIN_SIM 판단 때 이 차이를 감안한다.",
         "- 정답 1개 질의는 원래 조각만 정답(근접 조각이 멀어 판정하지 않음)이므로 Recall은 보수적인 값이다.", ""]
    path = os.path.join(a.out, f"golden_sets_{a.date}.md")
    open(path, "w", encoding="utf-8", newline="\n").write("\n".join(L))
    print(path)


def _usage(out):
    rows = _jsonl_read(os.path.join(out, "openai_usage.jsonl"))
    pi, po = sum(r.get("prompt_tokens", 0) for r in rows), sum(r.get("completion_tokens", 0) for r in rows)
    pin, pout = getattr(config, "OPENAI_PRICE_IN_PER_M", 0) or 0, getattr(config, "OPENAI_PRICE_OUT_PER_M", 0) or 0
    usd = (pi * pin + po * pout) / 1e6
    price = f" · 약 ${usd:.2f}" if (pin or pout) else " · USD 환산 불가(`OPENAI_PRICE_IN/OUT_PER_M` 미설정)"
    return {"calls": len(rows), "prompt": pi, "completion": po, "usd": usd if (pin or pout) else None,
            "text": f"호출 {len(rows)}회 · 입력 {pi:,} 토큰 · 출력 {po:,} 토큰{price}"}


DOC_TYPE_KO = {"talent": "인재상", "jd": "직무기술서(JD)", "project": "프로젝트", "hr": "인사 규정", "attendance": "근태 규정"}


def cmd_sample(a):
    """G-질문 대표 표본 → G_questions_sample_v1.md (questionbank/G_set/에 복사해 쓴다).

    고르는 순서: ① Chroma 검색에서 근거 조각이 상위 4위 안(질문 문장만으로 근거가 다시 찾아지는 것)인 통과 질문만
    ② 문서마다 1개(루브릭 평균 → Chroma 유사도 순) ③ 모자라면 직무·문서 유형이 덜 나온 쪽부터 같은 문서의 다른 섹션으로 채움(최대 a.n개).
    """
    gq = _jsonl_read(os.path.join(a.out, "golden_questions_v1.jsonl"))
    ids, docs, metas, _ = _load_public()
    text = {cid: _body(d) for cid, d in zip(ids, docs)}
    pool = [q for q in gq if q.get("chroma_rank") and q["chroma_rank"] <= 4]
    key = lambda q: (-q["rubric_mean"], -(q.get("chroma_sim") or 0))  # noqa: E731
    picked, seen_doc = [], set()
    for q in sorted(pool, key=key):
        if q["doc_id"] not in seen_doc:
            picked.append(q)
            seen_doc.add(q["doc_id"])
    rest = [q for q in sorted(pool, key=key) if q not in picked]
    while len(picked) < a.n and rest:
        rc, tc = Counter(q["role_hint"] for q in picked), Counter(q.get("doc_type") for q in picked)
        dc, sc = Counter(q["doc_id"] for q in picked), {(q["doc_id"], q.get("sec_title")) for q in picked}
        rest = [q for q in rest if (q["doc_id"], q.get("sec_title")) not in sc]
        if not rest:
            break
        nxt = min(rest, key=lambda q: (dc[q["doc_id"]], rc[q["role_hint"]], tc[q.get("doc_type")], key(q)))
        picked.append(nxt)
        rest.remove(nxt)
    picked.sort(key=lambda q: (q.get("doc_type") or "", q["doc_id"], q["id"]))
    meta = json.load(open(os.path.join(a.out, "run_meta.json"), encoding="utf-8")) if os.path.exists(os.path.join(a.out, "run_meta.json")) else {}
    model, jmodel, cost = meta.get("gen_model", "?"), meta.get("judge_model", "?"), _usage(a.out)
    n_cand = len(_jsonl_read(os.path.join(a.out, "questions_candidates.jsonl")))
    L = ["---", "version: v1", f"date: {a.date}", "---", "", "# G-질문 대표 표본 v1 (사내문서 Chroma 근거)", "",
         f"- 생성일: {a.date}",
         f"- 생성 방법: Chroma `internal_docs_public`({len(ids)}조각 · {len({m['doc_id'] for m in metas})}문서, 가상 회사 헤드헌터랩) → "
         f"문서마다 서로 떨어진 조각 {meta.get('per_doc', '?')}개 → 조각당 회사 맞춤 질문 1개를 `{model}`로 생성(`llm.openai_chat_json`, 가상 사내문서 조각만 전송) → "
         f"`backend/app/prompts/evaluate/judge_question_quality.md` [quality] 루브릭 판정 → 5항목 평균 ≥ {Q_KEEP} 그리고 grounded=true만 통과 → "
         "질문 문장을 로컬 `qwen3-embedding:0.6b`로 임베딩해 Chroma public을 다시 검색(제품과 같은 disclosure·status 필터), 근거 조각이 상위 4위 안인 것만 표본 후보.",
         f"- 근거는 **Chroma 사내문서 조각만** 쓴다. Redis 질문 은행 문항은 섞지 않았다.",
         f"- 전체: 후보 {n_cand}개 → 판정 통과 {len(gq)}개 → Chroma 재검색 상위 4위 안 {len(pool)}개 → **표본 {len(picked)}개**.",
         f"- 표본 고르는 기준: 문서마다 1개 먼저(public 문서가 {len({m['doc_id'] for m in metas})}개뿐이라 문서당 1개로는 최대 그 개수), "
         "모자라면 직무·문서 유형이 덜 나온 쪽부터 같은 문서의 **다른 섹션** 질문으로 채움.",
         f"- 생성 모델: `{model}` / 판정 모델: `{jmodel}`(로컬) / 임베딩: `{config.EMBED_MODEL}`(로컬). "
         "생성과 판정을 다른 모델로 나눠 자기 평가 편향을 줄였지만, 4B 판정의 구분력 한계는 남는다(사람 검토 없음).",
         f"- OpenAI 사용량(G-검색 + G-질문 생성 전체): {cost['text']}",
         "- 전체 원본: `documents/03_QA_doc/eval_runs/golden_questions_v1.jsonl` (방법·지표: 같은 폴더 `golden_sets_" + a.date + ".md`)",
         "- Chroma 유사도 = 1 − 코사인 거리(질문 문장 ↔ 근거 조각). 순위 = 질문 문장으로 검색했을 때 근거 조각의 순위.", "",
         "| 직무 | 개수 |", "|---|---|", *[f"| {ROLE_NAME.get(k, k)} | {v} |" for k, v in sorted(Counter(q['role_hint'] for q in picked).items())], "",
         "| 문서 유형 | 개수 |", "|---|---|", *[f"| {DOC_TYPE_KO.get(k, k)} | {v} |" for k, v in sorted(Counter(q.get('doc_type') for q in picked).items())], ""]
    for q in picked:
        cid = q["gold_chunk_ids"][0]
        body = [x.strip() for x in text.get(cid, "").splitlines() if x.strip() and not x.startswith("#")]
        exc = []
        for line in body:
            exc.append(line if len(line) <= 160 else line[:160] + "…")
            if len(exc) >= 3:
                break
        sc = q["scores"]
        L += [f"## {q['id']} · {ROLE_NAME.get(q['role_hint'], q['role_hint'])} · {DOC_TYPE_KO.get(q.get('doc_type'), q.get('doc_type'))}", "",
              f"- 근거: `{q['doc_id']}` / 조각 `{cid}` / 섹션 '{q.get('sec_title') or ''}'",
              f"- Chroma 유사도: {q.get('chroma_sim')} (검색 순위 {q.get('chroma_rank')}위)",
              f"- 직무: {ROLE_NAME.get(q['role_hint'], q['role_hint'])} (`{q['role_hint']}`)",
              "- 근거 조각 발췌:", "", *[f"  > {x}" for x in exc], "",
              f"- **기준 질문**: {q['question']}",
              f"- 판정: 명확성 {sc['clarity']} · 직무 관련성 {sc['job_relevance']} · 답변 가능성 {sc['answerability']} · "
              f"한 문장 {sc['one_sentence']} · 비편향 {sc['unbiased']} → **평균 {q['rubric_mean']}**", ""]
    path = os.path.join(a.out, "G_questions_sample_v1.md")
    open(path, "w", encoding="utf-8", newline="\n").write("\n".join(L))
    print(path, len(picked))


def main():
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest="cmd", required=True)
    r = s.add_parser("retrieval")
    r.add_argument("--out", required=True)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--per-doc", type=int, default=0, help="문서마다 고르게 떨어진 조각 n개만(0=전부)")
    r.add_argument("--gen", choices=["openai", "local"], default="openai")
    r.add_argument("--nq", type=int, default=2, help="조각당 질의 수(2~3, 마지막 1개는 키워드형)")
    r.add_argument("--model", default="qwen3:4b")
    r.add_argument("--seed", type=int, default=20261112)
    q = s.add_parser("questions")
    q.add_argument("--out", required=True)
    q.add_argument("--per-doc", type=int, default=3)
    q.add_argument("--gen", choices=["openai", "local"], default="openai")
    q.add_argument("--model", default="qwen3:4b")
    m = s.add_parser("metrics")
    m.add_argument("--out", required=True)
    m.add_argument("--model", default="qwen3:4b")
    e = s.add_parser("expand")
    e.add_argument("--out", required=True)
    e.add_argument("--model", default="qwen3:4b")
    rp = s.add_parser("report")
    rp.add_argument("--out", required=True)
    rp.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    sp = s.add_parser("sample")
    sp.add_argument("--out", required=True)
    sp.add_argument("--n", type=int, default=20)
    sp.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    a = p.parse_args()
    {"retrieval": cmd_retrieval, "questions": cmd_questions, "metrics": cmd_metrics, "report": cmd_report,
     "sample": cmd_sample, "expand": cmd_expand}[a.cmd](a)


if __name__ == "__main__":
    main()
