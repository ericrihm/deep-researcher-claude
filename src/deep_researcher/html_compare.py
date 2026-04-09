"""Side-by-side comparison HTML renderer.

Simpler than html_report.py -- just two columns with the markdown rendered
inline, plus the comparison summary at the top. No charts, no TOC.
Self-contained (inline CSS).
"""
from __future__ import annotations

import html
import re
from datetime import datetime


def build_compare_html(
    query: str,
    report_a: str,
    report_b: str,
    provider_a: str,
    provider_b: str,
    comparison_text: str,
) -> str:
    """Build a self-contained side-by-side HTML comparison page."""
    title = f"Compare: {html.escape(provider_a)} vs {html.escape(provider_b)}"
    query_escaped = html.escape(query)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    body_a = _md_to_html_simple(report_a)
    body_b = _md_to_html_simple(report_b)
    comparison_html = _md_to_html_simple(comparison_text)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #fff; --fg: #1a1a2e; --accent: #6c63ff;
    --border: #e0e0e0; --panel-bg: #f8f9fa; --badge-a: #2196f3;
    --badge-b: #ff9800;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #181a20; --fg: #e0e0e0; --accent: #9d97ff;
      --border: #333; --panel-bg: #23262f; --badge-a: #64b5f6;
      --badge-b: #ffb74d;
    }}
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--fg); line-height: 1.6;
    padding: 1rem;
  }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0.25rem; }}
  .meta {{ color: #888; font-size: 0.85rem; margin-bottom: 1rem; }}
  .comparison-summary {{
    background: var(--panel-bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.25rem; margin-bottom: 1.5rem;
  }}
  .comparison-summary h2 {{ font-size: 1.1rem; margin-bottom: 0.75rem; }}
  .columns {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;
  }}
  @media (max-width: 900px) {{
    .columns {{ grid-template-columns: 1fr; }}
  }}
  .col {{
    border: 1px solid var(--border); border-radius: 8px;
    padding: 1rem; overflow-y: auto; max-height: 80vh;
  }}
  .col-header {{
    position: sticky; top: 0; background: var(--panel-bg);
    padding: 0.5rem 0; margin-bottom: 0.75rem; z-index: 1;
    border-bottom: 2px solid var(--border);
  }}
  .badge {{
    display: inline-block; padding: 0.15rem 0.6rem; border-radius: 4px;
    color: #fff; font-weight: 600; font-size: 0.85rem;
  }}
  .badge-a {{ background: var(--badge-a); }}
  .badge-b {{ background: var(--badge-b); }}
  .col h3, .col h4, .col h5 {{ margin-top: 1rem; margin-bottom: 0.5rem; }}
  .col p {{ margin-bottom: 0.75rem; }}
  .col table {{
    width: 100%; border-collapse: collapse; font-size: 0.8rem;
    margin-bottom: 1rem;
  }}
  .col th, .col td {{
    border: 1px solid var(--border); padding: 0.35rem 0.5rem; text-align: left;
  }}
  .col th {{ background: var(--panel-bg); }}
  .comparison-summary h3, .comparison-summary h4 {{
    margin-top: 0.75rem; margin-bottom: 0.4rem;
  }}
  .comparison-summary p {{ margin-bottom: 0.5rem; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="meta">Query: {query_escaped} &middot; {ts}</p>

<div class="comparison-summary">
<h2>Comparison Analysis</h2>
{comparison_html}
</div>

<div class="columns">
  <div class="col" id="col-a">
    <div class="col-header">
      <span class="badge badge-a">{html.escape(provider_a)}</span>
    </div>
    {body_a}
  </div>
  <div class="col" id="col-b">
    <div class="col-header">
      <span class="badge badge-b">{html.escape(provider_b)}</span>
    </div>
    {body_b}
  </div>
</div>

<script>
// Synced scrolling: scroll one column, the other follows proportionally
(function() {{
  const a = document.getElementById('col-a');
  const b = document.getElementById('col-b');
  let syncing = false;
  function sync(src, dst) {{
    if (syncing) return;
    syncing = true;
    const pct = src.scrollTop / (src.scrollHeight - src.clientHeight || 1);
    dst.scrollTop = pct * (dst.scrollHeight - dst.clientHeight || 1);
    syncing = false;
  }}
  a.addEventListener('scroll', () => sync(a, b));
  b.addEventListener('scroll', () => sync(b, a));
}})();
</script>
</body>
</html>"""


def _md_to_html_simple(md: str) -> str:
    """Minimal markdown to HTML for comparison pages.

    Handles: headings, bold, italic, paragraphs, tables, bullet lists.
    Does NOT handle: citations, links (keep it simple).
    """
    if not md:
        return ""

    lines = md.split("\n")
    out: list[str] = []
    para: list[str] = []
    in_table = False

    def flush_para():
        if para:
            text = " ".join(para)
            text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
            text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
            out.append(f"<p>{text}</p>")
            para.clear()

    for line in lines:
        stripped = line.strip()

        # Headings
        hm = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if hm:
            flush_para()
            if in_table:
                out.append("</table>")
                in_table = False
            level = len(hm.group(1))
            out.append(f"<h{level}>{html.escape(hm.group(2))}</h{level}>")
            continue

        # Table separator
        if re.match(r"^\s*\|?\s*:?-{2,}", stripped):
            continue

        # Table row
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_para()
            if not in_table:
                out.append("<table>")
                in_table = True
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            tag = "td"
            row = "".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in cells)
            out.append(f"<tr>{row}</tr>")
            continue

        if in_table and not stripped.startswith("|"):
            out.append("</table>")
            in_table = False

        # Bullet
        bm = re.match(r"^[-*]\s+(.*)$", stripped)
        if bm:
            flush_para()
            text = bm.group(1)
            text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
            out.append(f"<p style='padding-left:1.5em'>&bull; {text}</p>")
            continue

        # Empty line
        if not stripped:
            flush_para()
            continue

        # Paragraph text
        para.append(html.escape(stripped))

    flush_para()
    if in_table:
        out.append("</table>")

    return "\n".join(out)
