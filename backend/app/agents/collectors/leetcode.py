"""LeetCode 어댑터 (2026-11-05 상태: **수집 불가 — 사이트가 자동 접근을 403으로 막음**).

- robots.txt가 `/graphql`·`/api/`를 금지 → 목록 API를 쓰지 않는다. 문제 slug는 실행할 때 `--ids two-sum ...`으로 직접 준다
- 문제 페이지 `/problems/{slug}/`는 robots.txt상 허용이지만, 일반 요청에 403(봇 차단)을 돌려준다
  → 수집 에이전트의 fetcher가 '차단'으로 처리하고 이 사이트를 멈춘다. 차단 우회(브라우저 위장·지문 조작 등)는 하지 않는다
- 접근이 열려 있을 때를 위한 파서(Next.js `__NEXT_DATA__`)만 둔다 — 실제 응답으로 검증하지 못함
"""
import json
import re

from bs4 import BeautifulSoup

SITE = {"name": "leetcode", "label": "LeetCode", "hosts": ["leetcode.com"]}
BASE = "https://leetcode.com"
LEVEL = {"Easy": 1, "Medium": 2, "Hard": 3}


def list_ids(fetcher, limit, kinds=("python_func",)):
    return []          # 목록 API(/graphql)는 robots.txt 금지 → --ids로 slug를 직접 준다


def problem_url(source_id, kind="python_func"):
    return f"{BASE}/problems/{source_id}/"


def parse(html, source_id):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    q = {}
    if m:
        data = json.loads(m.group(1))
        for item in (data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries") or []):
            cand = (item.get("state", {}).get("data") or {}).get("question") or {}
            if cand.get("content"):
                q = cand
                break
    content = BeautifulSoup(q.get("content") or "", "html.parser")
    starter = next((s.get("code", "") for s in q.get("codeSnippets") or [] if s.get("langSlug") == "python3"), "")
    return {
        "title": q.get("title") or "",
        "statement": content.get_text("\n", strip=True),
        "difficulty": q.get("difficulty") or "",
        "level_num": LEVEL.get(q.get("difficulty")),
        "category": ", ".join(t.get("name", "") for t in q.get("topicTags") or []),
        "languages": sorted(s.get("langSlug") for s in q.get("codeSnippets") or []),
        "starter": starter,
        "examples_header": [],          # LeetCode 예제는 문장 속 'Input: … Output: …' 형식 → 표가 없어 검토 필요로 분류된다
        "examples_rows": [],
    }
