"""프로그래머스 스쿨 코딩테스트 연습 어댑터.

- 목록: `/api/v2/school/challenges/` (robots.txt 허용 경로, 언어 필터 `languages[]=python3` | `mysql`)
- 문제: `/learn/courses/30/lessons/{id}?language=python3` — 서버가 HTML로 그려 주므로 브라우저(Playwright) 없이 읽힌다
"""
import re

from bs4 import BeautifulSoup

SITE = {"name": "programmers", "label": "프로그래머스", "hosts": ["school.programmers.co.kr"]}
BASE = "https://school.programmers.co.kr"
LIST_LANG = {"python_func": "python3", "sql": "mysql"}
PAGE_LANG = {"python_func": "python3", "sql": "mysql"}


def list_ids(fetcher, limit, kinds=("python_func",)):
    ids = []
    for kind in kinds:
        page = 1
        while len(ids) < limit:
            d = fetcher.get_json(f"{BASE}/api/v2/school/challenges/?page={page}&perPage=20"
                                 f"&order=recent&languages[]={LIST_LANG[kind]}")
            rows = d.get("result") or []
            if not rows:
                break
            for r in rows:
                if len(ids) >= limit:
                    break
                ids.append((str(r["id"]), kind))
            if page >= int(d.get("totalPages") or 1):
                break
            page += 1
    return ids


def problem_url(source_id, kind="python_func"):
    return f"{BASE}/learn/courses/30/lessons/{source_id}?language={PAGE_LANG.get(kind, 'python3')}"


def _examples(desc):
    """'입출력 예' 제목 바로 뒤 표 → (머리글, 행들). 셀은 원문 문자열 그대로."""
    for h in desc.find_all(["h5", "h4", "h3", "h6"]):
        if h.get_text(strip=True) == "입출력 예":
            table = h.find_next("table")
            if not table:
                break
            header = [th.get_text(" ", strip=True) for th in table.find_all("th")]
            rows = [[td.get_text(" ", strip=True) for td in tr.find_all("td")] for tr in table.find_all("tr")]
            return header, [r for r in rows if r]
    return [], []


def parse(html, source_id):
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(attrs={"data-lesson-title": True}) or soup
    desc = soup.select_one(".guide-section-description .markdown") or soup.select_one(".guide-section-description")
    header, rows = _examples(desc) if desc else ([], [])
    code = soup.find("textarea", id="code")
    langs = sorted({el.get("data-language") for el in soup.find_all(attrs={"data-language": True}) if el.get("data-language")})
    level = root.get("data-challenge-level") if root is not soup else None
    title = root.get("data-lesson-title") if root is not soup else ""
    if not title and soup.title:
        title = re.sub(r"^코딩테스트 연습 - | \| 프로그래머스 스쿨$", "", soup.title.get_text(strip=True))
    return {
        "title": title or "",
        "statement": desc.get_text("\n", strip=True) if desc else "",
        "difficulty": f"Lv.{level}" if level is not None else "",
        "level_num": int(level) if level not in (None, "") else None,
        "category": (root.get("data-challenge-category") or "") if root is not soup else "",
        "languages": langs,
        "starter": code.get_text() if code else "",
        "examples_header": header,
        "examples_rows": rows,
    }
