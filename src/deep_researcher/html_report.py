"""Standalone HTML report renderer.

Converts the Markdown report into a styled, self-contained HTML page with
clickable citations, a rich reference list, sticky TOC, and dark-mode support.
No external assets — CSS and JS are inlined so the file works offline.
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from datetime import datetime
from urllib.parse import quote_plus

from deep_researcher.models import Paper


# ---------------------------------------------------------------------------
# Markdown -> HTML (minimal; handles only what the report actually uses)
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9\s-]", "", text.lower()).strip()
    return re.sub(r"\s+", "-", s) or "section"


def _inline(text: str, paper_titles: dict[int, str]) -> str:
    """Apply inline markdown formatting to an already-HTML-escaped string.

    Order matters:
      1. escape HTML
      2. links [text](url)  (before citations, so Open Access links survive)
      3. citations [N] or [N, M]
      4. bold **text**
      5. italic *text*
    """
    text = html.escape(text, quote=False)

    def _link_sub(m: re.Match) -> str:
        label = m.group(1)
        url = m.group(2)
        return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'

    text = _LINK_RE.sub(_link_sub, text)

    def _cite_sub(m: re.Match) -> str:
        nums = [n.strip() for n in m.group(1).split(",")]
        parts = []
        for n in nums:
            try:
                idx = int(n)
            except ValueError:
                parts.append(n)
                continue
            title = paper_titles.get(idx, f"Reference {idx}")
            title_attr = html.escape(title, quote=True)
            parts.append(
                f'<a class="cite" href="#ref-{idx}" title="{title_attr}">{idx}</a>'
            )
        return "[" + ", ".join(parts) + "]"

    text = _CITE_RE.sub(_cite_sub, text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    return text


def _md_to_html(md: str, paper_titles: dict[int, str]) -> tuple[str, list[tuple[int, str, str]]]:
    """Convert the body of the markdown report to HTML.

    Returns (html, toc) where toc is a list of (level, id, text) tuples for
    headings at level <= 5.
    """
    lines = md.split("\n")
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    para: list[str] = []
    i = 0

    def flush_para() -> None:
        if para:
            text = " ".join(para)
            out.append(f"<p>{_inline(text, paper_titles)}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip HTML comments (used as metadata header)
        if stripped.startswith("<!--"):
            i += 1
            continue

        if not stripped:
            flush_para()
            i += 1
            continue

        m = _HEADING_RE.match(stripped)
        if m:
            flush_para()
            level = len(m.group(1))
            raw_text = m.group(2)
            anchor = _slug(raw_text)
            # Ensure unique anchors
            base = anchor
            n = 2
            existing = {a for _, a, _ in toc}
            while anchor in existing:
                anchor = f"{base}-{n}"
                n += 1
            toc.append((level, anchor, raw_text))
            inner = _inline(raw_text, paper_titles)
            out.append(f'<h{level} id="{anchor}">{inner}</h{level}>')
            i += 1
            continue

        # Markdown pipe table
        if "|" in stripped and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            flush_para()
            header_cells = [c.strip() for c in stripped.strip().strip("|").split("|")]
            i += 2  # skip separator row
            rows: list[list[str]] = []
            while i < len(lines) and "|" in lines[i]:
                row_stripped = lines[i].strip()
                if not row_stripped:
                    break
                cells = [c.strip() for c in row_stripped.strip("|").split("|")]
                rows.append(cells)
                i += 1
            thead = (
                "<tr>"
                + "".join(f"<th>{_inline(c, paper_titles)}</th>" for c in header_cells)
                + "</tr>"
            )
            tbody = "".join(
                "<tr>"
                + "".join(f"<td>{_inline(c, paper_titles)}</td>" for c in r)
                + "</tr>"
                for r in rows
            )
            out.append(
                f'<div class="table-wrap"><table><thead>{thead}</thead>'
                f"<tbody>{tbody}</tbody></table></div>"
            )
            continue

        # Bullet list
        if _BULLET_RE.match(stripped):
            flush_para()
            items: list[str] = []
            while i < len(lines) and _BULLET_RE.match(lines[i].strip()):
                m2 = _BULLET_RE.match(lines[i].strip())
                assert m2
                items.append(f"<li>{_inline(m2.group(1), paper_titles)}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return "\n".join(out), toc


# ---------------------------------------------------------------------------
# Reference rendering
# ---------------------------------------------------------------------------

def _paper_links(p: Paper) -> list[tuple[str, str, str]]:
    """Return a list of (css_class, label, href) link tuples for a paper."""
    links: list[tuple[str, str, str]] = []
    if p.doi:
        links.append(("doi", "DOI", f"https://doi.org/{p.doi}"))
    if p.open_access_url:
        links.append(("oa", "Open Access", p.open_access_url))
    if p.url and p.url != p.open_access_url:
        links.append(("url", "Publisher", p.url))
    if p.arxiv_id:
        links.append(("arxiv", "arXiv", f"https://arxiv.org/abs/{p.arxiv_id}"))
    if p.pmid:
        links.append(("pmid", "PubMed", f"https://pubmed.ncbi.nlm.nih.gov/{p.pmid}/"))
    # Always provide Scholar as a fallback search
    scholar_q = quote_plus(f"{p.title}".strip())
    links.append(("scholar", "Scholar", f"https://scholar.google.com/scholar?q={scholar_q}"))
    return links


def _render_reference(idx: int, p: Paper) -> str:
    authors = ", ".join(p.authors) if p.authors else "Unknown"
    if len(p.authors) > 4:
        authors = ", ".join(p.authors[:3]) + ", et al."
    year = p.year or "n.d."
    title_esc = html.escape(p.title or "(untitled)")
    journal = (
        f' <em class="journal">{html.escape(p.journal)}</em>.' if p.journal else ""
    )
    citations = (
        f'<span class="badge citations" title="Citation count">&#9733; {p.citation_count}</span>'
        if p.citation_count is not None
        else ""
    )
    oa_badge = (
        '<span class="badge oa" title="Open access full text available">OA</span>'
        if p.open_access_url
        else ""
    )

    link_html = " ".join(
        f'<a class="ref-link {cls}" href="{href}" target="_blank" rel="noopener">{label}</a>'
        for cls, label, href in _paper_links(p)
    )

    bibtex = html.escape(p.to_bibtex())
    bibtex_json = json.dumps(p.to_bibtex())

    # Data attributes for client-side filtering
    search_blob = " ".join(
        [
            p.title or "",
            " ".join(p.authors or []),
            str(p.year or ""),
            p.journal or "",
        ]
    ).lower()
    search_attr = html.escape(search_blob, quote=True)

    return f"""
