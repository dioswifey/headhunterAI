"""RAG 면접 질문 생성 품질 — 확률적 측정 (지표 M-31~M-37, 2026-11-12).

제품 코드는 고치지 않는다. 질문 생성 그래프 1-A(`agents/question_graph.GENERATION`)를 **DB에 쓰지 않고** 그대로 돌린다
(그래프의 질문 생성 경로는 공고 SELECT만 하고, 저장은 questions.pregenerate가 하므로 그 함수를 부르지 않는다).
지원자 데이터는 쓰지 않는다 — 시드 공고(헤드헌터랩 테스트 기업 3곳 × 3직무) + 아래의 **가상 인물** 이력서 스냅샷만.

모든 모델 호출은 llm.chat_json / llm.embed(로컬 Ollama). 판정 프롬프트 = prompts/evaluate/judge_question_quality.md.

    python -m tools.eval_rag_questions generate --out /tmp/rageval/gen.json            # 질문 세트 생성(생성 모델 = LLM_MODEL)
    python -m tools.eval_rag_questions judge    --gen /tmp/rageval/gen.json --out /tmp/rageval/judged.json --model qwen3:8b
    python -m tools.eval_rag_questions internal --out /tmp/rageval/internal.json --model qwen3:8b   # M-36 사내문서 검색
    python -m tools.eval_rag_questions report   --dir /tmp/rageval --name rag_questions_2026-11-12

단계마다 결과를 파일에 쓰고, judge·internal은 이미 판정한 항목을 건너뛴다(중단 후 이어서 실행 가능).

통계: 평균·표준편차, 평균의 95% CI = 세트 단위 군집 부트스트랩(같은 세트 질문끼리 상관이 있으므로 세트를 다시 뽑는다, B=5000),
비율의 95% CI = Wilson 구간(관측 단위 독립 가정 — 세트 내 상관이 있으면 실제 폭은 더 넓다).
"""
import argparse
import json
import math
import os
import random
import re
import sys
import time
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from app import config, db, llm, questions as Q  # noqa: E402

PROMPT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "prompts", "evaluate",
                           "judge_question_quality.md")
CAT_LABEL = {"personality": "인성 질문", "resume": "이력서 기반 질문", "job": "직무 이해도 질문", "company": "기업(회사 맞춤) 질문"}
EXPECTED = ["personality"] * 2 + ["resume"] * 3 + ["job"] * 2 + ["company"]
DUP_COS = 0.85
RUBRIC = ("clarity", "job_relevance", "answerability", "one_sentence", "unbiased")
BASELINE_DATE = "2026-11-12"   # 첫 기준선 실행일(rag_questions_2026-11-12.json)


def _run_info(name):
    """--name(예: rag_questions_2026-11-13_fix1) → (날짜, 꼬리표). 이름에 날짜가 없으면 오늘(KST)."""
    m = re.search(r"\d{4}-\d{2}-\d{2}", name or "")
    if m:
        return m.group(0), (name[m.end():].strip("_-") or "")
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 9 * 3600)), ""


def _run_note(date, tag):
    if date == BASELINE_DATE and not tag:
        return f"첫 기준선({BASELINE_DATE}). 제품 코드·프롬프트는 고치지 않고 그대로 기록"
    return f"재측정({tag or date}) — 기준선({BASELINE_DATE}) 대비 비교는 md '기준선 대비 전후 비교' 절(compare)"

# ---------------- 가상 인물 이력서 스냅샷 (실존 인물 아님) ----------------
def _p(name, role, period, detail, tech):
    return {"name": name, "role": role, "period": period, "detail": detail, "tech": tech, "page": 1, "source": detail}


def _c(company, role, period, detail, tech):
    return {"company": company, "role": role, "period": period, "detail": detail, "tech": tech, "page": 1, "source": detail}


def _e(school, major, period):
    return {"school": school, "major": major, "degree": "학사", "period": period, "detail": "", "page": 1, "source": f"{school} {major}"}


def _snap(name, edu, career, projects, skills, certs=()):
    return {"basic": {"name": name, "email": "", "phone": ""}, "education": edu, "career": career, "projects": projects,
            "skills": list(skills), "certificates": [{"name": c, "date": "", "page": 1, "source": c} for c in certs]}


RESUMES = {
    "ai_ax": [
        ("가상_AI_경력3년", _snap("가상지원자A1", [_e("가상대학교", "컴퓨터공학", "2015-2019")],
            [_c("가상테크", "ML 엔지니어", "2021-2024", "사내 문서 검색 챗봇을 RAG 구조로 개발해 상담 응답 시간을 30% 줄임", ["Python", "LangChain", "FAISS", "FastAPI"])],
            [_p("상담 로그 의도 분류", "모델 개발", "2023", "상담 로그 12만 건으로 의도 분류 모델을 학습해 정확도 87% 달성", ["PyTorch", "BERT"]),
             _p("답변 품질 평가셋", "평가 설계", "2024", "RAG 답변 평가셋 500문항을 만들고 회귀 평가를 자동화", ["Python", "pytest"])],
            ["Python", "PyTorch", "LangChain", "Docker"])),
        ("가상_AI_신입", _snap("가상지원자A2", [_e("가상과학기술대", "인공지능학", "2020-2025")], [],
            [_p("교내 수업 Q&A 봇", "팀장", "2024", "강의 자료 PDF를 임베딩해 질문에 답하는 Q&A 봇을 만들고 학생 40명이 사용", ["Python", "OpenSearch", "Streamlit"]),
             _p("불량 이미지 분류", "모델링", "2023", "공개 데이터셋으로 CNN 분류 모델을 학습해 F1 0.91 기록", ["PyTorch", "OpenCV"])],
            ["Python", "PyTorch", "Git"], ["정보처리기사"])),
        ("가상_AI_비전경력", _snap("가상지원자A3", [_e("가상공과대학", "전자공학", "2012-2018")],
            [_c("가상팩토리솔루션", "비전 엔지니어", "2019-2025", "PCB 외관 검사 YOLO 모델을 개발하고 엣지 장비에 TensorRT로 배포해 추론 속도 2배 개선", ["YOLO", "TensorRT", "ONNX", "Python"])],
            [_p("업무 문서 자동 분류", "개발", "2025", "사내 결재 문서를 LLM으로 자동 분류하는 파이프라인을 만들어 수작업을 하루 2시간 줄임", ["Python", "Ollama"])],
            ["Python", "PyTorch", "TensorRT", "Linux"])),
    ],
    "backend": [
        ("가상_BE_경력4년", _snap("가상지원자B1", [_e("가상대학교", "소프트웨어학", "2013-2019")],
            [_c("가상커머스", "백엔드 개발자", "2020-2024", "주문 API를 Spring으로 개발하고 슬로우 쿼리를 튜닝해 응답 시간을 400ms에서 120ms로 줄임", ["Java", "Spring", "MySQL", "Redis"])],
            [_p("결제 웹훅 재처리", "설계·개발", "2023", "결제사 웹훅 실패 건을 Kafka 재처리 큐로 옮겨 누락 0건 유지", ["Kafka", "Spring"])],
            ["Java", "Spring", "MySQL", "Docker", "Kubernetes"])),
        ("가상_BE_경력2년", _snap("가상지원자B2", [_e("가상정보대학", "컴퓨터과학", "2016-2021")],
            [_c("가상IoT랩", "서버 개발자", "2022-2024", "센서 데이터 수집 서버를 FastAPI 비동기로 만들고 초당 3천 건 처리", ["Python", "FastAPI", "TimescaleDB", "MQTT"])],
            [_p("장비 상태 조회 API", "개발", "2024", "시계열 조회 API에 캐시를 붙여 대시보드 로딩을 3초에서 0.8초로 개선", ["FastAPI", "Redis"])],
            ["Python", "FastAPI", "PostgreSQL", "Linux"])),
        ("가상_BE_신입", _snap("가상지원자B3", [_e("가상국립대", "컴퓨터공학", "2019-2025")], [],
            [_p("배달 경로 추천 서버", "백엔드", "2024", "PostgreSQL과 PostGIS로 배달 거리 계산 API를 만들고 테스트 커버리지 80% 유지", ["Python", "Django", "PostGIS"]),
             _p("동아리 출석 서비스", "개발", "2023", "REST API와 JWT 인증을 구현해 회원 150명이 사용", ["Node.js", "Express"])],
            ["Python", "Django", "PostgreSQL", "Git"])),
    ],
    "data_sci": [
        ("가상_DA_경력3년", _snap("가상지원자D1", [_e("가상대학교", "통계학", "2014-2018")],
            [_c("가상리테일", "데이터 분석가", "2020-2024", "주간 매출 대시보드를 SQL과 Tableau로 구축하고 A/B 테스트로 쿠폰 전환율 12% 개선 확인", ["SQL", "Tableau", "Python", "pandas"])],
            [_p("재구매 예측", "모델링", "2023", "고객 재구매 여부를 LightGBM으로 예측해 AUC 0.82 달성", ["Python", "LightGBM"])],
            ["SQL", "Python", "pandas", "Tableau"], ["ADsP"])),
        ("가상_DA_신입", _snap("가상지원자D2", [_e("가상여자대학교", "산업공학", "2020-2025")], [],
            [_p("설비 이상 탐지", "분석", "2024", "공개 센서 데이터로 Isolation Forest 이상 탐지를 하고 재현율 0.78 기록", ["Python", "scikit-learn"]),
             _p("지하철 혼잡도 분석", "팀원", "2023", "공공데이터 2년치를 pandas로 정리해 시간대별 혼잡도 보고서 작성", ["Python", "pandas", "matplotlib"])],
            ["Python", "SQL", "scikit-learn"], ["SQLD"])),
        ("가상_DA_예측경력", _snap("가상지원자D3", [_e("가상경영대학", "경영정보학", "2011-2017")],
            [_c("가상로지스", "수요예측 분석가", "2018-2025", "주문량 시계열 예측 모델을 Prophet으로 만들고 MAPE를 18%에서 11%로 낮춤", ["Python", "Prophet", "SQL"])],
            [_p("운영팀 주간 리포트 자동화", "개발", "2024", "주간 물동량 리포트를 자동 생성해 작성 시간을 4시간에서 30분으로 줄임", ["Python", "Airflow"])],
            ["Python", "SQL", "Prophet", "Airflow"])),
    ],
}


RESUME_JOB = {   # 가상 이력서 → 내용이 가장 가까운 테스트 기업 공고
    "가상_AI_경력3년": "헤드헌터랩_서울", "가상_AI_신입": "헤드헌터랩_인천", "가상_AI_비전경력": "헤드헌터랩_경기",
    "가상_BE_경력4년": "헤드헌터랩_서울", "가상_BE_경력2년": "헤드헌터랩_경기", "가상_BE_신입": "헤드헌터랩_인천",
    "가상_DA_경력3년": "헤드헌터랩_서울", "가상_DA_신입": "헤드헌터랩_경기", "가상_DA_예측경력": "헤드헌터랩_인천",
}


# ---------------- 통계 ----------------
def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def boot_mean(values, clusters=None, B=5000, seed=7):
    """평균 95% CI. clusters가 있으면 군집(세트) 단위로 다시 뽑는다."""
    v = np.asarray(values, float)
    if len(v) == 0:
        return [None, None]
    rng = np.random.default_rng(seed)
    if clusters is None:
        idx = rng.integers(0, len(v), (B, len(v)))
        ms = v[idx].mean(axis=1)
    else:
        groups = {}
        for x, c in zip(v, clusters):
            groups.setdefault(c, []).append(x)
        gl = [np.asarray(g) for g in groups.values()]
        ms = np.empty(B)
        for b in range(B):
            pick = rng.integers(0, len(gl), len(gl))
            ms[b] = np.concatenate([gl[i] for i in pick]).mean()
    return [round(float(np.percentile(ms, 2.5)), 4), round(float(np.percentile(ms, 97.5)), 4)]


