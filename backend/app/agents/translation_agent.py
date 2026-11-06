"""언어 번역 에이전트 — Skills 패턴 (LCEL, v0.7)

    요청(원문 필드 · source_lang · target_lang=지원자가 고른 언어)
        → Agent: select_skills (스킬 목록의 머리말 kind·source_lang·target_lang으로 고름)
            ├─ Skill A  translate_en_ko   영어 → 한국어 의역
            ├─ Skill B  translate_ko_en   한국어 → 영어 의역
            ├─ Skill C  fluency_ko        어색하지 않게 하기(한국어 다듬기)
            ├─ Skill D  fluency_en        어색하지 않게 하기(영어 다듬기)
            └─ (context) tech_terms       기술 용어 규칙 — 번역·다듬기 스킬에 함께 붙음
        (번역 전) scope_ko_en  범위 필터 — 언어는 한국어·영어만, 나라·문화는 한국·영어권만.
                               질문 자체가 걸리면 제외, 곁가지 칸만 걸리면 한국 맥락으로 바꿈. `scope()`, 질문 은행 sync·ingest가 부름
        → Response(번역 필드 · 쓴 스킬 · 검사 결과)

스킬 = `prompts/skills/translation/*.md` (머리말 name·description·kind·source_lang·target_lang + 본문 = 지시문).
**자동 등록**: 에이전트가 실행될 때마다 스킬 폴더를 다시 훑는다(파일 목록·수정 시각이 바뀌었을 때만 다시 읽음).
  → 스킬 파일을 넣거나 고치면 다음 실행부터 자동으로 쓰인다. 코드 수정·재시작 없음
    (로컬은 docker-compose가 스킬 폴더를 컨테이너에 직접 연결, 배포본은 이미지에 포함되므로 재배포 때 반영).
  → 지원 언어도 스킬 파일에서 계산한다(translate 스킬의 source_lang·target_lang).
    예: translate_ko_ja.md + fluency_ja.md를 넣으면 ko→ja가 바로 된다. 모델 호출은 `llm.chat_json`만.

처리 순서: translate 스킬 → 규칙 검사(걸리면 지적 사항을 붙여 1회 재번역) → fluency 스킬(다듬기)
          → 다듬은 문장 규칙 검사(통과 못 하면 번역 초안 유지) → 응답. 둘 다 실패하면 ok=False(호출한 쪽이 재시도).

확인(컨테이너 안):
    python -m app.agents.translation_agent --source ko --target en --question "팀 프로젝트에서 의견이 충돌했을 때 어떻게 조율했는지 설명해 주세요."
"""
import argparse
import json
import re
import sys
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from .. import config, llm, prompts

SKILL_DIR = prompts.DIR / "skills" / "translation"
FIELDS = ("question", "intent", "follow_up")

HANGUL = re.compile(r"[가-힣]")
HANGUL_RUN = re.compile(r"[가-힣]{2,}")
LATIN_SENTENCE = re.compile(r"[A-Za-z]{4,}(?:\s+[A-Za-z]{2,}){4,}")      # 영어 문장이 통째로 남음
KO_AWKWARD = [
    # '~나요/~가요/~까요/~래요/~죠'로 끝나는 반말에 가까운 의문형 전부 (QA-10: '다른가요?'를 놓쳤던 것 보완)
    (re.compile(r"(나요|가요|까요|래요|죠)\s*[?.]?$"), "끝맺음이 '~나요?/~가요?/~까요?'다. '~설명해 주세요.' 또는 '~하시겠습니까?'로 바꿀 것"),
    (re.compile(r"당신|그것을|되어지|에 대해서 어떻게 생각"), "번역투 표현(당신·그것을·되어지다 등)이 있다"),
    (re.compile(r"생산 환경"), "'생산 환경'은 production 직역이다. '실서비스' 또는 '운영 환경'으로 쓸 것"),
]
# 기계적으로 고칠 수 있는 것은 모델에게 다시 시키지 않고 코드가 고친다
KO_PARTICLE_SPACE = re.compile(r"([A-Za-z0-9)\]]) +(을|를|이|가|은|는|에|의|로|으로|와|과|도|에서|에게)(?=[\s,.?!]|$)")


def normalize(fields, to):
    """띄어쓰기·공백 정리(규칙). 'chunk 를' → 'chunk를', 연속 공백 → 한 칸."""
    out = {}
    for k, v in fields.items():
        s = re.sub(r"\s+", " ", (v or "")).strip() if isinstance(v, str) else v
        if to == "ko" and isinstance(s, str):
            s = KO_PARTICLE_SPACE.sub(r"\1\2", s)
        out[k] = s
    return out
