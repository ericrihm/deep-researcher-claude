"""Inline SVG chart rendering for the HTML report.

Pure data-to-SVG-string transforms. No dependencies beyond the stdlib,
no LLM involvement, no I/O. Every function is deterministic and
fast-testable.

Public API:
    compute_chart_data(synthesis_papers, all_papers, categories) -> dict
    render_year_histogram(years: dict[int, int]) -> str
    render_category_bars(categories: list[tuple[str, int, int]]) -> str
    render_source_donut(sources: dict[str, int]) -> str
    render_all_charts(data: dict) -> str
"""
from __future__ import annotations

import html
import math
from collections import Counter

from deep_researcher.models import Paper


def compute_chart_data(
    synthesis_papers: list[Paper],
    all_papers: dict[str, Paper],
    categories: dict[str, list[int]] | None,
) -> dict:
    """Return chart-ready data.

    Keys:
      'years'      : dict[int, int]              # year -> paper count, zero-filled
      'categories' : list[tuple[str, int, int]]  # (name, n_papers, total_cites), desc
      'sources'    : dict[str, int]              # normalized source -> paper count
    """
    # Year histogram (synthesis_papers, zero-filled)
    year_counts: Counter[int] = Counter()
    for p in synthesis_papers:
        if p.year is not None:
            year_counts[p.year] += 1
    if year_counts:
        y_min = min(year_counts)
        y_max = max(year_counts)
        years: dict[int, int] = {y: year_counts.get(y, 0) for y in range(y_min, y_max + 1)}
    else:
        years = {}

    # Categories — desc by paper count
    cat_list: list[tuple[str, int, int]] = []
    if categories:
        for name, indices in categories.items():
            n_papers = len(indices)
            total_cites = sum(
                (synthesis_papers[i].citation_count or 0)
                for i in indices
                if 0 <= i < len(synthesis_papers)
            )
            cat_list.append((name, n_papers, total_cites))
        cat_list.sort(key=lambda t: (-t[1], t[0]))

    # Sources — split comma-delimited
    src_counts: Counter[str] = Counter()
    for p in all_papers.values():
        for s in (p.source or "").split(","):
            s = s.strip()
            if s:
                src_counts[s] += 1
    sources: dict[str, int] = dict(src_counts)

    return {"years": years, "categories": cat_list, "sources": sources}


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

_EMPTY_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 40" '
    'width="100%" preserveAspectRatio="xMidYMid meet">'
    '<text x="300" y="24" text-anchor="middle" '
    'fill="currentColor" font-family="sans-serif" font-size="13" '
    'opacity="0.6">No data</text></svg>'
)

_PALETTE_FIXED = ["#8b5cf6", "#ec4899", "#14b8a6", "#f59e0b", "#06b6d4", "#84cc16"]


def _e(text: str) -> str:
    return html.escape(str(text), quote=True)


