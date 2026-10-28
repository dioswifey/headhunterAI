"""headhunterAI PRD/TRD PDF builder.

Usage:
    python tools/build_pdf.py <source.md> <output.pdf>

Pipeline: front matter + Markdown -> HTML (shared doc style) -> headless Chrome
(--dump-dom renders Mermaid to inline SVG) -> Chrome --print-to-pdf -> TOC page
numbers filled by a second print -> PyMuPDF header/footer stamping.
Requires: pip install markdown pymupdf ; Chrome or Edge installed.
"""
import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown
import pymupdf

HERE = Path(__file__).resolve().parent
CSS = (HERE / "doc_style.css").read_text(encoding="utf-8")
FONT_FILE = r"C:\Windows\Fonts\malgun.ttf"
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
]
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"


def chrome():
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    sys.exit("Chrome/Edge not found")


def split_front_matter(text):
    meta = {}
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for line in fm.strip().splitlines():
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
        return meta, body
    return meta, text


def md_to_html(body):
    # mermaid fences -> raw divs (kept out of the markdown parser)
    blocks = []

    def keep(m):
        blocks.append(m.group(1))
        return f"\n<div class=\"mermaid-slot\" data-i=\"{len(blocks) - 1}\"></div>\n"

    body = re.sub(r"```mermaid\n(.*?)```", keep, body, flags=re.S)
    out = markdown.markdown(body, extensions=["tables", "fenced_code", "attr_list", "md_in_html"])
    for i, src in enumerate(blocks):
        out = out.replace(f'<div class="mermaid-slot" data-i="{i}"></div>',
                          f'<div class="mermaid">{html.escape(src)}</div>')
    # group rows: "<td>TEXT {: colspan=N}</td><td></td>..." -> one spanning cell
    # (attr_list already sets colspan; drop the empty filler cells)
    out = re.sub(r'<td colspan="(\d)">(.*?)</td>(?:\s*<td></td>)+',
                 lambda m: f'<td class="group" colspan="{m.group(1)}">{m.group(2)}</td>', out)
    # cover key/value table has an empty header row
    out = re.sub(r"<thead>\s*<tr>\s*(<th></th>\s*)+</tr>\s*</thead>", "", out)
    return out


def page_html(meta, content, toc_numbers):
    def fill(m):
        return str(toc_numbers.get(m.group(1), "")) if toc_numbers else "00"
    content = re.sub(r"\{\{toc:([^}]+)\}\}", fill, content)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>{html.escape(meta.get('title', 'doc'))} {meta.get('version', '')}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css">
<style>{CSS}</style></head><body>{content}
<script src="{MERMAID_CDN}"></script>
<script>
if (window.mermaid && document.querySelector('.mermaid')) {{
  mermaid.initialize({{startOnLoad:false, theme:'neutral', securityLevel:'loose',
    flowchart:{{htmlLabels:false}}, er:{{useMaxWidth:true}},
    themeVariables:{{fontFamily:'Malgun Gothic, Pretendard, sans-serif', fontSize:'13px'}}}});
  mermaid.run({{querySelector:'.mermaid'}});
}}
</script></body></html>"""


def run_chrome(args):
    subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--run-all-compositor-stages-before-draw", *args],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def render(html_text, workdir, name):
    src = Path(workdir) / f"{name}.html"
    src.write_text(html_text, encoding="utf-8")
    # pass 1: let Mermaid draw, then freeze the DOM (SVG inline, scripts removed)
    if 'class="mermaid"' in html_text:
        dom = subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
                              "--virtual-time-budget=20000", "--dump-dom", src.as_uri()],
                             check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode("utf-8")
        dom = re.sub(r"<script.*?</script>", "", dom, flags=re.S)
        src.write_text("<!doctype html>" + dom, encoding="utf-8")
    pdf = Path(workdir) / f"{name}.pdf"
    run_chrome(["--no-pdf-header-footer", "--virtual-time-budget=8000",
                f"--print-to-pdf={pdf}", src.as_uri()])
    return pdf


def find_toc_pages(pdf_path, content):
    """Map each heading id used by {{toc:id}} to the page where its heading text appears."""
    keys = set(re.findall(r"\{\{toc:([^}]+)\}\}", content))
    titles = {}
    for m in re.finditer(r'<h[23][^>]*id="([^"]+)"[^>]*>(.*?)</h[23]>', content, flags=re.S):
        if m.group(1) in keys:
            titles[m.group(1)] = re.sub(r"<[^>]+>", "", m.group(2)).strip()
    doc = pymupdf.open(pdf_path)
    result = {}
    for key, title in titles.items():
        needle = title[:14]
        for i in range(2, doc.page_count):  # skip cover + toc
            if needle in doc[i].get_text():
                result[key] = i + 1
                break
    doc.close()
    return result


def stamp(pdf_in, pdf_out, meta):
    doc = pymupdf.open(pdf_in)
    n = doc.page_count
    header = meta.get("header", meta.get("title", ""))
    right = f"{meta.get('version', '')} · {meta.get('date', '')}"
    for i, page in enumerate(doc):
        if i == 0:
            continue
        w, h = page.rect.width, page.rect.height
        page.insert_font(fontname="mg", fontfile=FONT_FILE)
        page.insert_text((42, 30), header, fontname="mg", fontsize=7.5, color=(0.45, 0.45, 0.45))
        tw = pymupdf.get_text_length(right, fontname="helv", fontsize=7.5)
        page.insert_text((w - 42 - tw, 30), right, fontname="mg", fontsize=7.5, color=(0.45, 0.45, 0.45))
        page.draw_line((42, 36), (w - 42, 36), color=(0.85, 0.85, 0.85), width=0.5)
        foot = f"{i + 1} / {n}"
        fw = pymupdf.get_text_length(foot, fontname="helv", fontsize=8)
        page.insert_text(((w - fw) / 2, h - 22), foot, fontname="helv", fontsize=8, color=(0.45, 0.45, 0.45))
    doc.set_metadata({"title": f"{meta.get('title', '')} {meta.get('version', '')}",
                      "author": meta.get("author", "headhunterAI"), "creator": "tools/build_pdf.py"})
    doc.save(pdf_out, garbage=3, deflate=True)


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    meta, body = split_front_matter(src.read_text(encoding="utf-8"))
    content = md_to_html(body)
    with tempfile.TemporaryDirectory() as tmp:
        first = render(page_html(meta, content, None), tmp, "pass1")
        toc = find_toc_pages(first, content)
        final = render(page_html(meta, content, toc), tmp, "pass2")
        out.parent.mkdir(parents=True, exist_ok=True)
        stamp(final, out, meta)
    print(f"wrote {out} ({pymupdf.open(out).page_count} pages) toc={toc}")


if __name__ == "__main__":
    main()