EN_AWKWARD = [
    (re.compile(r"\bexplain about\b", re.I), "'explain about' is unnatural; use 'explain'"),
    (re.compile(r"\byou\s+(?:yourself\s+)?(?:have\s+)?experienced\s+experience\b", re.I), "redundant 'experienced experience'"),
]


# ---------------- 스킬 목록 (실행마다 자동 등록) ----------------
_CACHE = {"sig": None, "skills": {}}


def skills():
    """스킬 폴더의 *.md를 머리말로 등록한다. 파일 목록·수정 시각이 바뀌면 다시 읽는다."""
    files = sorted(SKILL_DIR.glob("*.md"))
    sig = tuple((p.name, p.stat().st_mtime_ns) for p in files)
    if sig == _CACHE["sig"]:
        return _CACHE["skills"]
    out = {}
    for p in files:
        text = p.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, flags=re.S)
        if not m:
            continue
        meta = dict(re.findall(r"^(\w+):\s*(.*?)\s*$", m.group(1), flags=re.M))
        meta["body"] = m.group(2).strip()
        meta["file"] = p.name
        out[meta.get("name", p.stem)] = meta
    _CACHE.update(sig=sig, skills=out)
    return out


def supported_pairs():
    """translate 스킬이 있는 (원문 언어, 목표 언어) 쌍 = 지원 언어."""
    return sorted({(s["source_lang"], s["target_lang"]) for s in skills().values()
                   if s.get("kind") == "translate" and s.get("source_lang") and s.get("target_lang")})


def languages():
    return sorted({lang for pair in supported_pairs() for lang in pair})


def _find(kind, **cond):
    for name, s in skills().items():
        if s.get("kind") == kind and all(s.get(k) == v for k, v in cond.items()):
            return name
    return None


# ---------------- 규칙 검사 ----------------
def check(fields, to, src):
    """유창성 규칙 검사. fields = {question_ko: ..}. 반환: 지적 사항 목록(빈 목록 = 통과)"""
    issues = []
    for f in FIELDS:
        s = (fields.get(f"{f}_{to}") or "").strip()
        if not s:
            if (src.get(f) or "").strip():
                issues.append(f"{f}_{to}가 비어 있다")
            continue
        if len(s) > 300:
            issues.append(f"{f}_{to}가 너무 길다(300자 이하)")
        if to == "ko":
            if not HANGUL.search(s):
                issues.append(f"{f}_{to}에 한국어가 없다")
            if LATIN_SENTENCE.search(s):
                issues.append(f"{f}_{to}에 영어 문장이 그대로 남아 있다. 기술 용어만 영어로 둘 것")
            rules = KO_AWKWARD if f != "intent" else KO_AWKWARD[1:]
            issues += [f"{f}_{to}: {msg}" for rx, msg in rules if rx.search(s)]
        else:
            if HANGUL_RUN.search(s):
                issues.append(f"{f}_{to} still contains Korean text")
            issues += [f"{f}_{to}: {msg}" for rx, msg in EN_AWKWARD if rx.search(s)]
    return issues


def _schema(to, fluency=False):
    keys = [f"{f}_{to}" for f in FIELDS]
    props = {k: {"type": "string"} for k in keys}
    if fluency:
        props.update(changed={"type": "boolean"}, notes={"type": "string"})
        keys = keys + ["changed"]
    return {"type": "object", "properties": props, "required": keys}


def _system(name, ctx_names):
    s = skills()
    return "\n\n".join([s[name]["body"]] + [s[c]["body"] for c in ctx_names if c in s])


def _src_block(x):
    lines = [f"competency: {x.get('competency', '')}"] if x.get("competency") else []
    lines += [f"{f}: {x['fields'].get(f, '')}" for f in FIELDS]
    return "\n".join(lines)


# ---------------- Agent 단계 ----------------
def _select_skills(x):
    skills()                                   # 실행할 때마다 스킬 폴더 자동 등록(바뀐 파일만 다시 읽음)
    s, t = x["source_lang"], x["target_lang"]
    if s == t:
        return {"translate": None, "fluency": None, "context": []}
    tr = _find("translate", source_lang=s, target_lang=t)
    if not tr:
        raise ValueError(f"no translate skill for {s}->{t}")
    return {"translate": tr, "fluency": _find("fluency", target_lang=t),
            "context": [n for n, v in skills().items() if v.get("kind") == "context"]}


