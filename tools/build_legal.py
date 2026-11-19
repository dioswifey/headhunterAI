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
/* 2026-11-19 사용자 지시: 글자 키우고, 잘 보이는 글꼴로, 자간(letter-spacing) 넣기.
   글꼴은 Pretendard를 명시적으로 지정하고, 검은 바탕에서 얇아 보이지 않게 본문을 Medium(500)으로,
   글자색도 --muted(62%)보다 밝은 78%로 올린다. 새 색이 아니라 기존 흰색의 투명도만 조정 */
.legal {{ max-width: 920px; margin: 0 auto; padding: 40px 20px 96px;
  font-family: var(--font); letter-spacing: .012em; }}
.legal h1 {{ font-weight: 700; font-size: 34px; line-height: 1.3; letter-spacing: -.005em; margin: 22px 0 10px; }}
.legal h2 {{ font-weight: 700; font-size: 23px; line-height: 1.4; letter-spacing: 0; margin: 46px 0 14px; }}
.legal h3 {{ font-weight: 600; font-size: 19px; line-height: 1.45; margin: 28px 0 10px; }}
.legal p, .legal li {{ color: rgba(255, 255, 255, .78); font-size: 17px; font-weight: 500;
  line-height: 1.85; letter-spacing: .012em; }}
.legal li {{ margin-bottom: 6px; }}
.legal ul, .legal ol {{ padding-left: 22px; }}
.legal b, .legal strong {{ color: var(--white); font-weight: 700; }}
.legal a {{ color: var(--neon); }}
.legal .tbl-wrap {{ overflow-x: auto; }}
.legal .tbl {{ font-size: 15.5px; letter-spacing: .012em; }}
.legal .tbl th, .legal .tbl td {{ padding: 13px 14px; line-height: 1.7; }}
.legal .tbl th {{ font-size: 14.5px; }}
.legal .meta {{ color: var(--muted2); font-size: 14px; margin-top: 40px; }}
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
    # 원본 md가 CRLF로 저장돼 있으면(Windows·core.autocrlf) 머리말 분리가 깨져 본문에 그대로 찍힌다
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    meta = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M)) if m else {}
    return meta, text[m.end():] if m else text


def build(src, dst, title):
    raw = (ROOT / src).read_bytes().decode("utf-8")
    meta, body = front(raw)
    html = markdown.markdown(body, extensions=["tables"])
    html = html.replace("<table>", '<div class="tbl-wrap"><table class="tbl">').replace("</table>", "</table></div>")
    # 2026-11-19 사용자 지시: 화면에 '초안 — 출시 전 전문가 확인 필요…' 배너를 띄우지 않는다.
    # MD 머리말의 status는 내부 기록용으로 그대로 두고, 공개 페이지에만 안 내보낸다
    draft = ""
    out = TEMPLATE.format(title=title, draft=draft, body=html, version=meta.get("version", ""), date=meta.get("date", ""))
    (ROOT / dst).write_bytes(out.encode("utf-8"))
    print("built", dst, len(out))


if __name__ == "__main__":
    for s, d, t in PAGES:
        build(s, d, t)
    sys.exit(0)
