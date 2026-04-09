from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from datetime import datetime

from deep_researcher.charts import compute_chart_data
from deep_researcher.html_report import build_html_report
from deep_researcher.models import Paper


def save_report(
    query: str,
    report_text: str,
    papers: dict[str, Paper],
    output_dir: str,
    folder: str | None = None,
    synthesis_papers: list[Paper] | None = None,
    exec_summary: str = "",
    categories: dict[str, list[int]] | None = None,
) -> dict[str, str]:
    if not folder:
        slug = _make_slug(query)
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        folder = os.path.join(output_dir, f"{timestamp}-{slug}")
    os.makedirs(folder, exist_ok=True)

    version = _next_version(folder)
    report_basename = "report.md" if version == 1 else f"report-{version}.md"
    html_basename = "report.html" if version == 1 else f"report-{version}.html"

    # Metadata header for the report
    source_counts = Counter()
    years = []
    for p in papers.values():
        for src in p.source.split(","):
            src = src.strip()
            if src:
                source_counts[src] += 1
        if p.year:
            years.append(p.year)

    header_lines = [
        f"<!-- Deep Researcher Report -->",
        f"<!-- Query: {query} -->",
        f"<!-- Generated: {datetime.now().isoformat()} -->",
        f"<!-- Papers found: {len(papers)} -->",
        f"<!-- Databases: {', '.join(f'{k} ({v})' for k, v in source_counts.most_common())} -->",
    ]
    if years:
        header_lines.append(f"<!-- Year range: {min(years)}-{max(years)} -->")
    header = "\n".join(header_lines) + "\n\n"

    report_path = os.path.join(folder, report_basename)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(header + report_text)

    # BibTeX with header
    bibtex_path = os.path.join(folder, "references.bib")
    seen_keys: dict[str, int] = {}  # key -> count
    with open(bibtex_path, "w", encoding="utf-8") as f:
        f.write(f"% Bibliography exported by Deep Researcher\n")
        f.write(f"% Generated: {datetime.now().strftime('%Y-%m-%d')}\n")
        f.write(f"% Total entries: {len(papers)}\n\n")
        for paper in papers.values():
            if not paper.title:
                continue
            bib = paper.to_bibtex()
            # Extract key to check for duplicates
            key_match = re.match(r"@\w+\{(.+),", bib)
            if key_match:
                key = key_match.group(1)
                if key in seen_keys:
                    seen_keys[key] += 1
                    suffix = f"_{seen_keys[key]}"
                    bib = paper.to_bibtex(key_suffix=suffix)
                else:
                    seen_keys[key] = 0
            f.write(bib)
            f.write("\n\n")

    # Full JSON with all fields
    papers_path = os.path.join(folder, "papers.json")
    papers_list = [p.to_dict() for p in papers.values() if p.title]
    with open(papers_path, "w", encoding="utf-8") as f:
        json.dump(papers_list, f, indent=2, ensure_ascii=False)

    # CSV export (for Excel/spreadsheet users)
    csv_path = os.path.join(folder, "papers.csv")
    csv_fields = ["title", "authors", "year", "journal", "citation_count", "doi", "source", "open_access_url", "abstract"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        for p in papers.values():
            if not p.title:
                continue
            row = p.to_dict()
            # Join authors list for CSV
            row["authors"] = "; ".join(row.get("authors", []))
            writer.writerow(row)

    # Styled HTML report (self-contained, inline CSS/JS)
    html_path = os.path.join(folder, html_basename)
    try:
        # If caller did not pass synthesis_papers, fall back to all papers
        # sorted by citation count so reference numbers are still deterministic.
        syn = synthesis_papers
        if syn is None:
            syn = sorted(
                [p for p in papers.values() if p.title],
                key=lambda p: (-(p.citation_count or 0), -(p.year or 0)),
            )
        chart_data = compute_chart_data(syn, papers, categories)
        html_doc = build_html_report(
            query,
            report_text,
            syn,
            papers,
            exec_summary=exec_summary,
            chart_data=chart_data,
        )
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_doc)
    except Exception as e:
        html_path = f"(failed: {e})"

    # Research metadata
    meta_path = os.path.join(folder, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "query": query,
            "generated": datetime.now().isoformat(),
            "total_papers": len(papers),
            "sources": dict(source_counts.most_common()),
            "year_range": [min(years), max(years)] if years else None,
        }, f, indent=2)

    return {
        "report": report_path,
        "html": html_path,
        "bibtex": bibtex_path,
        "papers (JSON)": papers_path,
        "papers (CSV)": csv_path,
        "metadata": meta_path,
    }


def save_checkpoint(papers: dict[str, Paper], folder: str) -> None:
    """Save papers.json as a checkpoint during search (safe to call repeatedly)."""
    os.makedirs(folder, exist_ok=True)
    papers_path = os.path.join(folder, "papers.json")
    papers_list = [p.to_dict() for p in papers.values() if p.title]
    with open(papers_path, "w", encoding="utf-8") as f:
        json.dump(papers_list, f, indent=2, ensure_ascii=False)


def get_output_folder(query: str, output_dir: str) -> str:
    """Get a timestamped output folder path for a query (does not create it)."""
    slug = _make_slug(query)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    folder = os.path.join(output_dir, f"{timestamp}-{slug}")
    return folder


def _next_version(folder: str) -> int:
    """Return 1 if report.md/report.html don't exist yet, else the next unused integer.

    Probes report-2.md, report-3.md, ... and also checks the matching
    report-N.html slot, so .md and .html stay synced even if one was
    manually deleted between runs.
    """
    if not os.path.exists(os.path.join(folder, "report.md")) and not os.path.exists(
        os.path.join(folder, "report.html")
    ):
        return 1
    n = 2
    while (
        os.path.exists(os.path.join(folder, f"report-{n}.md"))
        or os.path.exists(os.path.join(folder, f"report-{n}.html"))
    ):
        n += 1
    return n


def _make_slug(query: str) -> str:
    words = re.sub(r"[^a-z0-9\s]", "", query.lower()).split()
    slug = ""
    for w in words:
        if len(slug) + len(w) + 1 > 50:
            break
        slug = f"{slug}-{w}" if slug else w
    return slug or "research"