def _run_translate(x):
    sk, to = x["skills"], x["target_lang"]
    if not sk["translate"]:
        return {"draft": {f"{f}_{to}": x["fields"].get(f, "") for f in FIELDS}, "issues": [], "tries": 0}
    system, user, out, issues = _system(sk["translate"], sk["context"]), _src_block(x), {}, ["not run"]
    tries = 0
    for attempt in range(2):
        tries += 1
        extra = "" if attempt == 0 else (
            "\n\n[이전 번역의 문제 — 고쳐서 다시 번역 / Fix these issues]\n- " + "\n- ".join(issues)
            + "\n[이전 번역 / previous]\n" + json.dumps(out, ensure_ascii=False))
        try:
            out = normalize(llm.chat_json(system, user + extra, _schema(to), timeout=90, temperature=0.2,
                                          model=config.TRANSLATE_MODEL), to)
            issues = check(out, to, x["fields"])
        except llm.LLMError:
            issues = ["llm error"]
        if not issues:
            break
    return {"draft": {k: (out.get(k) or "").strip() for k in _schema(to)["required"]}, "issues": issues, "tries": tries}


def _run_fluency(x):
    """Skill C/D — 어색하지 않게 하기. 다듬은 문장이 규칙을 통과할 때만 채택."""
    sk, to, tr = x["skills"], x["target_lang"], x["translated"]
    if not sk["fluency"] or tr["issues"] == ["llm error"]:
        return {"final": tr["draft"], "changed": False, "notes": "", "issues": tr["issues"]}
    user = (f"[source ({x['source_lang']})]\n{_src_block(x)}\n\n[draft ({to})]\n"
            + json.dumps(tr["draft"], ensure_ascii=False))
    if tr["issues"]:
        user += "\n\n[규칙 검사에서 걸린 부분 / rule issues]\n- " + "\n- ".join(tr["issues"])
    try:
        out = llm.chat_json(_system(sk["fluency"], sk["context"]), user, _schema(to, fluency=True),
                            timeout=90, temperature=0.2, model=config.TRANSLATE_MODEL)
    except llm.LLMError:
        return {"final": tr["draft"], "changed": False, "notes": "fluency skill failed", "issues": tr["issues"]}
    keys = [f"{f}_{to}" for f in FIELDS]
    # 다듬기 스킬이 비워 둔 칸은 번역 초안으로 채운다(다듬을 것이 없었던 칸)
    revised = normalize({k: (out.get(k) or "").strip() or tr["draft"].get(k, "") for k in keys}, to)
    rev_issues = check(revised, to, x["fields"])
    if not rev_issues:
        return {"final": revised, "changed": bool(out.get("changed")) and revised != tr["draft"],
                "notes": (out.get("notes") or "").strip(), "issues": []}
    return {"final": tr["draft"], "changed": False, "notes": "polish rejected: " + "; ".join(rev_issues), "issues": tr["issues"]}


def _response(x):
    fl = x["polished"]
    used = [n for n in (x["skills"]["translate"], x["skills"]["fluency"]) if n] + x["skills"]["context"]
    return {"source_lang": x["source_lang"], "target_lang": x["target_lang"], "fields": fl["final"],
            "ok": not fl["issues"], "issues": fl["issues"], "skills_used": used,
            "fluency_changed": fl["changed"], "fluency_notes": fl["notes"], "translate_tries": x["translated"]["tries"],
            "model": config.TRANSLATE_MODEL}


translation_agent = (
    RunnablePassthrough.assign(skills=RunnableLambda(_select_skills).with_config(run_name="select_skills"))
    | RunnablePassthrough.assign(translated=RunnableLambda(_run_translate).with_config(run_name="skill_translate"))
    | RunnablePassthrough.assign(polished=RunnableLambda(_run_fluency).with_config(run_name="skill_fluency"))
    | RunnableLambda(_response).with_config(run_name="response")
).with_config(run_name="translation_agent")


# ---------------- 범위 필터 스킬 (kind: scope, 번역 전) ----------------
# 질문 은행은 IT 면접용: 언어는 한국어·영어만, 나라·문화 맥락은 한국·영어권만.
# ① 코드: 스킬 머리말 terms_question에 질문이 걸리면 drop(모델 호출 없음)
# ② 질문은 괜찮고 곁가지 칸(terms_side)에만 걸리면 스킬로 adapt(한국 맥락으로 바꿈)·drop 판단
# ③ 모델 실패·바꾼 뒤에도 걸림 → drop (다음 sync 때 다시 판단)
SCOPE_FIELDS = ("question", "intent", "follow_up", "model_answer", "red_flags")