def render_year_histogram(years: dict[int, int]) -> str:
    if not years:
        return _EMPTY_SVG
    w, h = 600, 200
    pad_l, pad_r, pad_t, pad_b = 30, 10, 20, 30
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b

    items = sorted(years.items())
    n = len(items)
    max_count = max((c for _, c in items), default=1) or 1
    bar_w = plot_w / n
    bar_inner = bar_w * 0.72

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="100%" preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="Papers per year">'
    ]
    parts.append(
        f'<text x="{pad_l - 4}" y="{pad_t + 4}" text-anchor="end" '
        f'font-family="sans-serif" font-size="10" fill="currentColor" '
        f'opacity="0.6">{max_count}</text>'
    )
    parts.append(
        f'<text x="{pad_l - 4}" y="{pad_t + plot_h}" text-anchor="end" '
        f'font-family="sans-serif" font-size="10" fill="currentColor" '
        f'opacity="0.6">0</text>'
    )

    stride = 1 if n <= 15 else 2

    for i, (year, count) in enumerate(items):
        x = pad_l + i * bar_w + (bar_w - bar_inner) / 2
        bh = (count / max_count) * plot_h if count else 0
        y = pad_t + plot_h - bh
        opacity = "0.2" if count == 0 else "1"
        label = f"{year}: {count} paper{'s' if count != 1 else ''}"
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_inner:.1f}" '
            f'height="{max(bh, 2):.1f}" fill="var(--accent, #1f6feb)" '
            f'opacity="{opacity}"><title>{_e(label)}</title></rect>'
        )
        if i % stride == 0:
            cx = x + bar_inner / 2
            parts.append(
                f'<text x="{cx:.1f}" y="{h - 10}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="10" '
                f'fill="currentColor" opacity="0.6">{year}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def render_category_bars(categories: list[tuple[str, int, int]]) -> str:
    if not categories:
        return _EMPTY_SVG
    row_h = 32
    top_pad = 10
    bot_pad = 10
    h = row_h * len(categories) + top_pad + bot_pad
    w = 600
    label_w = 160
    val_w = 110
    bar_area_x = label_w
    bar_area_w = w - label_w - val_w

    max_papers = max((n for _, n, _ in categories), default=1) or 1
    max_cites = max((c for _, _, c in categories), default=1) or 1

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="100%" preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="Papers per category">'
    ]
    for i, (name, n_papers, total_cites) in enumerate(categories):
        row_y = top_pad + i * row_h
        label_y = row_y + row_h / 2 + 4
        display = name if len(name) <= 22 else name[:21] + "…"
        parts.append(
            f'<text x="{label_w - 8}" y="{label_y:.1f}" text-anchor="end" '
            f'font-family="sans-serif" font-size="12" fill="currentColor">'
            f'{_e(display)}<title>{_e(name)}</title></text>'
        )
        bar_w = (n_papers / max_papers) * bar_area_w
        primary_y = row_y + 4
        parts.append(
            f'<rect x="{bar_area_x}" y="{primary_y:.1f}" '
            f'width="{bar_w:.1f}" height="14" rx="2" '
            f'fill="var(--accent, #1f6feb)"><title>{_e(f"{name}: {n_papers} papers")}</title></rect>'
        )
        cite_w = (total_cites / max_cites) * bar_area_w if max_cites else 0
        secondary_y = row_y + 20
        parts.append(
            f'<rect x="{bar_area_x}" y="{secondary_y:.1f}" '
            f'width="{cite_w:.1f}" height="6" rx="2" '
            f'fill="var(--badge-cite, #bf8700)" opacity="0.85">'
            f'<title>{_e(f"{name}: {total_cites} citations")}</title></rect>'
        )
        end_x = w - val_w + 6
        parts.append(
            f'<text x="{end_x}" y="{label_y:.1f}" '
            f'font-family="sans-serif" font-size="11" '
            f'fill="currentColor" opacity="0.75">'
            f'{n_papers} papers · {total_cites:,} cites</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def render_source_donut(sources: dict[str, int]) -> str:
    if not sources:
        return _EMPTY_SVG
    w, h = 600, 220
    cx, cy, r = 120, 110, 70
    stroke_w = 20
    total = sum(sources.values())
    if total == 0:
        return _EMPTY_SVG

    circumference = 2 * math.pi * r

    palette = ["var(--accent, #1f6feb)", "var(--badge-oa, #2da44e)", "var(--badge-cite, #bf8700)"]
    palette += _PALETTE_FIXED

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="100%" preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="Papers per source">'
    ]
    parts.append(
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
        f'stroke="currentColor" stroke-opacity="0.08" stroke-width="{stroke_w}"/>'
    )

    offset = 0.0
    ordered = sorted(sources.items(), key=lambda kv: (-kv[1], kv[0]))
    for i, (name, count) in enumerate(ordered):
        frac = count / total
        dash = frac * circumference
        gap = circumference - dash
        color = palette[i % len(palette)]
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{color}" stroke-width="{stroke_w}" '
            f'stroke-dasharray="{dash:.2f} {gap:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" '
            f'transform="rotate(-90 {cx} {cy})">'
            f'<title>{_e(f"{name}: {count} papers ({frac*100:.0f}%)")}</title>'
            f'</circle>'
        )
        offset += dash

    parts.append(
        f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="28" font-weight="700" '
        f'fill="currentColor">{total}</text>'
    )
    parts.append(
        f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="11" fill="currentColor" '
        f'opacity="0.6" letter-spacing="1">papers</text>'
    )

    lx = 230
    ly_start = 40
    row_h = 22
    for i, (name, count) in enumerate(ordered):
        ly = ly_start + i * row_h
        color = palette[i % len(palette)]
        parts.append(
            f'<rect x="{lx}" y="{ly - 10}" width="14" height="14" rx="2" '
            f'fill="{color}"/>'
        )
        parts.append(
            f'<text x="{lx + 22}" y="{ly + 2}" '
            f'font-family="sans-serif" font-size="13" fill="currentColor">'
            f'{_e(name)} <tspan opacity="0.6">({count})</tspan></text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def render_all_charts(data: dict) -> str:
    """Wrap all three charts in a collapsible <details> block."""
    years_svg = render_year_histogram(data.get("years") or {})
    cats_svg = render_category_bars(data.get("categories") or [])
    src_svg = render_source_donut(data.get("sources") or {})
    return (
        '<details class="charts" open>'
        '<summary>At a glance</summary>'
        '<div class="charts-grid">'
        '<figure class="chart chart-years"><figcaption>Papers per year</figcaption>'
        f'{years_svg}</figure>'
        '<figure class="chart chart-cats"><figcaption>Papers per category</figcaption>'
        f'{cats_svg}</figure>'
        '<figure class="chart chart-src"><figcaption>Sources</figcaption>'
        f'{src_svg}</figure>'
        '</div></details>'
    )
