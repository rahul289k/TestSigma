"""Render docs/DESIGN.md to docs/DESIGN.pdf in the same theme as docs/how-it-works.html.

    .venv/bin/python docs/build_pdf.py

Markdown -> themed HTML (each "## N. Title" section becomes a numbered card, mermaid blocks are drawn by the
browser) -> A4 PDF via Playwright's Chromium. The diagrams load mermaid from a CDN, so this needs internet.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt
from playwright.sync_api import sync_playwright

DOCS = Path(__file__).resolve().parent
SRC, OUT = DOCS / "DESIGN.md", DOCS / "DESIGN.pdf"
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs"


def slug(text: str) -> str:
    """GitHub-style anchor, so links such as (#7-results) keep working."""
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return re.sub(r" ", "-", text)


def to_html(md_text: str) -> str:
    md = MarkdownIt("commonmark", {"html": True, "typographer": False}).enable("table")

    def fence(renderer, tokens, idx, options, env):
        tok = tokens[idx]
        lang = tok.info.strip().split(" ")[0]
        if lang == "mermaid":
            return f'<div class="diagram"><pre class="mermaid">{html.escape(tok.content)}</pre></div>\n'
        return f'<pre class="code"><code>{html.escape(tok.content)}</code></pre>\n'
    md.add_render_rule("fence", fence)

    tokens = md.parse(md_text)
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            tok.attrSet("id", slug(tokens[i + 1].content))
    body = md.renderer.render(tokens, md.options, {})
    body = body.replace("<hr />\n", "")
    # "## 3. Graph schema" -> numbered badge, as in the HTML guides.
    body = re.sub(r'<h2 id="([^"]+)">(\d+)\. (.*?)</h2>', r'<h2 id="\1"><span class="num">\2</span>\3</h2>', body)
    # Everything before the first h2 is the title block; each h2 starts a card.
    head, _, rest = body.partition("<h2 ")
    cards = ['<section class="card"><h2 ' + part for part in ("<h2 " + rest).split("<h2 ")[1:]]
    return f'<header class="hero">{head}</header>\n' + "</section>\n".join(cards) + "</section>\n"


PAGE = """<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<title>Triangulate Design Document</title>
<style>
:root{
  --bg:#ffffff; --surface:#ffffff; --surface-2:#f1f3f6; --text:#1b1e24; --muted:#5a6272; --border:#e2e5ea;
  --accent:#3657d6; --accent-soft:#e8edfc; --code-bg:#f3f4f7;
}
@page{size:A4;margin:12mm 13mm 14mm}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:9.4pt/1.42 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  -webkit-print-color-adjust:exact;print-color-adjust:exact}
a{color:var(--accent);text-decoration:none}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.86em}
code{background:var(--code-bg);padding:.08em .32em;border-radius:4px}
pre.code{background:var(--code-bg);border:1px solid var(--border);border-radius:8px;padding:9px 11px;line-height:1.45;
  white-space:pre-wrap;word-break:break-word;margin:8px 0;break-inside:avoid}
pre.code code{background:none;padding:0}
header.hero{padding:2mm 0 4mm;border-bottom:3px solid var(--accent);margin-bottom:5mm}
header.hero h1{font-size:22pt;line-height:1.15;margin:0 0 2mm}
header.hero p{margin:1.5mm 0;color:var(--muted)}
header.hero p:first-of-type{font-size:11.5pt;color:var(--text)}
section.card{border:1px solid var(--border);border-radius:9px;padding:3mm 4.5mm 2mm;margin:0 0 3mm;background:var(--surface)}
h2{font-size:13pt;margin:0 0 2mm;line-height:1.3;break-after:avoid}
h3{font-size:10.5pt;margin:3mm 0 1mm;break-after:avoid}
h4{font-size:10pt;margin:3mm 0 1mm;break-after:avoid}
p{margin:1.3mm 0;orphans:3;widows:3}
ul,ol{margin:1.5mm 0;padding-left:5.5mm} li{margin:.6mm 0}
.num{display:inline-flex;align-items:center;justify-content:center;width:7mm;height:7mm;border-radius:2mm;
  background:var(--accent-soft);color:var(--accent);font-weight:700;font-size:10pt;margin-right:2.5mm;vertical-align:1px}
table{border-collapse:collapse;width:100%;font-size:8.2pt;margin:1.5mm 0 2.5mm}
thead{display:table-header-group}
tr{break-inside:avoid}
th,td{text-align:left;padding:1.1mm 1.8mm;border-bottom:1px solid var(--border);vertical-align:top}
th{font-size:7.6pt;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600;background:var(--surface-2)}
blockquote{margin:2mm 0;padding:1mm 4mm;border-left:3px solid var(--accent);background:var(--accent-soft);border-radius:0 6px 6px 0}
.diagram{background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:3mm;margin:3mm 0;text-align:center;break-inside:avoid}
pre.mermaid{margin:0;background:none;white-space:pre}
.diagram svg{max-height:118mm;width:auto!important}
.diagram .cluster rect{fill:#ffffff!important;stroke:var(--border)!important}
strong{font-weight:650}
</style>
</head>
<body>
__BODY__
<script type="module">
  import mermaid from '__MERMAID__';
  mermaid.initialize({startOnLoad:false, theme:'neutral', securityLevel:'strict',
    fontFamily:'ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
    flowchart:{curve:'basis', htmlLabels:true, nodeSpacing:30, rankSpacing:40, wrappingWidth:240}});
  await mermaid.run({querySelector:'pre.mermaid'});
  document.body.dataset.ready = '1';
</script>
</body>
</html>
"""

FOOTER = ('<div style="font-size:7.5pt;color:#5a6272;width:100%;padding:0 13mm;display:flex;justify-content:space-between;'
          'font-family:system-ui,sans-serif"><span>Triangulate · design document</span>'
          '<span>page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')


def main() -> int:
    page_html = PAGE.replace("__BODY__", to_html(SRC.read_text())).replace("__MERMAID__", MERMAID)
    tmp = DOCS / ".design-print.html"
    tmp.write_text(page_html)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 794, "height": 1123})
            page.goto(tmp.as_uri(), wait_until="networkidle")
            page.wait_for_selector("body[data-ready='1']", timeout=60_000)
            failed = page.evaluate("[...document.querySelectorAll('pre.mermaid')].filter(p => !p.querySelector('svg')).length")
            if failed:
                raise SystemExit(f"{failed} diagram(s) did not render")
            page.pdf(path=str(OUT), format="A4", print_background=True, display_header_footer=True,
                     header_template="<span></span>", footer_template=FOOTER,
                     margin={"top": "12mm", "bottom": "14mm", "left": "13mm", "right": "13mm"})
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)
    print(f"wrote {OUT.relative_to(DOCS.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
