from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime

from deep_researcher.models import Paper


def save_report(
    query: str,
    report_text: str,
    papers: dict[str, Paper],
    output_dir: str,
) -> dict[str, str]:
    slug = _make_slug(query)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    folder = os.path.join(output_dir, f"{timestamp}-{slug}")
    os.makedirs(folder, exist_ok=True)

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

    report_path = os.path.join(folder, "report.md")
    with open(report_path, "w") as f:
        f.write(header + report_text)

    # BibTeX with header
    bibtex_path = os.path.join(folder, "references.bib")
    seen_keys: set[str] = set()
    with open(bibtex_path, "w") as f:
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
                    continue
                seen_keys.add(key)
            f.write(bib)
            f.write("\n\n")

    # Full JSON with all fields
    papers_path = os.path.join(folder, "papers.json")
    papers_list = [p.to_dict() for p in papers.values() if p.title]
    with open(papers_path, "w") as f:
        json.dump(papers_list, f, indent=2, ensure_ascii=False)

    # Research metadata
    meta_path = os.path.join(folder, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump({
            "query": query,
            "generated": datetime.now().isoformat(),
            "total_papers": len(papers),
            "sources": dict(source_counts.most_common()),
            "year_range": [min(years), max(years)] if years else None,
        }, f, indent=2)

    return {
        "report": report_path,
        "bibtex": bibtex_path,
        "papers": papers_path,
        "metadata": meta_path,
    }


def _make_slug(query: str) -> str:
    words = re.sub(r"[^a-z0-9\s]", "", query.lower()).split()
    slug = ""
    for w in words:
        if len(slug) + len(w) + 1 > 50:
            break
        slug = f"{slug}-{w}" if slug else w
    return slug or "research"