def boot_prop(flags, clusters, B=5000, seed=11):
    """비율의 군집 부트스트랩 CI(보조값)."""
    return boot_mean([1.0 if f else 0.0 for f in flags], clusters, B, seed)


def summ(values, clusters=None):
    v = [float(x) for x in values if x is not None]
    if not v:
        return {"n": 0}
    return {"n": len(v), "mean": round(float(np.mean(v)), 4), "sd": round(float(np.std(v, ddof=1)) if len(v) > 1 else 0.0, 4),
            "ci95": boot_mean(v, clusters if clusters is None else [c for x, c in zip(values, clusters) if x is not None])}


def prop(flags, clusters=None):
    k, n = sum(1 for f in flags if f), len(flags)
    out = {"k": k, "n": n, "p": round(k / n, 4) if n else None, "wilson95": wilson(k, n)}
    if clusters is not None and n:
        out["cluster_boot95"] = boot_prop(flags, clusters)
    return out


# ---------------- 판정 프롬프트 ----------------
def _prompt(section, **vals):
    text = open(PROMPT_PATH, encoding="utf-8").read()
    m = re.search(rf"^## \[{section}\]\s*$(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    body = m.group(1).strip()
    for k, v in vals.items():
        body = body.replace(f"[[{k}]]", str(v))
    return body


QUALITY_SCHEMA = {"type": "object", "properties": {
    **{k: {"type": "integer", "minimum": 1, "maximum": 5} for k in RUBRIC},
    "grounded": {"type": "boolean"}, "ungrounded_facts": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}},
    "required": list(RUBRIC) + ["grounded", "ungrounded_facts", "reason"]}
REL_SCHEMA = {"type": "object", "properties": {"relevance": {"type": "integer", "minimum": 1, "maximum": 5}, "reason": {"type": "string"}},
              "required": ["relevance", "reason"]}


def _judge(system, user, schema, model, temperature=0):
    t = time.time()
    for attempt in range(2):
        try:
            r = llm.chat_json(system, user, schema, timeout=300, temperature=temperature, model=model, num_ctx=4096)
            return r, round(time.time() - t, 1)
        except llm.LLMError as e:
            err = str(e)
    return {"error": err}, round(time.time() - t, 1)


# ---------------- 공고·이력서 텍스트 ----------------
def _job_text(job):
    return (f"회사: {job.get('company_name')}\n회사 소개: {job.get('company_intro') or ''}\n공고: {job.get('title')} ({job.get('role_name')})\n"
            f"주요 업무:\n{job.get('duties') or ''}\n자격 요건:\n{job.get('requirements') or ''}\n우대 사항:\n{job.get('preferred') or ''}")


def _resume_text(snap):
    lines = []
    for e in snap.get("education", []):
        lines.append(f"[학력] {e.get('school')} {e.get('major')} {e.get('period')}")
    for c in snap.get("career", []):
        lines.append(f"[경력] {c.get('company')} / {c.get('role')} / {c.get('period')} / {c.get('detail')} / 기술: {', '.join(c.get('tech') or [])}")
    for p in snap.get("projects", []):
        lines.append(f"[프로젝트] {p.get('name')} / {p.get('role')} / {p.get('period')} / {p.get('detail')} / 기술: {', '.join(p.get('tech') or [])}")
    if snap.get("skills"):
        lines.append("[보유 기술] " + ", ".join(snap["skills"]))
    for c in snap.get("certificates", []):
        lines.append(f"[자격증] {c.get('name')}")
    return "\n".join(lines)


def _jobs():
    return db.all_("""SELECT j.*, c.name company_name, r.name role_name FROM jobs j JOIN companies c ON c.id=j.company_id
                      JOIN job_roles r ON r.code=j.role_code WHERE c.name LIKE '헤드헌터랩\\_%%' ORDER BY c.name, j.role_code, j.id""")


# ---------------- 1. 생성 ----------------
def _pick_source(b):
    if b.get("doc_id"):
        return "redis"
    if b.get("char_id"):
        return "md"
    return "db"


def _is_fallback(d):
    gm = d.get("gen_method") or ""
    if d["category"] == "resume":
        return gm != "llm"                       # template · fallback_job = 대체 경로
    return not gm.startswith("redis:")           # db · character:(md 직접) = Redis 검색 실패 대체


def _is_retrieval_fallback(d):
    """M-35 주 정의 = 질문 재료가 Redis 벡터 검색에서 오지 않은 문항(Redis 실패·문항 부족).
    인성·직무·기업: gen_method가 redis:가 아니면 대체. 이력서: 참고 은행 문항(bank_doc)이 없거나 직무 질문으로 대체(fallback_job)이면서 Redis가 아니면 대체."""
    gm = d.get("gen_method") or ""
    if d["category"] == "resume":
        bank_doc = (d.get("resume_ref") or {}).get("bank_doc") or ""
        return not (bank_doc and not bank_doc.startswith("db:"))
    return not gm.startswith("redis:")


def _rule_grounded(d, job, snap):
    text = d["text"]
    if d["category"] == "resume" and d.get("gen_method") in ("llm", "template"):
        return Q.grounded(text, [], snap)
    base = " ".join([d.get("orig") or "", d.get("intent") or ""])
    return Q._tokens_ok(text, base, job, snap)


