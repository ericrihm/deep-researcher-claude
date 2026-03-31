from __future__ import annotations

import os
import re
from datetime import datetime

from deep_researcher.models import Paper


def save_report(
    query: str,
    report_text: str,
    papers: dict[str, Paper],
    output_dir: str,
) -> dict[str, str]:
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower().strip())[:50].strip("-")
    date_str = datetime.now().strftime("%Y-%m-%d")
    folder = os.path.join(output_dir, f"{date_str}-{slug}")
    os.makedirs(folder, exist_ok=True)

    report_path = os.path.join(folder, "report.md")
    with open(report_path, "w") as f:
        f.write(report_text)

    bibtex_path = os.path.join(folder, "references.bib")
    with open(bibtex_path, "w") as f:
        for paper in papers.values():
            f.write(paper.to_bibtex())
            f.write("\n\n")

    papers_path = os.path.join(folder, "papers.json")
    import json
    papers_list = []
    for p in papers.values():
        papers_list.append({
            "title": p.title,
            "authors": p.authors,
            "year": p.year,
            "abstract": p.abstract,
            "doi": p.doi,
            "url": p.url,
            "source": p.source,
            "citation_count": p.citation_count,
            "journal": p.journal,
            "open_access_url": p.open_access_url,
        })
    with open(papers_path, "w") as f:
        json.dump(papers_list, f, indent=2)

    return {
        "report": report_path,
        "bibtex": bibtex_path,
        "papers": papers_path,
    }
