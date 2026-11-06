"""질문 은행 검색 결과 → 프롬프트 템플릿 → 회사 맞춤 질문 (LCEL, v0.7)

    build_query → retrieve(Redis KNN) → pick(중복 역량 제거) → prompt(ChatPromptTemplate, prompts/generate/question_templates.md)
                → generate(llm.chat_json) → validate(규칙 검사, 실패 문항은 은행 번역문으로 대체)

ADLC 6.1 그래프 1의 `retrieve` + `generate` 노드에 해당한다. 아직 면접 출제(`questions.build_set`)에는 연결하지 않았다.

확인(컨테이너 안):
    python -m app.agents.question_chain --job-id 1 --category job -n 4
"""
import argparse
import json
import re
import sys

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from .. import config, db, llm, prompts, vectorstore
from ..questions import BLOCKLIST

QUERY_INSTRUCT = "Instruct: Given a job posting and candidate keywords, retrieve relevant interview questions\nQuery: "
CATEGORY_LABEL = {"job": "직무 질문", "company": "기업 질문", "personality": "인성 질문"}
OUT_SCHEMA = {"type": "object", "properties": {"questions": {"type": "array", "items": {
    "type": "object", "properties": {"source_id": {"type": "string"}, "question": {"type": "string"},
                                     "company_facts": {"type": "array", "items": {"type": "string"}}},
    "required": ["source_id", "question", "company_facts"]}}}, "required": ["questions"]}


# ---------------- 템플릿 파일 읽기 ----------------
def load_sections(name="generate/question_templates"):
    text = (prompts.DIR / f"{name}.md").read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    parts = re.split(r"^## \[([^\]]+)\]\s*$", text, flags=re.M)
    return {parts[i].strip(): parts[i + 1].strip() for i in range(1, len(parts), 2)}


def build_prompt(role_code, category):
    s = load_sections()
    role_guide = s.get(f"role:{role_code}") or "직무 공고의 주요 업무·자격 요건에 맞는 실무 역량을 묻는다."
    tmpl = ChatPromptTemplate.from_messages([("system", s["system"]), ("human", s["human"])])
    return tmpl.partial(role_guide=role_guide, category_guide=s.get(f"category:{category}", ""),
                        category_label=CATEGORY_LABEL.get(category, category), seconds=str(config.QUESTION_SECONDS))


# ---------------- 단계 ----------------
def _query(x):
    j = x["job"]
    kw = ", ".join(x.get("resume_keywords") or [])
    return " ".join(filter(None, [j.get("role_name"), j.get("duties"), j.get("requirements"), kw]))[:1200]


def _retrieve(x):
    roles = [x["job"]["role_code"]] if x["category"] == "job" else ["common", x["job"]["role_code"]]
    v = llm.embed([QUERY_INSTRUCT + x["query"]])[0]
    return vectorstore.knn(v, k=max(12, x["n"] * 4), role_codes=roles, categories=[x["category"]])


def _pick(x):
    """역량·dup_group이 겹치지 않게 후보를 n×2개까지 남긴다(모델이 고를 여지)."""
    out, seen = [], set()
    for h in x["hits"]:
        g = h.get("dup_group") or h.get("competency") or h["doc_id"]
        if g in seen:
            continue
        seen.add(g)
        out.append(h)
        if len(out) >= x["n"] * 2:
            break
    return out


def _fmt_hits(hits):
    lines = []
    for h in hits:
        q = h.get("question_ko") or h["question"]
        lines.append(f"- id={h['doc_id']} | 역량: {h.get('competency', '')} | 질문: {q} | 평가 의도: {h.get('intent_ko') or h.get('intent', '')}")
    return "\n".join(lines) or "(검색 결과 없음)"


def _prompt_vars(x):
    j = x["job"]
    return {"company_name": j.get("company_name") or "우리 회사", "role_name": j.get("role_name") or "",
            "company_intro": (j.get("company_intro") or "")[:400], "duties": (j.get("duties") or "")[:400],
            "requirements": (j.get("requirements") or "")[:300], "n": str(x["n"]),
            "resume_keywords": ", ".join(x.get("resume_keywords") or []) or "(없음)",
            "bank_hits": _fmt_hits(x["picked"])}


def _generate(x):
    msgs = build_prompt(x["job"]["role_code"], x["category"]).invoke(x["vars"]).to_messages()
    try:
        return llm.chat_json(msgs[0].content, msgs[1].content, OUT_SCHEMA, timeout=90, temperature=0.4).get("questions", [])
    except llm.LLMError:
        return []


def _validate(x):
    j = x["job"]
    squash = re.sub(r"\s+", "", " ".join([j.get("company_name") or "", j.get("company_intro") or "",
                                          j.get("duties") or "", j.get("requirements") or ""]))
    by_id = {h["doc_id"]: h for h in x["picked"]}
    out, used = [], set()
    for g in x["generated"]:
        sid, q = (g.get("source_id") or "").strip(), (g.get("question") or "").strip()
        facts_ok = all(re.sub(r"\s+", "", f) in squash for f in g.get("company_facts") or [] if f and f.strip())
        if sid in by_id and sid not in used and 10 <= len(q) <= 160 and not BLOCKLIST.search(q) and facts_ok \
                and re.search(r"[가-힣]", q):
            used.add(sid)
            out.append({"source_id": sid, "question": q, "company_facts": g.get("company_facts") or [], "fallback": False})
        if len(out) == x["n"]:
            break
    for h in x["picked"]:          # 부족분: 은행 번역문(없으면 원문) 그대로
        if len(out) == x["n"]:
            break
        if h["doc_id"] not in used:
            used.add(h["doc_id"])
            out.append({"source_id": h["doc_id"], "question": h.get("question_ko") or h["question"],
                        "company_facts": [], "fallback": True})
    return out


question_chain = (
    RunnablePassthrough.assign(query=RunnableLambda(_query).with_config(run_name="build_query"))
    | RunnablePassthrough.assign(hits=RunnableLambda(_retrieve).with_config(run_name="retrieve"))
    | RunnablePassthrough.assign(picked=RunnableLambda(_pick).with_config(run_name="pick"))
    | RunnablePassthrough.assign(vars=RunnableLambda(_prompt_vars).with_config(run_name="prompt_vars"))
    | RunnablePassthrough.assign(generated=RunnableLambda(_generate).with_config(run_name="generate"))
    | RunnableLambda(_validate).with_config(run_name="validate")
).with_config(run_name="question_chain")


def job_context(job_id):
    return db.one("""SELECT j.title, j.duties, j.requirements, c.name AS company_name,
                            COALESCE(NULLIF(j.company_intro, ''), c.intro) AS company_intro,
                            r.code AS role_code, r.name AS role_name
                     FROM jobs j JOIN companies c ON c.id=j.company_id JOIN job_roles r ON r.code=j.role_code
                     WHERE j.id=%s""", job_id)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--category", default="job", choices=list(CATEGORY_LABEL))
    p.add_argument("-n", type=int, default=4)
    p.add_argument("--keywords", nargs="*", default=[])
    a = p.parse_args(argv)
    db.pool.open()                     # CLI 실행: 앱 시작 때 여는 연결 풀을 직접 연다
    job = job_context(a.job_id)
    if not job:
        raise SystemExit(f"job {a.job_id} not found")
    out = question_chain.invoke({"job": dict(job), "category": a.category, "n": a.n, "resume_keywords": a.keywords})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