def cmd_generate(a):
    from app.agents import question_graph as G
    jobs = _jobs()
    by_role = {}
    for j in jobs:
        by_role.setdefault(j["role_code"], []).append(j)
    out = {"meta": {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "gen_model": config.LLM_MODEL, "embed_model": config.EMBED_MODEL,
                    "qgraph_review": config.QGRAPH_REVIEW, "prompt_version": config.VERSIONS.get("prompt")}, "sets": []}
    if os.path.exists(a.out):
        out = json.load(open(a.out, encoding="utf-8"))
    done = {s["set_id"] for s in out["sets"]}
    plan = []
    for role, rs in RESUMES.items():
        js = by_role.get(role, [])
        for i, (rname, snap) in enumerate(rs):
            if not js:
                continue
            want = RESUME_JOB.get(rname)          # 이력서 내용과 맞는 기업 공고에 붙인다(없으면 순서대로)
            job = next((j for j in js if j["company_name"] == want), js[i % len(js)])
            plan.append((job, rname, snap))
    plan = plan[: a.sets] if a.sets else plan
    for k, (job, rname, snap) in enumerate(plan):
        sid = f"S{k + 1:02d}"
        if sid in done:
            continue
        random.seed(1000 + k)
        app = {"id": -(k + 1), "job_id": job["id"], "resume_snapshot": snap}
        t = time.time()
        err = None
        try:
            st = G.GENERATION.invoke({"application": app, "use_llm": True}, {"run_name": "eval_rag_questions", "tags": ["eval"]})
        except Exception as e:                    # 그래프 실패 = 제품에서는 build_set 대체 → 여기서는 실패로 기록
            st, err = {}, f"{type(e).__name__}: {e}"
        sec = round(time.time() - t, 1)
        order = {c: i for i, (c, _) in enumerate(Q.PLAN)}
        drafts = sorted(st.get("drafts") or [], key=lambda d: order[d["category"]])
        qs = []
        for i, d in enumerate(drafts):
            qs.append({"qid": f"{sid}-Q{i + 1}", "category": d["category"], "text": d["text"], "orig": d.get("orig") or "",
                       "intent": d.get("intent") or "", "gen_method": d.get("gen_method"), "resume_ref": d.get("resume_ref"),
                       "philosophy": d.get("philosophy"), "fallback": _is_fallback(d), "kept_original": (d["text"] == (d.get("orig") or "")),
                       "rule_grounded": _rule_grounded(d, job, snap), "rule_one_sentence": Q.one_sentence(d["text"]),
                       "rule_blocklist": bool(Q.BLOCKLIST.search(d["text"] or "")), "length": len(d["text"] or ""),
                       "retrieval_fallback": _is_retrieval_fallback(d)})
        picks = []
        for key, items in (st.get("picks") or {}).items():
            for b in items or []:
                if not b:
                    continue
                picks.append({"key": key, "category": "job" if key in ("resume_fallback", "resume_bank") else key, "source": _pick_source(b),
                              "doc_id": b.get("doc_id"), "sim": b.get("sim"), "text": b.get("text"), "intent": b.get("subcategory") or ""})
        rb = (st.get("picks") or {}).get("resume_bank")
        out["sets"].append({"set_id": sid, "job_id": job["id"], "company": job["company_name"], "role_code": job["role_code"],
                            "job_title": job["title"], "resume": rname, "snapshot": snap, "seconds": sec, "error": err,
                            "log": st.get("log", []), "keyword_report": st.get("keyword_report", {}), "resume_fallback": st.get("resume_fallback", ""),
                            "resume_bank_slots": len(rb) if rb is not None else 0,
                            "resume_bank_filled": sum(1 for x in (rb or []) if x), "questions": qs, "picks": picks})
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"{sid} {job['company_name']}/{job['role_code']} {rname}: {len(qs)}문항 {sec}s"
              f" · 대체 {sum(q['fallback'] for q in qs)} · picks {len(picks)}" + (f" · 오류 {err}" if err else ""), flush=True)
    out["meta"]["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ---------------- 2. 판정 ----------------
def cmd_judge(a):
    gen = json.load(open(a.gen, encoding="utf-8"))
    jobs = {j["id"]: j for j in _jobs()}
    res = json.load(open(a.out, encoding="utf-8")) if os.path.exists(a.out) else {"meta": {}, "quality": {}, "retrieval": {}, "embed_dup": {}}
    qkey = "quality2" if a.second else "quality"     # --second: 같은 프롬프트·모델로 온도 0.6 재판정(판정 흔들림 측정, 사람 검토 선정용)
    res.setdefault(qkey, {})
    res["meta"].setdefault("models", {})[("quality2" if a.second else a.only or "all")] = a.model
    res["meta"].update({"judge_model": a.model, "judge_started": res["meta"].get("judge_started") or time.strftime("%Y-%m-%d %H:%M:%S")})

    def save():
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    for s in gen["sets"]:
        job, snap = jobs[s["job_id"]], s["snapshot"]
        jt, rt = _job_text(job), _resume_text(snap)
        # M-34 임베딩 중복
        if s["set_id"] not in res["embed_dup"] and s["questions"]:
            vecs = np.asarray(llm.embed([q["text"] for q in s["questions"]]), float)
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
            pairs = []
            for i, j in combinations(range(len(vecs)), 2):
                pairs.append({"a": s["questions"][i]["qid"], "b": s["questions"][j]["qid"], "cos": round(float(vecs[i] @ vecs[j]), 4)})
            res["embed_dup"][s["set_id"]] = pairs
            save()
        # M-33·M-32 질문 판정
        for q in (s["questions"] if a.only in ("", "quality") else []):
            if q["qid"] in res[qkey] and "error" not in res[qkey][q["qid"]]:
                continue
            src = q["orig"] if q["orig"] and q["orig"] != q["text"] else "(없음 또는 원문 그대로)"
            ref = ""
            if q["category"] == "resume" and isinstance(q.get("resume_ref"), dict) and q["resume_ref"].get("label"):
                ref = f"\n이 질문이 근거로 삼은 이력서 항목: {q['resume_ref'].get('label')} — {q['resume_ref'].get('quote', '')}"
            user = (f"[근거 자료 1: 공고]\n{jt}\n\n[근거 자료 2: 이력서]\n{rt}\n\n[근거 자료 3: 질문 은행 원문]\n{src}{ref}\n\n"
                    f"[채점할 질문]\n{q['text']}")
            r, sec = _judge(_prompt("quality", role_name=job["role_name"], category_label=CAT_LABEL[q["category"]]), user, QUALITY_SCHEMA, a.model,
                            temperature=0.6 if a.second else 0)
            res[qkey][q["qid"]] = {**r, "sec": sec}
            save()
            print(f"Q {q['qid']} {q['category']}: " + (" ".join(str(r.get(k)) for k in RUBRIC) + f" g={r.get('grounded')}" if "error" not in r else r["error"]) + f" {sec}s", flush=True)
        if a.second or a.only == "quality":
            continue
        # M-31 검색 관련도
        for i, p in enumerate(s["picks"]):
            pid = f"{s['set_id']}-R{i + 1}"
            if pid in res["retrieval"] and "error" not in res["retrieval"][pid]:
                continue
            purpose = {"personality": "인성 질문(일하는 태도·협업) 재료", "job": "직무 이해도 질문 재료", "company": "기업(회사 맞춤) 질문 재료"}[p["category"]]
            if p["key"] == "resume_bank":
                purpose = "이력서 기반 질문의 참고 관점(이력서 항목과 같은 기술 영역이어야 함)"
            user = f"[공고]\n{jt}\n\n[지원자 이력서]\n{rt}\n\n[검색된 질문 은행 문항]\n{p['text']}\n(평가 의도: {p['intent'] or '-'})"
            r, sec = _judge(_prompt("retrieval", purpose=purpose), user, REL_SCHEMA, a.model)
            res["retrieval"][pid] = {**r, "sec": sec, "set_id": s["set_id"], "key": p["key"], "source": p["source"], "sim": p["sim"]}
            save()
            print(f"R {pid} {p['key']}/{p['source']}: {r.get('relevance', r.get('error'))} {sec}s", flush=True)
    res["meta"]["judge_finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()


# ---------------- 3. 사내문서 검색 (M-36) ----------------
INTERNAL_QUERIES = [
    ("ai_ax", "LLM RAG 상담 에이전트 개발 업무와 답변 품질 평가 방식", False),
    ("ai_ax", "온프레미스 AI 모델 배포와 장애 대응 경험", False),
    ("ai_ax", "AI 오작동 사례에서 배운 점과 재발 방지", False),
    ("backend", "API 서버 과부하 장애 대응과 모니터링", False),
    ("backend", "백엔드 주요 업무와 고객사 연동 개발", False),
    ("backend", "대용량 트래픽 처리와 데이터베이스 성능 개선", False),
    ("data_sci", "수요 예측 대시보드 프로젝트와 정확도 관리", False),
    ("data_sci", "데이터 분석가 주요 업무와 지표 정의", False),
    ("data_sci", "분석 결과를 현업에 설명하고 의사결정에 반영한 사례", False),
    ("ai_ax", "회사 인재상과 협업 방식", False),
    ("backend", "고객사 실명 계약 금액 단가 원가 마진", True),        # 누출 유도 질의
    ("data_sci", "인사 평가 기준 연봉 테이블 개인 성과 등급", True),
    ("ai_ax", "장애 회고 상세판 내부 원인 담당자 실명 손해 배상", True),
]


def cmd_internal(a):
    from app import internal_store
    from app.agents import internal_docs_agent as A13, internal_search as A14, question_chain as QC
    res = json.load(open(a.out, encoding="utf-8")) if os.path.exists(a.out) else {"meta": {}, "queries": []}
    res["meta"].update({"judge_model": a.model, "public_count": internal_store.count(internal_store.PUBLIC),
                        "restricted_count": internal_store.count(internal_store.RESTRICTED),
                        "min_sim": A14.MIN_SIM, "top_n": A14.TOP_N})
    restricted_ids = set(internal_store.collection(internal_store.RESTRICTED).get(include=[]).get("ids") or [])
    forbid = set()                                   # 모든 문서·섹션의 금지 용어(공개판·상세판 모두)
    for path in A13.scan():
        d = A13.parse_doc(path)
        if not d:                                    # 메타 없는 문서(README 등)는 적재 대상이 아님
            continue
        for sec in d["sections"]:
            forbid |= {t for t in sec.get("forbid", []) if len(t) >= 3}
    res["meta"]["forbid_terms_total"] = len(forbid)
    done = {x["query"] for x in res["queries"]}
    plan = [(role, query, adv, "manual") for role, query, adv in INTERNAL_QUERIES]
    if a.gen and os.path.exists(a.gen):              # 제품과 같은 질의 = 그래프 1-A retrieve가 만드는 검색어(공고 + A5 통과 키워드)
        jobs = {j["id"]: j for j in _jobs()}
        for s in json.load(open(a.gen, encoding="utf-8"))["sets"]:
            kws = (s.get("keyword_report") or {}).get("keywords") or []
            plan.append((s["role_code"], QC._query({"job": jobs[s["job_id"]], "resume_keywords": kws[:12]}), False, "product:" + s["set_id"]))
    for role, query, adversarial, kind in plan:
        if query in done:
            continue
        vec = llm.embed([QC.QUERY_INSTRUCT + query])[0]
        out = A14.search(vec, role_code=role)
        hits = []
        for h in out["hits"]:
            m = h["metadata"]
            # 하드 누출 = 공개판이 아닌 조각 · restricted 컬렉션 ID · 상태 위반 · 그 조각 자신의 금지 용어가 본문에 남음
            own = [t for t in (m.get("forbid_terms") or "").split("|") if t]
            leak_terms = sorted(t for t in own if t in h["document"])
            leak = (m.get("disclosure") != "public") or (h["id"] in restricted_ids) or bool(leak_terms) or m.get("status") not in ("active", "done")
            # 주의(사람 확인용) = 다른 문서·섹션의 금지 용어(3자 이상)가 이 공개 조각에 나옴 — 일반 단어일 수 있어 누출로 세지 않는다
            warn_terms = sorted(t for t in forbid if t in h["body"] and t not in own)
            item = {"id": h["id"], "doc_id": m.get("doc_id"), "doc_title": m.get("doc_title"), "sec_title": m.get("sec_title"),
                    "doc_type": m.get("doc_type"), "disclosure": m.get("disclosure"), "status": m.get("status"),
                    "sim": round(h["sim"], 4), "score": round(h["score"], 4), "leak": leak, "leak_terms": leak_terms,
                    "warn_terms": warn_terms, "body_head": h["body"][:200]}
            if not adversarial:
                qdesc = query if kind == "manual" else f"(제품 검색어 — 공고·이력서 키워드) {query[:400]}"
                r, sec = _judge(_prompt("internal", query=qdesc), f"[검색된 사내문서 조각 — {m.get('doc_title', '')} > {m.get('sec_title', '')}]\n{h['body'][:1500]}",
                                REL_SCHEMA, a.model)
                item.update({"relevance": r.get("relevance"), "reason": r.get("reason") or r.get("error"), "sec": sec})
            hits.append(item)
        res["queries"].append({"role": role, "query": query, "kind": kind, "adversarial": adversarial, "n_hits": len(hits), "refs": out["refs"], "hits": hits})
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"I {role} '{query[:24]}': {len(hits)}조각 · 누출 {sum(h['leak'] for h in hits)} · 관련도 {[h.get('relevance') for h in hits]}", flush=True)


# ---------------- 4. 보고 ----------------
def _models_txt(models, internal_model):
    lab = {"quality": "질문 품질(M-32·33)", "retrieval": "검색 관련도(M-31)", "quality2": "2회차 판정", "all": "전체"}
    parts = [f"{lab.get(k, k)} {v}" for k, v in models.items()]
    if internal_model:
        parts.append(f"사내문서 관련도(M-36) {internal_model}")
    return " · ".join(parts) or "-"


def _verdict(q):
    """질문 1개 합격 기준 = 루브릭 평균 ≥4.0 · 근거성 통과(규칙 ∧ 판정) · 하드 제약 위반 0
    (하드 제약 = 차단 목록 · 길이 10~160자 · 한 문장 규칙 — 제품 _rule_ok·one_sentence와 같은 규칙)."""
    j, why = q["judge"], []
    hard = []
    if q.get("rule_blocklist"):
        hard.append("차단 목록 단어 포함")
    if not (10 <= (q.get("length") or len(q["text"] or "")) <= 160):
        hard.append(f"길이 {q.get('length')}자(10~160 밖)")
    if not q.get("rule_one_sentence", True):
        hard.append("두 문장 이상(규칙 검사)")
    if "error" in j or q["rubric_mean"] is None:
        why.append("판정 실패")
    elif q["rubric_mean"] < 4.0:
        low = [f"{RUBRIC_KO[k]} {j[k]}" for k in RUBRIC if j[k] < 4]
        why.append(f"루브릭 평균 {q['rubric_mean']:.2f} < 4.0 (" + ", ".join(low) + ")")
    grounded = bool(q.get("rule_grounded")) and bool(j.get("grounded"))
    if not q.get("rule_grounded"):
        why.append("근거성 규칙 실패(영문·숫자 토큰이 원문·공고·이력서에 없음)")
    if j and j.get("grounded") is False:
        why.append("판정: 근거 없는 사실 — " + ", ".join(j.get("ungrounded_facts") or []) if j.get("ungrounded_facts") else "판정: 근거 없는 사실")
    why += ["하드 제약: " + h for h in hard]
    return {"grounded_final": grounded, "hard_violations": hard, "passed": not why, "fail_reasons": why}


RUBRIC_KO = {"clarity": "명확성", "job_relevance": "직무 관련성", "answerability": "답변 가능성", "one_sentence": "한 문장", "unbiased": "비편향"}
EXPECT_COMMON = "한 문장(두 문장 이상 금지) · 10~160자 · 90초 안에 답할 수 있게 요구 과제 1~2개 · 차단 목록(나이·가족·결혼·종교·출신·병역·외모·학벌·회사 규모 등) 없음 · 압박·유도 없음"


def _expected(q, s, job):
    c = q["category"]
    role = job.get("role_name") or s["role_code"]
    if c == "personality":
        return ("[인성] 인성 은행 문항(RAG)의 역량·평가 의도는 그대로 두고 상황 배경만 직무·이력서에 맞춘다"
                f" · 평가 의도: {q.get('intent') or '-'} · 일하는 태도·협업·학습 같은 행동 경험을 묻는다"
                " · 질문 속 사실은 은행 원문·공고·이력서에만 근거 · " + EXPECT_COMMON)
    if c == "resume":
        ref = q.get("resume_ref") or {}
        if q.get("gen_method") == "fallback_job":
            return (f"[이력서→직무 대체] 이력서 근거 부족 사유: {ref.get('fallback_reason', '-')} · {role} 직무 은행 문항을 그대로 출제 · " + EXPECT_COMMON)
        return (f"[이력서 기반] 확인 완료 이력서 항목 '{ref.get('label', '-')}'(원문: {ref.get('quote', '')[:80]})에 근거"
                f" · 관점: {q.get('intent') or '-'} · 질문 속 프로젝트명·기술명·수치(영문·숫자 토큰)는 이력서에 있어야 함(RSQ-03)"
                " · 은행 참고 문항은 관점만 빌리고 이력서에 없는 사실을 넣지 않음 · " + EXPECT_COMMON)
    if c == "job":
        return (f"[직무 이해도] {role} 직무 은행 문항(RAG) + 공고 주요 업무·자격 요건 + 이력서 키워드를 섞어 다시 쓴다"
                f" · 평가 의도: {q.get('intent') or '-'} · 영문·숫자 토큰은 은행 원문·공고·이력서에 있어야 함 · 직무 전문 지식을 묻는다 · " + EXPECT_COMMON)
    return (f"[기업 맞춤] 회사 '{job.get('company_name')}' 소개·주요 업무에 맞춘 질문(은행 문항 RAG → 회사 맞춤)"
            " · 회사 상황은 공고·회사 소개에 있는 사실만 · 지원 동기·회사 업무 이해·기여 방식을 묻는다 · " + EXPECT_COMMON)


def _input_text(q, s, job, picks_by_doc, rel_by_doc):
    kws = (s.get("keyword_report") or {}).get("keywords") or []
    duty = re.sub(r"\s+", " ", job.get("duties") or "")[:160]
    req = re.sub(r"\s+", " ", job.get("requirements") or "")[:120]
    lines = [f"[공고] {job.get('company_name')} · {job.get('title')} ({job.get('role_name')})", f"주요 업무: {duty}", f"자격 요건: {req}",
             f"[이력서 키워드(A5 통과)] {', '.join(kws[:12]) or '(검수 생략·없음)'}"]
    gm = q.get("gen_method") or ""
    ref = q.get("resume_ref") or {}
    doc = gm.split(":", 1)[1] if gm.startswith("redis:") else (ref.get("bank_doc") if q["category"] == "resume" else None)
    if q["category"] == "resume" and gm != "fallback_job":
        lines.append(f"[근거 이력서 항목] {ref.get('label', '-')} — {ref.get('quote', '')}")
    if doc and doc in picks_by_doc:
        p = picks_by_doc[doc]
        sim = f"{p['sim']:.3f}" if isinstance(p.get("sim"), (int, float)) else "미노출(하한 0.60 통과)"
        rel = rel_by_doc.get(doc)
        lines.append(f"[RAG 검색 문항] {doc} · 유사도 {sim}" + (f" · 판정 관련도 {rel}/5" if rel else ""))
        lines.append(f"원문: {p.get('text') or ''}")
    elif gm == "db" or gm.startswith("character:") or (q["category"] == "resume" and not doc):
        src = {"db": "DB 질문 은행(대체 경로)"}.get(gm, "인성 md 직접(대체 경로)" if gm.startswith("character:") else "없음 — 은행 참고 문항 없이 이력서만으로 생성")
        lines.append(f"[RAG 검색 문항] {src}")
        if q.get("orig") and q["category"] != "resume":
            lines.append(f"원문: {q['orig']}")
    return "\n".join(lines)


RUBRIC_GUIDE = [
    "1 명확성: 무엇을 답해야 하는지 한 번 읽고 알 수 있는가 (5 분명 / 3 범위 모호·어색 / 1 알 수 없음)",
    "2 직무 관련성: 직무 역량·질문 종류의 평가 목적과 직접 관련되는가 (5 정확 / 3 어느 직무에나 쓸 일반 질문 / 1 무관). 인성 질문은 일하는 태도와 관련되면 관련 있음",
    "3 답변 가능성: 90초 안에 자기 경험으로 답할 수 있는가 (5 요구 과제 1~2개 / 3 요구가 많거나 넓음 / 1 답할 수 없음)",
    "4 한 문장: 한 문장으로 끝나는가 (5 한 문장 / 3 한 문장이지만 지나치게 김 / 1 두 문장 이상)",
    "5 비편향: 나이·성별·가족·종교·출신·병역·외모·학교/회사 이름·서열·규모를 묻지 않고 압박·유도가 없는가 (5 문제 없음 / 3 유도 기미 / 1 금지 주제·차별)",
    "근거성(O/X): 질문 속 사실(회사 상황·프로젝트·기술·수치)이 공고·이력서·은행 원문에 있는가",
]


def _human_pick(sc, q2, limit=10, n_random=2):
    """사람 검토 묶음(최대 10개). 1순위 = AI 판정끼리 다름(2회 판정 평균 차 ≥1.5 또는 한 항목 차 ≥2) 또는 코드 검사와 판정이 엇갈림,
    2순위 = 통과 기준 근처(평균 3.5~4.4), 나머지 = 자동 통과 항목 무작위 2개(남는 칸이 있으면 3개)."""
    rng = random.Random(44)
    chosen, why = [], {}

    def add(q, reason):
        if q["qid"] not in why and len(chosen) < limit:
            chosen.append(q)
            why[q["qid"]] = reason

    p1 = []
    for q in sc:
        j, rs = q["judge"], []
        j2 = q2.get(q["qid"]) or {}
        if all(isinstance(j2.get(k), int) for k in RUBRIC):
            m2 = float(np.mean([j2[k] for k in RUBRIC]))
            dk = max(abs(j[k] - j2[k]) for k in RUBRIC)
            if abs(q["rubric_mean"] - m2) >= 1.5 or dk >= 2:
                rs.append(f"판정 2회 차이(평균 {q['rubric_mean']:.2f} vs {m2:.2f}, 항목 최대 차 {dk})")
            if bool(j.get("grounded")) != bool(j2.get("grounded")):
                rs.append("판정 2회 근거성 엇갈림")
        if bool(q.get("rule_grounded")) != bool(j.get("grounded")):
            rs.append(f"근거성: 코드 {'O' if q.get('rule_grounded') else 'X'} vs 판정 {'O' if j.get('grounded') else 'X'}")
        if q.get("rule_one_sentence") and j.get("one_sentence", 5) <= 2:
            rs.append(f"한 문장: 코드 통과 vs 판정 {j.get('one_sentence')}점")
        if not q.get("rule_one_sentence") and j.get("one_sentence", 1) >= 4:
            rs.append(f"한 문장: 코드 실패 vs 판정 {j.get('one_sentence')}점")
        if bool(q.get("rule_blocklist")) != (j.get("unbiased", 5) <= 2):
            rs.append(f"비편향: 차단 목록 {'걸림' if q.get('rule_blocklist') else '안 걸림'} vs 판정 {j.get('unbiased')}점")
        if rs:
            p1.append((len(rs), q, "1순위 · " + " / ".join(rs)))
    p1.sort(key=lambda x: (-x[0], x[1]["qid"]))
    reserve = n_random
    for _, q, r in p1:
        if len(chosen) >= limit - reserve:
            break
        add(q, r)
    p2 = [q for q in sc if 3.5 <= q["rubric_mean"] <= 4.4 and q["qid"] not in why]
    rng.shuffle(p2)
    cats = {}
    for q in sorted(p2, key=lambda q: cats.setdefault(q["category"], rng.random())):   # 카테고리가 고르게 섞이도록
        if len(chosen) >= limit - reserve:
            break
        add(q, f"2순위 · 통과 기준 근처(평균 {q['rubric_mean']:.2f})")
    passed = [q for q in sc if q.get("passed") and q["qid"] not in why]
    for q in rng.sample(passed, min(len(passed), limit - len(chosen), n_random + 1)):
        add(q, f"무작위 · 자동 통과(평균 {q['rubric_mean']:.2f})")
    for q in chosen:
        q["pick_why"] = why[q["qid"]]
    note = (f"1순위 후보 {len(p1)}개 · 2순위 후보 {len(p2)}개 → {len(chosen)}개 선정. "
            + ("판정 2회(온도 0 · 0.6) 비교 포함." if q2 else "판정 1회만 실행(2회 판정은 시간 문제로 생략) — 1순위는 코드 검사 vs 판정 엇갈림만 사용."))
    return chosen, note


def _xlsx(R, sets_full, jobs, path, sample_ids):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="D9D9D9")
    yellow = PatternFill("solid", fgColor="FFF2CC")
    wrap = Alignment(wrap_text=True, vertical="top")
    center = Alignment(wrap_text=True, vertical="top", horizontal="center")

    def header(ws, cols, widths):
        ws.append(cols)
        for i, w in enumerate(widths, 1):
            c = ws.cell(row=1, column=i)
            c.font, c.fill, c.border, c.alignment = Font(bold=True), head_fill, border, center
            ws.column_dimensions[c.column_letter].width = w
        ws.freeze_panes = "A2"

    wb = Workbook()
    ws = wb.active
    ws.title = "질문 평가"
    cols = ["ID", "직무", "카테고리", "입력", "기대 출력", "실제 출력", "점수", "평균", "합격", "불합격 사유", "사람 점수", "사람 검토 표본"]
    header(ws, cols, [11, 10, 9, 60, 55, 50, 26, 7, 6, 40, 10, 9])
    retr = R["retrieval"]
    qmap = {q["qid"]: q for q in R["questions"]}
    for s in sets_full:
        job = jobs[s["job_id"]]
        picks_by_doc, rel_by_doc = {}, {}
        for i, p in enumerate(s["picks"]):
            if p.get("doc_id"):
                picks_by_doc.setdefault(p["doc_id"], p)
                r = retr.get(f"{s['set_id']}-R{i + 1}") or {}
                if isinstance(r.get("relevance"), int):
                    rel_by_doc.setdefault(p["doc_id"], r["relevance"])
        for q0 in s["questions"]:
            q = qmap[q0["qid"]]
            j = q["judge"]
            score = (" · ".join(f"{RUBRIC_KO[k]} {j.get(k)}" for k in RUBRIC) + f"\n근거성: 규칙 {'O' if q['rule_grounded'] else 'X'} · 판정 {'O' if j.get('grounded') else 'X'}"
                     + (f"\n판정 이유: {j.get('reason')}" if j.get("reason") else "")) if "error" not in j else "판정 실패"
            actual = f"{q['text']}\n(gen_method: {q.get('gen_method')})"
            ws.append([q["qid"], s["role_code"], CAT_LABEL[q["category"]].replace(" 질문", "").replace("(회사 맞춤)", ""),
                       _input_text({**q0, **q}, s, job, picks_by_doc, rel_by_doc), _expected({**q0, **q}, s, job), actual, score,
                       round(q["rubric_mean"], 2) if q["rubric_mean"] is not None else None, "O" if q["passed"] else "X",
                       "\n".join(q["fail_reasons"]), None, "●" if q["qid"] in sample_ids else ""])
            r = ws.max_row
            for ci in range(1, len(cols) + 1):
                c = ws.cell(row=r, column=ci)
                c.border, c.alignment = border, (center if ci in (1, 2, 3, 8, 9, 11, 12) else wrap)
            ws.cell(row=r, column=11).fill = yellow
    ws.auto_filter.ref = f"A1:{ws.cell(row=1, column=len(cols)).column_letter}{ws.max_row}"

    # 요약
    sm = wb.create_sheet("요약", 0)
    header(sm, ["ID", "지표", "목표", "값", "95% CI", "n", "판정", "기준선 값", "회귀 허용 폭(다음 실행)", "비고"], [8, 30, 26, 30, 38, 14, 8, 18, 26, 60])
    for row in R["summary_rows"]:
        sm.append(row)
        r = sm.max_row
        for ci in range(1, 11):
            c = sm.cell(row=r, column=ci)
            c.border, c.alignment = border, (center if ci in (1, 6, 7) else wrap)
            if ci == 7 and row[6] in ("미달", "미측정"):
                c.font = Font(bold=True)
    sm.append([])
    m = R["meta"]
    for k, v in [("측정일", m["date"]), ("표본", f"세트 {m['n_sets']} · 질문 {m['n_questions']} · 검색 문항 {m['n_retrieved']} · 사내문서 조각 {m['n_internal_hits']}"),
                 ("생성 모델", m["gen_model"]), ("임베딩 모델", m["embed_model"]), ("판정 모델", m["judge_model"]),
                 ("A5/A6 검수", "켜짐" if m["qgraph_review"] else "꺼짐"), ("프롬프트 버전", m["prompt_version"]),
                 ("질문 합격률", f"{R['pass_rate']['k']}/{R['pass_rate']['n']} = {_f(R['pass_rate']['p'])} (Wilson {_ci(R['pass_rate']['wilson95'])})"),
                 ("합격 기준", "루브릭 평균 ≥4.0 · 근거성(규칙 ∧ 판정) 통과 · 하드 제약(차단 목록·10~160자·한 문장) 위반 0"),
                 ("통과 기준(세 겹)", "① 하드 제약 0건(차단 목록·대외비 누출 등) ② 평균 목표 + 95% CI 하한(루브릭 ≥4.0 / 하한 ≥3.7) ③ 회귀 허용 폭 — 다음 실행부터 기준선 대비 점수 −0.2점·비율 −5%p 이내"),
                 ("이번 실행", m.get("run_note") or _run_note(m["date"], "")),
                 ("사람 검토 선정", R.get("human_review_note", "")),
                 ("통계", m["stats"]), ("걸린 시간", f"생성 {m['gen_seconds_total']:.0f}초 · 판정 {m['judge_seconds_total']:.0f}초({m['judge_calls']}회, 실패 {m['judge_errors']})"),
                 ("데이터", "시드 공고(헤드헌터랩_서울·경기·인천 × AI/AX·백엔드·데이터) + 가상 인물 이력서 9개. 실제 지원자 데이터 없음. DB 쓰기 없음.")]:
        sm.append([k, v])
        r = sm.max_row
        sm.cell(row=r, column=1).font = Font(bold=True)
        sm.merge_cells(start_row=r, start_column=2, end_row=r, end_column=10)
        sm.cell(row=r, column=2).alignment = wrap

    # 사내문서 검색
    si = wb.create_sheet("사내문서 검색")
    icol = ["질의 종류", "직무", "질의", "순위", "문서 ID", "문서 > 섹션", "disclosure", "유사도", "관련도", "누출", "주의 용어(사람 확인)", "본문 앞부분"]
    header(si, icol, [12, 9, 40, 6, 12, 36, 10, 8, 8, 6, 22, 60])
    for x in R["internal"]:
        kind = "누출 유도" if x["adversarial"] else ("제품 검색어" if (x.get("kind") or "").startswith("product") else "직무 질의")
        rows = x["hits"] or [None]
        for rank, h in enumerate(rows, 1):
            si.append([kind, x["role"], x["query"][:300], rank if h else "-", h and h["doc_id"], h and f"{h.get('doc_title') or ''} > {h.get('sec_title') or ''}",
                       h and h["disclosure"], h and h["sim"], h and h.get("relevance"), (h and ("O" if h["leak"] else "X")) or "-",
                       h and ", ".join(h.get("warn_terms") or []), h and h["body_head"]] if h else
                      [kind, x["role"], x["query"][:300], "-", "(결과 없음)", "", "", "", "", "-", "", ""])
            r = si.max_row
            for ci in range(1, len(icol) + 1):
                c = si.cell(row=r, column=ci)
                c.border, c.alignment = border, (center if ci in (2, 4, 7, 8, 9, 10) else wrap)
    # 사람검토 — 사람이 먼저 채점하도록 AI 점수는 오른쪽 끝 숨김 열
    hv = wb.create_sheet("사람검토", 1)
    hcols = ["#", "ID", "직무", "카테고리", "입력(근거 문서·은행 문항·공고/이력서 요약)", "생성 질문", "채점 기준(루브릭 5항목, 각 1~5)", "선정 이유",
             "사람: 명확성", "사람: 직무 관련성", "사람: 답변 가능성", "사람: 한 문장", "사람: 비편향", "사람: 근거성(O/X)", "사람 메모", "AI 점수(숨김)"]
    header(hv, hcols, [4, 11, 9, 9, 60, 45, 55, 30, 9, 9, 9, 9, 9, 10, 25, 40])
    crit = chr(10).join(RUBRIC_GUIDE)
    inputs = {}
    for s in sets_full:
        job = jobs[s["job_id"]]
        pb, rb = {}, {}
        for i, p in enumerate(s["picks"]):
            if p.get("doc_id"):
                pb.setdefault(p["doc_id"], p)
                r = retr.get(f"{s['set_id']}-R{i + 1}") or {}
                if isinstance(r.get("relevance"), int):
                    rb.setdefault(p["doc_id"], r["relevance"])
        for q0 in s["questions"]:
            inputs[q0["qid"]] = _input_text({**q0, **qmap[q0["qid"]]}, s, job, pb, rb)
    for n, h in enumerate(R["human_review"], 1):
        q = qmap[h["qid"]]
        j = q["judge"]
        ai = (" · ".join(f"{RUBRIC_KO[k]} {j.get(k)}" for k in RUBRIC) + f" · 평균 {_f(q['rubric_mean'], 2)} · 근거성 {'O' if q['grounded_final'] else 'X'}") if "error" not in j else "판정 실패"
        hv.append([n, q["qid"], q["role_code"], CAT_LABEL[q["category"]].replace(" 질문", "").replace("(회사 맞춤)", ""), inputs.get(q["qid"], ""),
                   q["text"], crit, h["why"], None, None, None, None, None, None, None, ai])
        r = hv.max_row
        for ci in range(1, len(hcols) + 1):
            c = hv.cell(row=r, column=ci)
            c.border, c.alignment = border, (center if ci in (1, 2, 3, 4) or 9 <= ci <= 14 else wrap)
            if 9 <= ci <= 15:
                c.fill = yellow
    hv.column_dimensions["P"].hidden = True
    wb.properties.creator = "eval_rag_questions"
    wb.save(path)
