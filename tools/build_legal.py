"""legal/*.md → main/privacy.html · main/terms.html (v0.9).

사용: python tools/build_legal.py
원본은 legal/ 의 MD. 화면은 기존 3색 토큰(shared/styles.css)과 기존 표 스타일(.tbl)만 쓴다(새 색 없음).
"""
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
PAGES = [("legal/privacy_policy.md", "main/privacy.html", "개인정보 처리방침"),
         ("legal/terms_of_service.md", "main/terms.html", "이용약관")]

TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · headhunterAI</title>
<link rel="icon" href="/images/headhuntgerAI_log.png.png">
<link rel="stylesheet" crossorigin href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<link rel="stylesheet" href="/shared/styles.css">
<style>
.legal {{ max-width: 860px; margin: 0 auto; padding: 40px 16px 80px; }}
.legal h1 {{ font-weight: 700; font-size: 28px; margin: 18px 0 6px; }}
.legal h2 {{ font-weight: 700; font-size: 19px; margin: 34px 0 10px; }}
.legal p, .legal li {{ color: var(--muted); }}
.legal b, .legal strong {{ color: var(--white); }}
.legal .tbl-wrap {{ overflow-x: auto; }}
.legal .meta {{ color: var(--muted2); font-size: 13px; }}
.legal .draft {{ border: 1px solid var(--line-strong); border-radius: var(--radius); padding: 10px 14px; font-size: 13.5px; color: var(--muted); }}
</style>
</head>
<body>
<main class="legal">
  <a class="logo" href="/"><img src="/images/headhunterAI_logo_crop.png" alt="headhunterAI" style="height:36px"></a>
  {draft}
  {body}
  <p class="meta">문서 버전 {version} · {date}</p>
</main>
</body>
</html>
"""


def front(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    meta = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M)) if m else {}
    return meta, text[m.end():] if m else text


def build(src, dst, title):
    raw = (ROOT / src).read_bytes().decode("utf-8")
    meta, body = front(raw)
    html = markdown.markdown(body, extensions=["tables"])
    html = html.replace("<table>", '<div class="tbl-wrap"><table class="tbl">').replace("</table>", "</table></div>")
    draft = f'<p class="draft">{meta["status"]}</p>' if "초안" in meta.get("status", "") else ""
    out = TEMPLATE.format(title=title, draft=draft, body=html, version=meta.get("version", ""), date=meta.get("date", ""))
    (ROOT / dst).write_bytes(out.encode("utf-8"))
    print("built", dst, len(out))


if __name__ == "__main__":
    for s, d, t in PAGES:
        build(s, d, t)
    sys.exit(0)
