"""ExpertHire Preparation API 어댑터 — 질문 은행 중 IT/코딩(`category=Software/Coding`)만 가져온다.

- 공식 REST API: `GET https://prep-api.experthire.cloud/v1/question-bank/questions` (목록) · `/questions/{id}` (상세)
- 인증: `Authorization: Bearer $EH_SECRET_KEY` — 키는 저장소 루트 `.env`의 `EH_SECRET_KEY`에만 둔다(코드·문서에 쓰지 않음)
- 페이지: `limit`(최대 100) + `has_more`가 참인 동안 `next_cursor`를 `starting_after`로 넘김. 정렬은 고정(`sort=frequency`)
- 문서(2026-11-05 확인): `statement_html`은 원문이 아니라 ExpertHire가 AI로 쓴 설명, `statement_available`이 false면 `url`(LeetCode 원본)만 있음.
  시작 코드·테스트 케이스는 주지 않는다 → 수집 에이전트에서는 'other / 검토 필요'로 저장된다(문제 설명·난이도·주제·출처만)
- 403 `module_not_enabled` = 키에 question_bank 모듈이 꺼져 있음, 429 = 요청 한도 → 수집 에이전트가 차단으로 처리하고 멈춘다
"""
import os

from bs4 import BeautifulSoup

SITE = {"name": "experthire", "label": "ExpertHire", "hosts": ["prep-api.experthire.cloud"], "api": True}
BASE = "https://prep-api.experthire.cloud/v1/question-bank"
CATEGORY = "Software/Coding"
LEVEL = {"easy": 1, "medium": 2, "hard": 3}


def headers():
    key = os.environ.get("EH_SECRET_KEY", "").strip()
    if not key:
        raise RuntimeError("EH_SECRET_KEY 없음 — 저장소 루트 .env에 넣고 docker compose up -d api")
    return {"Authorization": f"Bearer {key}"}


def list_ids(fetcher, limit, kinds=("python_func",)):
    ids, cursor = [], None
    while len(ids) < limit:
        q = f"{BASE}/questions?category={CATEGORY.replace('/', '%2F')}&sort=frequency&limit={min(100, limit - len(ids))}"
        if cursor:
            q += f"&starting_after={cursor}"
        d = fetcher.get_json(q, headers=headers())
        rows = d.get("data") or []
        ids += [(str(r["id"]), "other") for r in rows if r.get("id")]
        cursor = d.get("next_cursor")
        if not rows or not d.get("has_more") or not cursor:
            break
    return ids[:limit]


def problem_url(source_id, kind="other"):
    return f"{BASE}/questions/{source_id}"


def fetch(fetcher, url):
    return fetcher.get_json(url, headers=headers())


def parse(raw, source_id):
    q = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    html = q.get("statement_html") if q.get("statement_available", True) else ""
    text = BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True)
    topics = q.get("topics") or q.get("tags") or []
    if topics and isinstance(topics[0], dict):
        topics = [t.get("name") or t.get("slug") or "" for t in topics]
    diff = str(q.get("difficulty") or "").lower()
    src = q.get("source") or ""
    issues = ["ExpertHire: 시작 코드·테스트 케이스를 주지 않음(설명만)"]
    if q.get("statement_source") == "ai":
        issues.append("설명은 ExpertHire가 AI로 쓴 글(원문 아님)")
    if not html:
        issues.append("statement_available=false — 원본 url만 있음")
    return {
        "title": q.get("title") or "",
        "statement": text,
        "difficulty": diff,
        "level_num": LEVEL.get(diff),
        "category": ", ".join([CATEGORY, f"source={src}"] + [t for t in topics if t]),
        "languages": [],
        "starter": "",
        "examples_header": [],
        "examples_rows": [],
        "origin_url": q.get("url") or "",
        "issues": issues,
    }