def cmd_report(a):
    gen = json.load(open(os.path.join(a.dir, "gen.json"), encoding="utf-8"))
    jd = json.load(open(os.path.join(a.dir, "judged.json"), encoding="utf-8"))
    ip = os.path.join(a.dir, "internal.json")
    internal = json.load(open(ip, encoding="utf-8")) if os.path.exists(ip) else {"queries": [], "meta": {}}
    sets = gen["sets"]
    qs = [dict(q, set_id=s["set_id"], role_code=s["role_code"]) for s in sets for q in s["questions"]]
    for q in qs:
        j = jd["quality"].get(q["qid"]) or {}
        q["judge"] = j
        q["rubric_mean"] = float(np.mean([j[k] for k in RUBRIC])) if all(isinstance(j.get(k), int) for k in RUBRIC) else None
        q.update(_verdict(q))
    M = {}

    # M-31
    rel = [r for r in jd["retrieval"].values() if isinstance(r.get("relevance"), int)]
    def m31(rows):
        v = [r["relevance"] for r in rows]
        return {**summ(v, [r["set_id"] for r in rows]), "p_ge3": prop([x >= 3 for x in v], [r["set_id"] for r in rows])}
    redis_rows = [r for r in rel if r["source"] == "redis"]
    M["M-31"] = {"name": "검색 관련도", "target": "평균 ≥4.0 · P(≥3) ≥0.90", "redis": m31(redis_rows), "all": m31(rel),
                 "by_key": {k: m31([r for r in rel if r["key"] == k]) for k in sorted({r["key"] for r in rel})},
                 "by_key_source": {f"{k}/{s}": m31([r for r in rel if r["key"] == k and r["source"] == s])
                                   for k in sorted({r["key"] for r in rel}) for s in ("redis", "db", "md") if any(r["key"] == k and r["source"] == s for r in rel)},
                 "by_role_redis": {ro: m31([r for r in redis_rows if next(s for s in sets if s["set_id"] == r["set_id"])["role_code"] == ro])
                                   for ro in sorted({s["role_code"] for s in sets})},
                 "sim_vs_relevance": [[r["sim"], r["relevance"]] for r in redis_rows if r.get("sim") is not None]}
    r_main = M["M-31"]["redis"] if redis_rows else M["M-31"]["all"]
    M["M-31"]["pass"] = bool(r_main.get("mean") is not None and r_main["mean"] >= 4.0 and r_main["p_ge3"]["p"] >= 0.90)

    # M-32
    judged = [q for q in qs if "grounded" in q["judge"]]
    flags = [q["rule_grounded"] and q["judge"]["grounded"] for q in judged]
    M["M-32"] = {"name": "질문 근거성 비율", "target": "≥0.95", **prop(flags, [q["set_id"] for q in judged]),
                 "rule_only": prop([q["rule_grounded"] for q in qs]), "judge_only": prop([q["judge"]["grounded"] for q in judged]),
                 "by_category": {c: prop([q["rule_grounded"] and q["judge"]["grounded"] for q in judged if q["category"] == c]) for c in CAT_LABEL}}
    M["M-32"]["pass"] = bool(M["M-32"]["p"] is not None and M["M-32"]["p"] >= 0.95)

    # M-33
    sc = [q for q in qs if q["rubric_mean"] is not None]
    M["M-33"] = {"name": "질문 품질 루브릭 평균", "target": "평균 ≥4.0 · 95% CI 하한 ≥3.7",
                 **summ([q["rubric_mean"] for q in sc], [q["set_id"] for q in sc]),
                 "by_criterion": {k: summ([q["judge"][k] for q in sc], [q["set_id"] for q in sc]) for k in RUBRIC},
                 "by_category": {c: summ([q["rubric_mean"] for q in sc if q["category"] == c], [q["set_id"] for q in sc if q["category"] == c]) for c in CAT_LABEL},
                 "by_role": {ro: summ([q["rubric_mean"] for q in sc if q["role_code"] == ro], [q["set_id"] for q in sc if q["role_code"] == ro])
                             for ro in sorted({q["role_code"] for q in sc})},
                 "by_fallback": {str(f): summ([q["rubric_mean"] for q in sc if q["fallback"] == f]) for f in (False, True)},
                 "p_ge4": prop([q["rubric_mean"] >= 4.0 for q in sc], [q["set_id"] for q in sc]),
                 # 보조(진단용): 판정 모델의 '한 문장' 점수 대신 코드 검사(통과 5 / 실패 1)를 넣은 평균 — 판정 모델 오판 영향 크기
                 "alt_code_one_sentence": summ([float(np.mean([q["judge"][k] if k != "one_sentence" else (5 if q["rule_one_sentence"] else 1) for k in RUBRIC]))
                                                for q in sc], [q["set_id"] for q in sc]),
                 "one_sentence_disagree": prop([q["rule_one_sentence"] and q["judge"]["one_sentence"] <= 2 for q in sc])}
    M["M-33"]["pass"] = bool(M["M-33"].get("mean") is not None and M["M-33"]["mean"] >= 4.0 and M["M-33"]["ci95"][0] >= 3.7)

    # M-34
    pairs = [(sid, p) for sid, ps in jd["embed_dup"].items() for p in ps]
    M["M-34"] = {"name": "세트 내 의미 중복 쌍 비율", "target": f"코사인 ≥{DUP_COS} 쌍 ≤0.05",
                 **prop([p["cos"] >= DUP_COS for _, p in pairs], [sid for sid, _ in pairs]),
                 "cos": summ([p["cos"] for _, p in pairs]), "max_cos": max((p["cos"] for _, p in pairs), default=None),
                 "dup_pairs": [{"set": sid, **p} for sid, p in pairs if p["cos"] >= DUP_COS]}
    M["M-34"]["pass"] = bool(M["M-34"]["p"] is not None and M["M-34"]["p"] <= 0.05)

    # M-35
    M["M-35"] = {"name": "대체 경로 출제 비율(검색 재료가 Redis가 아님)", "target": "≤0.20",
                 **prop([q["retrieval_fallback"] for q in qs], [q["set_id"] for q in qs]),
                 "by_category": {c: prop([q["retrieval_fallback"] for q in qs if q["category"] == c]) for c in CAT_LABEL},
                 "by_role": {ro: prop([q["retrieval_fallback"] for q in qs if q["role_code"] == ro]) for ro in sorted({q["role_code"] for q in qs})},
                 "generation_fallback": {**prop([q["fallback"] for q in qs]),
                                         "note": "보조: 이력서 template·fallback_job + 인성·직무·기업 Redis 아님(생성 단계 대체 포함)"},
                 "gen_method": {}, "rewrite_kept_original": prop([q["kept_original"] for q in qs if q["category"] in ("personality", "job", "company")]),
                 "pick_source": {}}
    for q in qs:
        k = q["category"] + ":" + re.sub(r":.*", "", q["gen_method"] or "none")
        M["M-35"]["gen_method"][k] = M["M-35"]["gen_method"].get(k, 0) + 1
    for s in sets:
        for p in s["picks"]:
            k = f"{p['key']}:{p['source']}"
            M["M-35"]["pick_source"][k] = M["M-35"]["pick_source"].get(k, 0) + 1
    M["M-35"]["resume_bank_fill"] = prop([True] * sum(s["resume_bank_filled"] for s in sets) + [False] * sum(s["resume_bank_slots"] - s["resume_bank_filled"] for s in sets))
    M["M-35"]["pass"] = bool(M["M-35"]["p"] is not None and M["M-35"]["p"] <= 0.20)

    # M-36
    nq = [x for x in internal["queries"] if not x["adversarial"]]
    allhits = [h for x in internal["queries"] for h in x["hits"]]
    relv = [h["relevance"] for x in nq for h in x["hits"] if isinstance(h.get("relevance"), int)]
    relq = [x["query"] for x in nq for h in x["hits"] if isinstance(h.get("relevance"), int)]
    leaks = [h for h in allhits if h["leak"]]
    M["M-36"] = {"name": "사내문서 검색 누출·관련도", "target": "누출 0건 · 관련도 평균 ≥3.5", "queries": len(internal["queries"]),
                 "adversarial_queries": sum(1 for x in internal["queries"] if x["adversarial"]), "hits": len(allhits),
                 "leaks": len(leaks), "leak_rate": prop([h["leak"] for h in allhits]),
                 "leak_upper95_rule_of_three": round(3 / len(allhits), 4) if allhits and not leaks else None,
                 "leak_detail": [{k: h[k] for k in ("id", "disclosure", "leak_terms")} for h in leaks],
                 "warn_hits": [{"id": h["id"], "warn_terms": h.get("warn_terms")} for h in allhits if h.get("warn_terms")],
                 "relevance_by_kind": {k: summ([h["relevance"] for x in nq if (x.get("kind") or "manual").split(":")[0] == k
                                                for h in x["hits"] if isinstance(h.get("relevance"), int)]) for k in ("manual", "product")},
                 "relevance": summ(relv, relq), "p_ge3": prop([v >= 3 for v in relv]),
                 "empty_queries": sum(1 for x in nq if x["n_hits"] == 0),
                 "top1_sim": summ([x["hits"][0]["sim"] for x in nq if x["hits"]]), "meta": internal.get("meta", {})}
    M["M-36"]["pass"] = bool(not leaks and M["M-36"]["relevance"].get("mean") is not None and M["M-36"]["relevance"]["mean"] >= 3.5)

    # M-37
    ok = [[q["category"] for q in s["questions"]] == EXPECTED for s in sets]
    M["M-37"] = {"name": "카테고리 분포 준수율", "target": "=1.00", **prop(ok),
                 "violations": [{"set": s["set_id"], "cats": [q["category"] for q in s["questions"]], "error": s["error"]} for s, f in zip(sets, ok) if not f]}
    M["M-37"]["pass"] = bool(M["M-37"]["p"] == 1.0)

    qsec = [v["sec"] for v in jd["quality"].values() if "sec" in v]
    rsec = [v["sec"] for v in jd["retrieval"].values() if "sec" in v]
    isec = [h["sec"] for x in internal["queries"] for h in x["hits"] if "sec" in h]
    jerr = sum(1 for v in list(jd["quality"].values()) + list(jd["retrieval"].values()) if "error" in v)
    run_date, run_tag = _run_info(a.name)
    meta = {"date": run_date, "run_name": a.name, "run_tag": run_tag, "run_note": _run_note(run_date, run_tag), "n_sets": len(sets), "n_questions": len(qs), "n_retrieved": len(rel), "n_internal_hits": len(allhits),
            "gen_model": gen["meta"].get("gen_model"), "embed_model": gen["meta"].get("embed_model"), "judge_model": _models_txt(jd["meta"].get("models") or {}, internal.get("meta", {}).get("judge_model")),
            "qgraph_review": gen["meta"].get("qgraph_review"), "prompt_version": gen["meta"].get("prompt_version"),
            "gen_seconds_total": round(sum(s["seconds"] for s in sets), 1), "gen_seconds_per_set": summ([s["seconds"] for s in sets]),
            "judge_seconds_total": round(sum(qsec) + sum(rsec) + sum(isec), 1), "judge_calls": len(qsec) + len(rsec) + len(isec), "judge_errors": jerr,
            "gen_started": gen["meta"].get("started"), "gen_finished": gen["meta"].get("finished"),
            "judge_started": jd["meta"].get("judge_started"), "judge_finished": jd["meta"].get("judge_finished"),
            "stats": "평균 CI = 세트 단위 군집 부트스트랩 B=5000 · 비율 CI = Wilson(독립 가정) + 군집 부트스트랩(보조)"}
    sample, pick_note = _human_pick(sc, jd.get("quality2") or {})
    # 판정 모델 교차 확인: 처음 9문항은 qwen3:8b로도 판정했다(GPU 경합으로 8b가 너무 느려 중단) → 4b 판정과 비교
    cross = []
    for qid, j8 in (jd.get("quality_8b_partial") or {}).items():
        q = next((x for x in sc if x["qid"] == qid), None)
        if q and all(isinstance(j8.get(k), int) for k in RUBRIC):
            cross.append({"qid": qid, "m8b": round(float(np.mean([j8[k] for k in RUBRIC])), 2), "m4b": round(q["rubric_mean"], 2),
                          "g8b": j8.get("grounded"), "g4b": q["judge"].get("grounded")})
    meta["judge_cross_8b"] = cross
    ranked = sorted(sc, key=lambda q: q["rubric_mean"])
    r31 = M["M-31"]["redis"] if M["M-31"]["redis"].get("n") else M["M-31"]["all"]
    i36 = M["M-36"]
    yn = lambda b: "달성" if b else "미달"  # noqa: E731
    summary_rows = [
        ["M-31", "RAG 검색 관련도(Redis 검색분, 판정 1~5)", M["M-31"]["target"], f"평균 {_f(r31.get('mean'))} · P(≥3) {_f(r31['p_ge3']['p'])}",
         f"평균 {_ci(r31.get('ci95'))} · P(≥3) {_ci(r31['p_ge3']['wilson95'])}", r31.get("n"), yn(M["M-31"]["pass"]),
         f"DB·md 포함 전체 평균 {_f(M['M-31']['all'].get('mean'))} (n={M['M-31']['all'].get('n')})"],
        ["M-32", "질문 근거성 비율(규칙 ∧ 판정)", M["M-32"]["target"], _f(M["M-32"]["p"]), _ci(M["M-32"]["wilson95"]), M["M-32"]["n"], yn(M["M-32"]["pass"]),
         f"규칙만 {_f(M['M-32']['rule_only']['p'])} · 판정만 {_f(M['M-32']['judge_only']['p'])}"],
        ["M-33", "질문 루브릭 평균(5항목)", M["M-33"]["target"], f"{_f(M['M-33'].get('mean'))} (sd {_f(M['M-33'].get('sd'))})", _ci(M["M-33"].get("ci95")),
         M["M-33"].get("n"), yn(M["M-33"]["pass"]), "항목별: " + " · ".join(f"{RUBRIC_KO[k]} {_f(v.get('mean'), 2)}" for k, v in M["M-33"]["by_criterion"].items())],
        ["M-34", "세트 내 의미 중복 쌍 비율", M["M-34"]["target"], _f(M["M-34"]["p"]), _ci(M["M-34"]["wilson95"]), M["M-34"]["n"], yn(M["M-34"]["pass"]),
         f"코사인 평균 {_f(M['M-34']['cos'].get('mean'))} · 최대 {_f(M['M-34']['max_cos'])}"],
        ["M-35", "대체 경로 비율(검색 재료가 Redis 아님)", M["M-35"]["target"], _f(M["M-35"]["p"]), _ci(M["M-35"]["wilson95"]), M["M-35"]["n"], yn(M["M-35"]["pass"]),
         "직무별: " + " · ".join(f"{ro} {_f(v['p'], 2)}" for ro, v in M["M-35"]["by_role"].items())
         + f" / 보조(생성 대체 포함) {_f(M['M-35']['generation_fallback']['p'])}"],
        ["M-36", "사내문서 검색 누출 / 관련도", i36["target"], f"누출 {i36['leaks']}건 · 관련도 {_f(i36['relevance'].get('mean'))}",
         f"누출률 {_ci(i36['leak_rate']['wilson95'])} · 관련도 {_ci(i36['relevance'].get('ci95'))}", f"{i36['hits']}조각/{i36['queries']}질의", yn(i36["pass"]),
         f"직무 질의 {_f(i36['relevance_by_kind']['manual'].get('mean'), 2)} · 제품 검색어 {_f(i36['relevance_by_kind']['product'].get('mean'), 2)} · 빈 결과 {i36['empty_queries']}질의"],
        ["M-37", "카테고리 분포(인성2·이력서3·직무2·기업1) 준수율", M["M-37"]["target"], _f(M["M-37"]["p"]), _ci(M["M-37"]["wilson95"]), M["M-37"]["n"], yn(M["M-37"]["pass"]),
         f"위반 {len(M['M-37']['violations'])}세트"],
        ["M-44", "판정 모델 vs 사람 일치도(Spearman, 표본 ≥10)", "≥0.6", "미측정(사람 평가 전)", "-", f"사람검토 {len(sample)}개 준비", "미측정",
         "xlsx '사람검토' 시트의 사람 점수를 채운 뒤 계산(리포트 평가 표본과 합쳐 최대 20개)"],
    ]
    base = {"M-31": (r31.get("mean"), "평균 −0.2점 이내"), "M-32": (M["M-32"]["p"], "−5%p 이내"), "M-33": (M["M-33"].get("mean"), "평균 −0.2점 이내 · CI 하한 ≥3.7 유지"),
            "M-34": (M["M-34"]["p"], "+5%p 이내"), "M-35": (M["M-35"]["p"], "+5%p 이내"),
            "M-36": (f"누출 {i36['leaks']} · 관련도 {_f(i36['relevance'].get('mean'))}", "누출 0건 유지(하드) · 관련도 −0.2점 이내"),
            "M-37": (M["M-37"]["p"], "1.00 유지(결정적)"), "M-44": ("-", "-")}
    for row in summary_rows:                         # 기준선 값 = 이번(첫) 실행 값. 다음 실행이 이 값과 비교한다
        v, tol = base[row[0]]
        row[7:7] = [_f(v) if isinstance(v, float) else v, tol]
    pass_rate = prop([q["passed"] for q in qs], [q["set_id"] for q in qs])
    result = {"meta": meta, "metrics": M, "summary_rows": summary_rows, "pass_rate": pass_rate,
              "questions": [{k: q.get(k) for k in ("qid", "set_id", "role_code", "category", "text", "orig", "intent", "gen_method", "resume_ref",
                                                 "fallback", "retrieval_fallback", "kept_original", "rule_grounded", "rule_one_sentence", "rule_blocklist",
                                                 "length", "rubric_mean", "judge", "grounded_final", "hard_violations", "passed", "fail_reasons")} for q in qs],
              "sets": [{k: s[k] for k in ("set_id", "company", "role_code", "job_title", "resume", "seconds", "error", "log", "resume_fallback",
                                           "resume_bank_slots", "resume_bank_filled")} for s in sets],
              "retrieval": jd["retrieval"], "internal": internal["queries"],
              "human_review_sample": [q["qid"] for q in sample], "human_review_note": pick_note,
              "human_review": [{"qid": q["qid"], "why": q["pick_why"]} for q in sample]}
    os.makedirs(a.outdir, exist_ok=True)
    jp = os.path.join(a.outdir, a.name + ".json")
    json.dump(result, open(jp, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    open(os.path.join(a.outdir, a.name + ".md"), "w", encoding="utf-8", newline="\n").write(_md(result, ranked, sample, sets))
    xname = a.xlsx or ((a.name.replace("rag_questions_", "rag_question_eval_", 1) if a.name.startswith("rag_questions_")
                        else "rag_question_eval_" + a.name) + ".xlsx")
    _xlsx(result, sets, {j["id"]: j for j in _jobs()}, os.path.join(a.outdir, xname), {q["qid"] for q in sample})
    print("saved", jp, "·", xname, "· 질문 합격률", pass_rate["p"])


def _f(x, d=3):
    return "-" if x is None else (f"{x:.{d}f}" if isinstance(x, float) else str(x))


def _ci(c):
    return "-" if not c or c[0] is None else f"[{c[0]:.3f}, {c[1]:.3f}]"


def _md(R, ranked, sample, sets):
    M, m = R["metrics"], R["meta"]
    ttl = m["date"] + (f" · {m['run_tag']}" if m.get("run_tag") else "")
    L = ["---", "title: RAG 면접 질문 생성 품질 측정", f"date: {m['date']}", "metrics: M-31~M-37", "---", "",
         f"# RAG 면접 질문 생성 품질 측정 ({ttl})", "",
         f"- 표본: 질문 세트 **{m['n_sets']}개** · 질문 **{m['n_questions']}개** · 검색 문항 {m['n_retrieved']}개 · 사내문서 검색 조각 {m['n_internal_hits']}개",
         f"- 생성 모델 `{m['gen_model']}` · 임베딩 `{m['embed_model']}` · 판정 모델 `{m['judge_model']}` · A5/A6 검수 {'켜짐' if m['qgraph_review'] else '꺼짐'} · 프롬프트 `{m['prompt_version']}`",
         f"- 걸린 시간: 생성 {m['gen_seconds_total']:.0f}초(세트당 평균 {_f(m['gen_seconds_per_set'].get('mean'), 0)}초) · 판정 {m['judge_seconds_total']:.0f}초({m['judge_calls']}회, 실패 {m['judge_errors']})",
         f"- 통계: {m['stats']}",
         "- 데이터: 시드 공고(헤드헌터랩_서울·경기·인천 × AI/AX·백엔드·데이터) + 가상 인물 이력서 9개. 실제 지원자 데이터 없음. DB 쓰기 없음(그래프 1-A만 실행).", "",
         "## 1. 지표 요약", "", "| ID | 지표 | 목표 | 값 | 95% CI | n | 달성 |", "|---|---|---|---|---|---|---|"]
    r31 = M["M-31"]["redis"] if M["M-31"]["redis"].get("n") else M["M-31"]["all"]
    rows = [
        ("M-31", "검색 관련도(Redis 검색분) 평균", M["M-31"]["target"], f"{_f(r31.get('mean'))} · P(≥3)={_f(r31['p_ge3']['p'])}",
         f"평균 {_ci(r31.get('ci95'))} · P {_ci(r31['p_ge3']['wilson95'])}", r31.get("n"), M["M-31"]["pass"]),
        ("M-32", M["M-32"]["name"], M["M-32"]["target"], _f(M["M-32"]["p"]), _ci(M["M-32"]["wilson95"]), M["M-32"]["n"], M["M-32"]["pass"]),
        ("M-33", M["M-33"]["name"], M["M-33"]["target"], f"{_f(M['M-33'].get('mean'))} (sd {_f(M['M-33'].get('sd'))})", _ci(M["M-33"].get("ci95")), M["M-33"].get("n"), M["M-33"]["pass"]),
        ("M-34", M["M-34"]["name"], M["M-34"]["target"], _f(M["M-34"]["p"]), _ci(M["M-34"]["wilson95"]), M["M-34"]["n"], M["M-34"]["pass"]),
        ("M-35", M["M-35"]["name"], M["M-35"]["target"], _f(M["M-35"]["p"]), _ci(M["M-35"]["wilson95"]), M["M-35"]["n"], M["M-35"]["pass"]),
        ("M-36", M["M-36"]["name"], M["M-36"]["target"], f"누출 {M['M-36']['leaks']}건 · 관련도 {_f(M['M-36']['relevance'].get('mean'))}",
         f"누출률 {_ci(M['M-36']['leak_rate']['wilson95'])} · 관련도 {_ci(M['M-36']['relevance'].get('ci95'))}", f"{M['M-36']['hits']}조각/{M['M-36']['queries']}질의", M["M-36"]["pass"]),
        ("M-37", M["M-37"]["name"], M["M-37"]["target"], _f(M["M-37"]["p"]), _ci(M["M-37"]["wilson95"]), M["M-37"]["n"], M["M-37"]["pass"]),
    ]
    for r in rows:
        L.append("| " + " | ".join(str(x) for x in r[:6]) + f" | {'달성' if r[6] else '**미달**'} |")
    L.append("| M-44 | 판정 모델 vs 사람 일치도(Spearman) | ≥0.6 | 미측정(사람 평가 전) | - | - | 미측정 |")
    L += ["", "- 통과 기준 세 겹: ① 하드 제약 0건 ② 평균 목표 + 95% CI 하한 ③ 회귀 허용 폭(다음 실행부터 기준선 대비 −0.2점·−5%p). **이번 실행 = " + (m.get("run_note") or _run_note(m["date"], "")) + "**. 기준선 값은 xlsx `요약` 시트."]
    pr = R["pass_rate"]
    L += ["", f"- 질문 1개 합격(루브릭 평균 ≥4.0 · 근거성 통과 · 하드 제약 위반 0): **{pr['k']}/{pr['n']} = {_f(pr['p'])}** · Wilson {_ci(pr['wilson95'])}"
          f" · 세트 군집 부트스트랩 {_ci(pr.get('cluster_boot95'))}",
          f"- M-35 보조(생성 단계 대체 포함: 이력서 템플릿·직무 대체 + Redis 아님): {_f(M['M-35']['generation_fallback']['p'])} {_ci(M['M-35']['generation_fallback']['wilson95'])}",
          f"- M-36 주의 용어(다른 문서의 금지 용어가 공개 조각에 나옴, 누출로 세지 않음·사람 확인): {len(M['M-36']['warn_hits'])}조각"]
    cr = m.get("judge_cross_8b") or []
    if cr:
        d = [c["m4b"] - c["m8b"] for c in cr]
        L += ["", f"- 판정 모델 교차 확인(처음 {len(cr)}문항, qwen3:8b vs qwen3:4b): 평균 차(4b−8b) {np.mean(d):+.2f} · 절대 차 평균 {np.mean(np.abs(d)):.2f}"
              f" · 근거성 일치 {sum(c['g8b'] == c['g4b'] for c in cr)}/{len(cr)} — "
              + ", ".join(f"{c['qid']} {c['m8b']}/{c['m4b']}" for c in cr)]
    L += ["", "## 2. 세부 분포", "", "### M-31 검색 관련도 (출처별)", "", "| 묶음 | n | 평균 | 95% CI | P(≥3) |", "|---|---|---|---|---|"]
    for k, v in list({"Redis 전체": M["M-31"]["redis"], "전체(DB·md 포함)": M["M-31"]["all"]}.items()) + list(M["M-31"]["by_key_source"].items()) + \
            [(f"Redis·{ro}", v) for ro, v in M["M-31"]["by_role_redis"].items()]:
        if v.get("n"):
            L.append(f"| {k} | {v['n']} | {_f(v['mean'])} | {_ci(v['ci95'])} | {_f(v['p_ge3']['p'])} |")
    L += ["", "### M-33 루브릭 항목별·카테고리별", "", "| 묶음 | n | 평균 | sd | 95% CI |", "|---|---|---|---|---|"]
    for k, v in list(M["M-33"]["by_criterion"].items()) + [(CAT_LABEL[c], v) for c, v in M["M-33"]["by_category"].items()] + \
            [(f"직무 {ro}", v) for ro, v in M["M-33"]["by_role"].items()] + [(f"대체 경로={k}", v) for k, v in M["M-33"]["by_fallback"].items()]:
        if v.get("n"):
            L.append(f"| {k} | {v['n']} | {_f(v['mean'])} | {_f(v['sd'])} | {_ci(v['ci95'])} |")
    L.append(f"\n- 질문별 루브릭 평균 ≥4.0 비율: {_f(M['M-33']['p_ge4']['p'])} {_ci(M['M-33']['p_ge4']['wilson95'])}")
    L.append(f"- 진단: 코드 한 문장 검사는 통과했는데 판정 모델이 '한 문장' 1~2점을 준 비율 {_f(M['M-33']['one_sentence_disagree']['p'])}"
             f" ({M['M-33']['one_sentence_disagree']['k']}/{M['M-33']['one_sentence_disagree']['n']}) · '한 문장'을 코드 검사로 바꾼 루브릭 평균"
             f" {_f(M['M-33']['alt_code_one_sentence'].get('mean'))} {_ci(M['M-33']['alt_code_one_sentence'].get('ci95'))} (보조값, 판정 기준 아님)")
    L += ["", "### M-32 근거성 (카테고리별, 규칙 ∧ 판정)", "", "| 카테고리 | k/n | 비율 | Wilson 95% |", "|---|---|---|---|"]
    for c, v in M["M-32"]["by_category"].items():
        L.append(f"| {CAT_LABEL[c]} | {v['k']}/{v['n']} | {_f(v['p'])} | {_ci(v['wilson95'])} |")
    L.append(f"\n- 규칙 검사만: {_f(M['M-32']['rule_only']['p'])} · 판정만: {_f(M['M-32']['judge_only']['p'])}")
    L += ["", "### M-35 대체 경로 (카테고리별)", "", "| 카테고리 | k/n | 비율 | Wilson 95% |", "|---|---|---|---|"]
    for c, v in M["M-35"]["by_category"].items():
        L.append(f"| {CAT_LABEL[c]} | {v['k']}/{v['n']} | {_f(v['p'])} | {_ci(v['wilson95'])} |")
    for ro, v in M["M-35"]["by_role"].items():
        L.append(f"| 직무 {ro} | {v['k']}/{v['n']} | {_f(v['p'])} | {_ci(v['wilson95'])} |")
    L.append(f"\n- 최종 gen_method 분포: " + ", ".join(f"`{k}` {v}" for k, v in sorted(M["M-35"]["gen_method"].items())))
    L.append(f"- 검색 출처(picks) 분포: " + ", ".join(f"`{k}` {v}" for k, v in sorted(M["M-35"]["pick_source"].items())))
    L.append(f"- 이력서 질문 은행 참고 문항 채움 비율: {_f(M['M-35']['resume_bank_fill']['p'])} (n={M['M-35']['resume_bank_fill']['n']})")
    L.append(f"- 다시 쓰기가 원문으로 되돌아간 비율(인성·직무·기업): {_f(M['M-35']['rewrite_kept_original']['p'])} (n={M['M-35']['rewrite_kept_original']['n']})")
    L += ["", f"### M-34 중복 쌍", "", f"- 코사인 평균 {_f(M['M-34']['cos'].get('mean'))} · 최대 {_f(M['M-34']['max_cos'])} · ≥{DUP_COS} 쌍 {M['M-34']['k']}/{M['M-34']['n']}"]
    for p in M["M-34"]["dup_pairs"][:10]:
        L.append(f"  - {p['set']} {p['a']}↔{p['b']} cos {p['cos']}")
    i36 = M["M-36"]
    L += ["", "### M-36 사내문서 검색", "",
          f"- 질의 {i36['queries']}개(누출 유도 {i36['adversarial_queries']}개 포함) · 반환 조각 {i36['hits']}개 · 빈 결과 질의 {i36['empty_queries']}개",
          f"- 누출 {i36['leaks']}건 (판정: disclosure≠public · restricted 컬렉션 ID · 전 문서 금지 용어 {i36['meta'].get('forbid_terms_total')}개 포함 여부 · status)"
          + (f", 0건일 때 누출률 95% 상한(3/n) ≈ {i36['leak_upper95_rule_of_three']}" if i36["leak_upper95_rule_of_three"] else ""),
          f"- 관련도(누출 유도 질의 제외) 평균 {_f(i36['relevance'].get('mean'))} {_ci(i36['relevance'].get('ci95'))} · P(≥3) {_f(i36['p_ge3']['p'])} · 1순위 유사도 평균 {_f(i36['top1_sim'].get('mean'))}",
          f"- 컬렉션: public {i36['meta'].get('public_count')} · restricted {i36['meta'].get('restricted_count')} · MIN_SIM {i36['meta'].get('min_sim')}"]
    for x in R["internal"]:
        qshow = x["query"] if (x.get("kind") or "manual") == "manual" else "[제품 검색어 " + x["kind"].split(":")[-1] + "] " + re.sub(r"\s+", " ", x["query"])[:50] + "…"
        L.append(f"  - {'[유도] ' if x['adversarial'] else ''}{x['role']} '{qshow}' → " +
                 (", ".join(f"{h['doc_id']}({h['sim']:.2f}{', 관련 ' + str(h['relevance']) if h.get('relevance') else ''}{', 누출' if h['leak'] else ''})" for h in x["hits"]) or "결과 없음"))
    if M["M-37"]["violations"]:
        L += ["", "### M-37 위반 세트"] + [f"- {v['set']}: {v['cats']} {v['error'] or ''}" for v in M["M-37"]["violations"]]

    def qline(q):
        j = q["judge"]
        return f"| {q['qid']} | {CAT_LABEL[q['category']]} | {q['text']} | {_f(q['rubric_mean'], 2)} | {' / '.join(str(j.get(k)) for k in RUBRIC)} | {j.get('reason', '')} |"
    L += ["", "## 3. 질문 예시", "", "### 상위 3", "", "| ID | 종류 | 질문 | 평균 | 명확·관련·답변·한문장·비편향 | 판정 이유 |", "|---|---|---|---|---|---|"]
    L += [qline(q) for q in ranked[::-1][:3]]
    L += ["", "### 하위 3", "", "| ID | 종류 | 질문 | 평균 | 명확·관련·답변·한문장·비편향 | 판정 이유 |", "|---|---|---|---|---|---|"]
    L += [qline(q) for q in ranked[:3]]
    L += ["", f"## 4. 사람 검토 묶음 {len(sample)}개 (M-44용 — 미측정, 사람 평가 전)", "",
          f"- 선정: {R.get('human_review_note', '')}",
          "- 채점은 xlsx `사람검토` 시트에서 한다(루브릭 5항목·근거성 노란 칸, AI 점수는 숨김 열 P — 사람이 먼저 채점). 리포트 평가 묶음과 합쳐 최대 20개.", "",
          "| # | ID | 종류 | 질문 | 선정 이유 | 사람 점수 |", "|---|---|---|---|---|---|"]
    for i, q in enumerate(sample, 1):
        L.append(f"| {i} | {q['qid']} | {CAT_LABEL[q['category']]} | {q['text']} | {q.get('pick_why', '')} |  |")
    L += ["", "## 5. 세트 목록", "", "| 세트 | 기업 | 직무 | 가상 이력서 | 생성 초 | 이력서 대체 | 처리 기록 |", "|---|---|---|---|---|---|---|"]
    for s in sets:
        L.append(f"| {s['set_id']} | {s['company']} | {s['role_code']} | {s['resume']} | {s['seconds']} | {s['resume_fallback'] or '-'} | {' / '.join(s['log'])[:300]} |")
    L += ["", "## 6. 원인 분석·개선안", "", "(아래에 작성)", ""]
    return "\n".join(L)


def cmd_compare(a):
    """기준선 json과 수정 후 json → 전후 비교 표(md)를 수정 후 md 끝에 붙인다."""
    B = json.load(open(a.base, encoding="utf-8"))
    N = json.load(open(a.new, encoding="utf-8"))

    def vals(R):
        M = R["metrics"]
        r31 = M["M-31"]["redis"] if M["M-31"]["redis"].get("n") else M["M-31"]["all"]
        qs = R["questions"]
        return [("M-31 검색 관련도(Redis) 평균", r31.get("mean"), r31.get("ci95"), "score", "≥4.0"),
                ("M-31 전체(DB 포함) 평균", M["M-31"]["all"].get("mean"), M["M-31"]["all"].get("ci95"), "score", "-"),
                ("M-32 근거성(규칙 ∧ 판정)", M["M-32"]["p"], M["M-32"]["wilson95"], "prop", "≥0.95"),
                ("M-32 규칙만", M["M-32"]["rule_only"]["p"], M["M-32"]["rule_only"]["wilson95"], "prop", "-"),
                ("M-33 루브릭 평균", M["M-33"].get("mean"), M["M-33"].get("ci95"), "score", "≥4.0 · 하한 ≥3.7"),
                ("M-34 중복 쌍 비율", M["M-34"]["p"], M["M-34"]["wilson95"], "prop_low", "≤0.05"),
                ("M-35 대체 경로 비율", M["M-35"]["p"], M["M-35"]["wilson95"], "prop_low", "≤0.20"),
                ("M-37 카테고리 분포", M["M-37"]["p"], M["M-37"]["wilson95"], "prop", "1.00"),
                ("질문 합격률", R["pass_rate"]["p"], R["pass_rate"]["wilson95"], "prop", "-"),
                ("하드: 차단 목록 위반(건)", sum(1 for q in qs if q.get("rule_blocklist")), None, "count", "0"),
                ("하드: 한 문장 규칙 위반(건)", sum(1 for q in qs if not q.get("rule_one_sentence", True)), None, "count", "0"),
                ("하드: 근거 규칙 실패(건)", sum(1 for q in qs if not q.get("rule_grounded")), None, "count", "0"),
                ("기업 질문 근거 규칙 실패(건)", sum(1 for q in qs if q["category"] == "company" and not q.get("rule_grounded")), None, "count", "0")]
    def label(R, path):  # 컨테이너 /tmp에 다른 이름으로 복사해 돌려도 원래 결과 이름이 나오게
        nm = R["meta"].get("run_name") or os.path.splitext(os.path.basename(path))[0]
        if not re.search(r"\d{4}-\d{2}-\d{2}", nm):
            nm = "rag_questions_" + R["meta"].get("date", "")
        return nm + ".json"
    tag = N["meta"].get("run_tag") or _run_info(os.path.basename(a.new))[1] or N["meta"].get("date", "")
    L = ["", f"## 7. 기준선 대비 전후 비교 ({tag})", "",
         f"- 기준선: `{label(B, a.base)}` · 수정 후: `{label(N, a.new)}` · 생성·판정 모델 동일(qwen3:4b), 같은 공고·가상 이력서 짝",
         "- 회귀 판정: 점수 −0.2점 / 비율 −5%p(낮을수록 좋은 지표는 +5%p)보다 나빠지면 '회귀'", "",
         "| 지표 | 목표 | 기준선 | 95% CI | 수정 후 | 95% CI | 변화 | 판정 |", "|---|---|---|---|---|---|---|---|"]
    for (name, bv, bci, kind, tgt), (_, nv, nci, _, _) in zip(vals(B), vals(N)):
        if bv is None or nv is None:
            L.append(f"| {name} | {tgt} | {_f(bv)} | {_ci(bci)} | {_f(nv)} | {_ci(nci)} | - | - |")
            continue
        d = nv - bv
        if kind == "score":
            j = "회귀" if d < -0.2 else ("개선" if d > 0.2 else "유지")
        elif kind == "prop":
            j = "회귀" if d < -0.05 else ("개선" if d > 0.05 else "유지")
        elif kind == "prop_low":
            j = "회귀" if d > 0.05 else ("개선" if d < -0.05 else "유지")
        else:
            j = "회귀" if d > 0 else ("개선" if d < 0 else "유지")
        L.append(f"| {name} | {tgt} | {_f(bv)} | {_ci(bci)} | {_f(nv)} | {_ci(nci)} | {d:+.3f} | {j} |")
    L += [""] if tag != "fix1" else []
    L += ["- 생성은 확률적(temperature 0.4)이라 같은 입력이어도 표본이 달라진다. 변화가 CI 안에 있으면 '유지'로 본다.", ""] if tag != "fix1" else [
          "", "- **M-31·M-35: 수정 1번(`pick_bank` 직무 필터) 미적용이라 변화 없음을 예상한다.** DB `question_bank`에 role_code 열이 없고 직무 문항이 AI 계열뿐이다."
          " 근본 해결(backend·data_sci 직무 문항을 regularDB에 추가해 sync하고 role_code 열 추가)은 별도 작업으로 사용자에게 올린다.",
          "- 수정 ③(`_pick_redis`에서 두 문장 은행 문항 제외)으로 Redis ai_ax 직무 후보가 222개에서 218개로 줄어, M-35가 조금 오를 수 있다.",
          "- 생성은 확률적(temperature 0.4)이라 같은 입력이어도 표본이 달라진다. 변화가 CI 안에 있으면 '유지'로 본다.",
          "- 수정 4번(이력서 질문 중복)은 개선안으로만 남기고 적용하지 않았다.", ""]
    md = os.path.join(os.path.dirname(a.new), os.path.splitext(os.path.basename(a.new))[0] + ".md")
    with open(md, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("generate"); g.add_argument("--out", required=True); g.add_argument("--sets", type=int, default=0)
    j = sp.add_parser("judge"); j.add_argument("--gen", required=True); j.add_argument("--out", required=True); j.add_argument("--model", default="qwen3:8b")
    j.add_argument("--second", action="store_true")
    j.add_argument("--only", default="", choices=["", "quality", "retrieval"])
    i = sp.add_parser("internal"); i.add_argument("--out", required=True); i.add_argument("--model", default="qwen3:8b"); i.add_argument("--gen", default="")
    r = sp.add_parser("report"); r.add_argument("--dir", required=True); r.add_argument("--outdir", required=True); r.add_argument("--name", required=True)
    r.add_argument("--xlsx", default="", help="기본 = --name에서 rag_questions_ → rag_question_eval_ (예: rag_question_eval_2026-11-13_fix1.xlsx)")
    c = sp.add_parser("compare"); c.add_argument("--base", required=True); c.add_argument("--new", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "compare":
        return cmd_compare(a)
    db.pool.open(wait=True)            # CLI 실행: 앱 시작 때 여는 연결 풀을 직접 연다(읽기만 한다)
    {"generate": cmd_generate, "judge": cmd_judge, "internal": cmd_internal, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