def _scope_skill():
    name = _find("scope")
    return (name, skills()[name]) if name else (None, None)


def scope(fields):
    """반환: {decision: keep|adapt|drop, reason, fields(adapt면 바뀐 칸), hits, by: rule|skill|none}"""
    name, sk = _scope_skill()
    same = {f: fields.get(f, "") or "" for f in SCOPE_FIELDS}
    if not sk:
        return {"decision": "keep", "reason": "", "fields": same, "hits": [], "by": "none"}
    rq = re.compile(sk.get("terms_question", "(?!)"), re.I)
    rs = re.compile(sk.get("terms_side", "(?!)"), re.I)
    qhit = sorted({m.group(0) for m in rq.finditer(same["question"])})
    if qhit:
        return {"decision": "drop", "reason": f"질문 자체가 한국·영어권 밖 언어·나라를 다룸({', '.join(qhit)})",
                "fields": same, "hits": qhit, "by": "rule"}
    side = {f: sorted({m.group(0) for m in rs.finditer(same[f])}) for f in SCOPE_FIELDS if f != "question"}
    side = {f: h for f, h in side.items() if h}
    if not side:
        return {"decision": "keep", "reason": "", "fields": same, "hits": [], "by": "none"}
    hits = sorted({h for v in side.values() for h in v})
    schema = {"type": "object", "properties": {"decision": {"type": "string", "enum": ["keep", "adapt", "drop"]},
                                               "reason": {"type": "string"},
                                               **{f: {"type": "string"} for f in SCOPE_FIELDS}},
              "required": ["decision", "reason", *SCOPE_FIELDS]}
    user = ("[걸린 칸 / flagged fields] " + json.dumps(side, ensure_ascii=False) + "\n\n[문항 / item]\n"
            + json.dumps(same, ensure_ascii=False))
    try:
        out = llm.chat_json(sk["body"], user, schema, timeout=90, temperature=0.1, model=config.TRANSLATE_MODEL)
    except llm.LLMError:
        return {"decision": "drop", "reason": "범위 스킬 호출 실패 → 일단 제외(다음 sync 때 다시 판단)",
                "fields": same, "hits": hits, "by": "skill_failed"}
    dec = out.get("decision")
    reason = (out.get("reason") or "").strip()
    if dec == "adapt":
        new = dict(same)
        for f in side:                                  # 걸린 칸만 바꾼다. 질문·다른 칸은 원문 그대로
            new[f] = (out.get(f) or "").strip() or same[f]
        left = sorted({m.group(0) for f in side for m in rs.finditer(new[f])})
        if left or new == same:
            return {"decision": "drop", "reason": f"한국 맥락으로 바꾸지 못함({', '.join(left) or '변경 없음'})",
                    "fields": same, "hits": hits, "by": "skill"}
        return {"decision": "adapt", "reason": reason, "fields": new, "hits": hits, "by": "skill"}
    if dec == "keep":
        return {"decision": "keep", "reason": reason, "fields": same, "hits": hits, "by": "skill"}
    return {"decision": "drop", "reason": reason or "범위 밖", "fields": same, "hits": hits, "by": "skill"}


def translate(fields, source_lang, target_lang, competency=""):
    """다른 모듈에서 부르는 진입점. target_lang = 지원자가 고른 언어."""
    if source_lang != target_lang and (source_lang, target_lang) not in supported_pairs():
        raise ValueError(f"unsupported language: {source_lang}->{target_lang} (skills: {supported_pairs()})")
    return translation_agent.invoke({"fields": fields, "source_lang": source_lang,
                                     "target_lang": target_lang, "competency": competency})


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--source", default="ko")
    p.add_argument("--target", default="en")
    p.add_argument("--question", default="")
    p.add_argument("--intent", default="")
    p.add_argument("--follow-up", default="")
    p.add_argument("--list-skills", action="store_true")
    a = p.parse_args(argv)
    if a.list_skills:
        print(json.dumps({"languages": languages(), "pairs": supported_pairs(),
                          "skills": {n: {k: v for k, v in s.items() if k != "body"} for n, s in skills().items()}},
                         ensure_ascii=False, indent=1))
        return
    out = translate({"question": a.question, "intent": a.intent, "follow_up": a.follow_up}, a.source, a.target)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