<li class="reference" id="ref-{idx}" data-search="{search_attr}">
  <div class="ref-head">
    <span class="ref-num">[{idx}]</span>
    <span class="ref-title">{title_esc}</span>
    {oa_badge}{citations}
  </div>
  <div class="ref-meta">{html.escape(authors)} &middot; <span class="year">{year}</span>.{journal}</div>
  <div class="ref-actions">
    {link_html}
    <button class="copy-bib" data-bibtex='{html.escape(bibtex_json, quote=True)}'>Copy BibTeX</button>
  </div>
  <details class="bibtex-details"><summary>BibTeX</summary><pre>{bibtex}</pre></details>
</li>
""".strip()


def _render_exec_summary(text: str) -> str:
    """Render the executive summary block, or empty string if missing."""
    text = (text or "").strip()
    if not text:
        return ""
    return (
        '<section class="exec-summary" aria-label="Executive summary">'
        '<h2 class="exec-label">Executive summary</h2>'
        f'<p>{html.escape(text)}</p>'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Full page template
# ---------------------------------------------------------------------------

_CSS = r"""
:root {
  --bg: #fafaf7;
  --fg: #1a1a1a;
  --muted: #666;
  --accent: #4a6585;
  --accent-soft: #e7edf4;
  --border: #e2e2dd;
  --card: #ffffff;
  --code-bg: #f3f3ef;
  --row-alt: #f5f5f0;
  --badge-oa: #2da44e;
  --badge-cite: #bf8700;
  --shadow: 0 1px 3px rgba(0,0,0,0.06), 0 4px 12px rgba(0,0,0,0.04);
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
  --serif: "Iowan Old Style", "Charter", "Georgia", serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1e1e24;
    --fg: #c8c8d0;
    --muted: #8a8a9a;
    --accent: #7b93b0;
    --accent-soft: #2a3340;
    --border: #333339;
    --card: #242430;
    --code-bg: #2a2a35;
    --row-alt: #242430;
    --badge-oa: #5a9a6a;
    --badge-cite: #c4a24a;
    --shadow: 0 1px 3px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.3);
  }
}
[data-theme="dark"] {
  --bg: #1e1e24; --fg: #c8c8d0; --muted: #8a8a9a; --accent: #7b93b0;
  --accent-soft: #2a3340; --border: #333339; --card: #242430; --code-bg: #2a2a35;
  --row-alt: #242430; --badge-oa: #5a9a6a; --badge-cite: #c4a24a;
  --shadow: 0 1px 3px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.3);
}
[data-theme="light"] {
  --bg: #fafaf7; --fg: #1a1a1a; --muted: #666; --accent: #4a6585;
  --accent-soft: #e7edf4; --border: #e2e2dd; --card: #ffffff; --code-bg: #f3f3ef;
  --row-alt: #f5f5f0; --badge-oa: #2da44e; --badge-cite: #bf8700;
  --shadow: 0 1px 3px rgba(0,0,0,0.06), 0 4px 12px rgba(0,0,0,0.04);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; padding: 0;
  font-family: var(--serif);
  font-size: 17px;
  line-height: 1.75;
  color: var(--fg);
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
}
.layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 0; max-width: 1280px; margin: 0 auto; }
aside.toc {
  position: sticky; top: 0; align-self: start; height: 100vh; overflow-y: auto;
  padding: 2rem 1rem 2rem 1.5rem; border-right: 1px solid var(--border);
  font-family: var(--sans); font-size: 13px;
}
aside.toc h2 { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin: 1.5rem 0 .75rem; }
aside.toc h2:first-of-type { margin-top: 0; }
aside.toc ol { list-style: none; padding: 0; margin: 0; }
aside.toc li { margin: .15rem 0; }
aside.toc a {
  color: var(--fg); text-decoration: none; display: block;
  padding: .25rem .6rem; border-left: 3px solid transparent;
  opacity: 0.6; transition: opacity .12s, border-color .12s;
}
aside.toc a:hover { opacity: 1; }
aside.toc a.active { opacity: 1; border-left-color: var(--accent); background: transparent; }
aside.toc li.lvl-4 a { padding-left: 1.6rem; }
aside.toc li.lvl-5 a { padding-left: 2.2rem; font-size: 12px; }
main { padding: 2.5rem 3rem 5rem; min-width: 0; }
.meta-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  padding: 1.25rem 1.5rem; margin: 0 0 2rem; box-shadow: var(--shadow);
  font-family: var(--sans);
}
.meta-card h1 { margin: 0 0 .5rem; font-size: 1.5rem; font-family: var(--serif); }
.meta-card .meta-row { display: flex; flex-wrap: wrap; gap: 1rem 1.5rem; color: var(--muted); font-size: 14px; }
.meta-card .meta-row span strong { color: var(--fg); font-weight: 600; }
h1, h2, h3, h4, h5 { font-family: var(--sans); line-height: 1.3; }
h2 { margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border); font-size: 1.7rem; color: var(--accent); font-weight: 600; }
h2:first-of-type { border-top: none; padding-top: 0; }
h3 { margin-top: 2.5rem; font-size: 1.45rem; color: var(--accent); font-weight: 600; padding-bottom: .35rem; border-bottom: 1px solid var(--border); }
h4 { margin-top: 2rem; font-size: 1.2rem; color: var(--fg); font-weight: 600; }
h5 { margin-top: 1.5rem; font-size: 1.05rem; color: var(--fg); font-weight: 600; }
p { max-width: 720px; margin: 0 0 1.5em; }
ul, ol { max-width: 720px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
a.cite {
  display: inline-block; font-weight: 500; font-size: .72em;
  color: var(--muted); background: transparent;
  padding: 0 .3em; border: 1px solid var(--border); border-radius: 3px;
  text-decoration: none; vertical-align: super; line-height: 1.4;
  margin: 0 1px; transition: color .12s, border-color .12s;
}
a.cite:hover { color: var(--accent); border-color: var(--accent); background: transparent; }
.table-wrap { overflow-x: auto; margin: 1.5rem 0; border: 1px solid var(--border); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-family: var(--sans); font-size: 0.9em; }
th, td { text-align: left; padding: 12px 14px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { background: var(--code-bg); font-weight: 600; color: var(--fg); position: sticky; top: 0; border-bottom: 1px solid var(--border); }
tbody tr:nth-child(even) td { background: var(--row-alt); }
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--accent-soft); }
/* Cap the typically-widest "Key Finding" column */
td:nth-child(4) { max-width: 250px; }
ul { padding-left: 1.3rem; }
ul li { margin: .3rem 0; }
pre { background: var(--code-bg); padding: .75rem 1rem; border-radius: 6px; overflow-x: auto; font-family: var(--mono); font-size: 13px; }
/* References */
.references-header { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; margin-top: 2.5rem; }
.references-header h4 { margin: 0; }
#ref-search {
  flex: 1; min-width: 200px; padding: .5rem .75rem;
  border: 1px solid var(--border); border-radius: 6px; background: var(--card);
  color: var(--fg); font-family: var(--sans); font-size: 14px;
}
#ref-search:focus { outline: 2px solid var(--accent); outline-offset: 1px; border-color: var(--accent); }
ol.references { list-style: none; padding: 0; margin: 1rem 0; counter-reset: none; display: flex; flex-direction: column; gap: 12px; }
li.reference {
  padding: .8rem .9rem; margin: 0; border: 1px solid var(--border);
  border-radius: 8px; background: var(--card); transition: box-shadow .15s, transform .15s;
  font-family: var(--sans); font-size: 14px;
}
li.reference:target { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); animation: flash 1s ease-out; }
@keyframes flash { 0% { background: var(--accent-soft); } 100% { background: var(--card); } }
li.reference.hidden { display: none; }
.ref-head { display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap; }
.ref-num { color: var(--muted); font-variant-numeric: tabular-nums; font-weight: 600; }
.ref-title { font-weight: 600; color: var(--fg); flex: 1; min-width: 200px; }
.ref-meta { color: var(--muted); margin: .35rem 0 .5rem; font-size: 13px; }
.ref-meta .year { color: var(--fg); font-variant-numeric: tabular-nums; }
.ref-actions { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; }
.ref-link {
  font-size: 12px; padding: .25rem .55rem; border-radius: 4px;
  background: var(--accent-soft); color: var(--accent); font-weight: 600;
  text-decoration: none; border: 1px solid transparent;
}
.ref-link:hover { background: var(--accent); color: #fff; }
.ref-link.oa { background: #e6f1e8; color: var(--badge-oa); }
.ref-link.doi { background: #f4ece0; color: #8a5a1f; }
@media (prefers-color-scheme: dark) {
  .ref-link.oa { background: #1f2a22; color: #7ab384; }
  .ref-link.doi { background: #2a241a; color: #c4a24a; }
}
.copy-bib {
  font-size: 12px; padding: .25rem .55rem; border-radius: 4px;
  background: var(--card); color: var(--muted); border: 1px solid var(--border);
  cursor: pointer; font-family: var(--sans); font-weight: 500;
}
.copy-bib:hover { border-color: var(--accent); color: var(--accent); }
.copy-bib.copied { background: var(--badge-oa); color: #fff; border-color: var(--badge-oa); }
.badge {
  font-size: 11px; font-weight: 700; padding: 1px 6px; border-radius: 10px;
  font-family: var(--sans); letter-spacing: .02em;
}
.badge.oa { background: var(--badge-oa); color: #fff; }
.badge.citations { background: transparent; color: var(--badge-cite); border: 1px solid var(--badge-cite); }
details.bibtex-details { margin-top: .5rem; }
details.bibtex-details summary { cursor: pointer; color: var(--muted); font-size: 12px; }
details.bibtex-details pre { margin-top: .5rem; font-size: 11px; max-height: 200px; }
/* Theme toggle */
.theme-toggle {
  position: fixed; top: 1rem; right: 1rem; z-index: 10;
  background: var(--card); border: 1px solid var(--border); color: var(--fg);
  padding: .5rem .7rem; border-radius: 20px; cursor: pointer;
  font-family: var(--sans); font-size: 13px; box-shadow: var(--shadow);
}
.theme-toggle:hover { border-color: var(--accent); }
/* Scroll-to-top */
#to-top {
  position: fixed; bottom: 1.5rem; right: 1.5rem; z-index: 10;
  background: var(--accent); color: #fff; border: none;
  width: 40px; height: 40px; border-radius: 50%;
  font-size: 18px; cursor: pointer; opacity: 0; transition: opacity .2s;
  box-shadow: var(--shadow);
}
#to-top.visible { opacity: 1; }
/* Responsive */
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  aside.toc { position: static; height: auto; border-right: none; border-bottom: 1px solid var(--border); }
  main { padding: 1.5rem; }
}
/* Print */
/* Executive summary */
.exec-summary {
  background: var(--accent-soft);
  border-left: 4px solid var(--accent);
  padding: 1.25rem 1.5rem;
  margin: 0 0 2rem;
  border-radius: 8px;
  font-family: var(--serif);
  font-size: 1.05rem;
  line-height: 1.7;
}
.exec-summary .exec-label {
  margin: 0 0 .5rem;
  font-family: var(--sans);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--accent);
}
.exec-summary p { margin: 0; max-width: none; }
/* Charts */
details.charts {
  margin: 0 0 2rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card);
}
details.charts > summary {
  cursor: pointer;
  padding: .75rem 1rem;
  font-family: var(--sans);
  font-weight: 600;
  font-size: 13px;
  letter-spacing: .05em;
  text-transform: uppercase;
  color: var(--muted);
}
details.charts[open] > summary { border-bottom: 1px solid var(--border); }
.charts-grid {
  display: grid;
  gap: 1.5rem;
  padding: 24px;
  grid-template-columns: 1fr;
}
@media (min-width: 1100px) {
  .charts-grid { grid-template-columns: 1fr 1fr; }
  .charts-grid .chart-years { grid-column: 1 / -1; }
}
figure.chart { margin: 0; }
figure.chart figcaption {
  font-family: var(--sans);
  font-size: 12px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .05em;
  margin-bottom: .5rem;
}
figure.chart svg { display: block; width: 100%; height: auto; }
figure.chart svg rect:hover { opacity: 0.7; }
@media print {
  details.charts { break-inside: avoid; }
  details.charts:not([open]) > *:not(summary) { display: block !important; }
}
@media print {
  .theme-toggle, #to-top, aside.toc, #ref-search, .copy-bib { display: none !important; }
  .layout { display: block; max-width: 100%; }
  main { padding: 0; }
  body { font-size: 11pt; background: #fff; color: #000; }
  a { color: #000; text-decoration: underline; }
  li.reference { break-inside: avoid; border: none; padding: .3rem 0; }
  details.bibtex-details { display: none; }
  h3, h4, h5 { break-after: avoid; }
  table, .table-wrap { break-inside: avoid; }
}
"""

_JS = r"""
(function() {
  // Theme toggle
  const stored = localStorage.getItem('dr-theme');
  if (stored) document.documentElement.setAttribute('data-theme', stored);
  const btn = document.getElementById('theme-toggle');
  function updateBtn() {
    const cur = document.documentElement.getAttribute('data-theme')
      || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    btn.textContent = cur === 'dark' ? '\u263C Light' : '\u263D Dark';
  }
  updateBtn();
  btn.addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme')
      || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('dr-theme', next);
    updateBtn();
  });

  // Reference filter
  const search = document.getElementById('ref-search');
  if (search) {
    search.addEventListener('input', (e) => {
      const q = e.target.value.trim().toLowerCase();
      document.querySelectorAll('li.reference').forEach(li => {
        const hay = li.getAttribute('data-search') || '';
        li.classList.toggle('hidden', q && !hay.includes(q));
      });
    });
  }

  // Copy BibTeX
  document.querySelectorAll('.copy-bib').forEach(b => {
    b.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(JSON.parse(b.dataset.bibtex));
        const orig = b.textContent;
        b.textContent = 'Copied!';
        b.classList.add('copied');
        setTimeout(() => { b.textContent = orig; b.classList.remove('copied'); }, 1200);
      } catch (err) { console.error(err); }
    });
  });

  // Scroll to top
  const top = document.getElementById('to-top');
  window.addEventListener('scroll', () => {
    top.classList.toggle('visible', window.scrollY > 600);
  });
  top.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));

  // TOC active highlighting via IntersectionObserver
  const headings = document.querySelectorAll('main h3, main h4, main h5');
  const tocLinks = new Map();
  document.querySelectorAll('aside.toc a').forEach(a => {
    tocLinks.set(a.getAttribute('href').slice(1), a);
  });
  const obs = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if (e.isIntersecting) {
        const a = tocLinks.get(e.target.id);
        if (a) {
          document.querySelectorAll('aside.toc a.active').forEach(x => x.classList.remove('active'));
          a.classList.add('active');
        }
      }
    });
  }, { rootMargin: '-20% 0px -70% 0px' });
  headings.forEach(h => obs.observe(h));
})();
"""


def build_html_report(
    query: str,
    report_md: str,
    synthesis_papers: list[Paper],
    all_papers: dict[str, Paper],
    exec_summary: str = "",
    chart_data: dict | None = None,
) -> str:
    """Build a self-contained HTML report page."""
    # Build id -> title map for citation tooltips
    paper_titles: dict[int, str] = {
        i: p.title or f"Reference {i}"
        for i, p in enumerate(synthesis_papers, 1)
    }

    # Strip the Markdown references section — we rebuild it programmatically
    body_md = re.sub(
        r"(?ms)^####\s*References\s*\n.*\Z",
        "",
        report_md,
    ).rstrip()

    body_html, toc = _md_to_html(body_md, paper_titles)

    # Metadata
    source_counts: Counter[str] = Counter()
    years: list[int] = []
    for p in all_papers.values():
        for src in (p.source or "").split(","):
            src = src.strip()
            if src:
                source_counts[src] += 1
        if p.year:
            years.append(p.year)

    yr_range = f"{min(years)}-{max(years)}" if years else "n/a"
    sources_str = ", ".join(f"{k} ({v})" for k, v in source_counts.most_common()) or "n/a"
    oa_count = sum(1 for p in all_papers.values() if p.open_access_url)

    # TOC HTML
    toc_items = []
    for level, anchor, text in toc:
        if level < 3 or level > 5:
            continue
        toc_items.append(
            f'<li class="lvl-{level}"><a href="#{anchor}">{html.escape(text)}</a></li>'
        )
    toc_html = "<ol>" + "".join(toc_items) + "</ol>" if toc_items else ""

    # References HTML
    refs_html = "\n".join(
        _render_reference(i, p) for i, p in enumerate(synthesis_papers, 1)
    )

    exec_summary_html = _render_exec_summary(exec_summary)
    from deep_researcher.charts import render_all_charts
    charts_html = render_all_charts(chart_data) if chart_data else ""

    title_text = html.escape(query)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_text} — Deep Researcher</title>
<style>{_CSS}</style>
</head>
<body>
<button class="theme-toggle" id="theme-toggle" aria-label="Toggle theme">Theme</button>
<div class="layout">
  <aside class="toc">
    <h2>Contents</h2>
    {toc_html}
  </aside>
  <main>
    <div class="meta-card">
      <h1>{title_text}</h1>
      <div class="meta-row">
        <span><strong>Generated:</strong> {generated}</span>
        <span><strong>Papers:</strong> {len(all_papers)}</span>
        <span><strong>In synthesis:</strong> {len(synthesis_papers)}</span>
        <span><strong>Open access:</strong> {oa_count}</span>
        <span><strong>Year range:</strong> {yr_range}</span>
      </div>
      <div class="meta-row" style="margin-top:.5rem;">
        <span><strong>Sources:</strong> {html.escape(sources_str)}</span>
      </div>
    </div>
    {exec_summary_html}
    {charts_html}
    {body_html}
    <div class="references-header">
      <h4 id="references">References ({len(synthesis_papers)})</h4>
      <input id="ref-search" type="search" placeholder="Filter references by title, author, year...">
    </div>
    <ol class="references">
      {refs_html}
    </ol>
  </main>
</div>
<button id="to-top" title="Back to top">&uarr;</button>
<script>{_JS}</script>
</body>
</html>
"""
