"""DBLP computer science bibliography search.

DBLP (dblp.org) is the definitive index of computer science publications.
Free API, no key required. Covers all top-4 security conferences (USENIX
Security, ACM CCS, NDSS, IEEE S&P), plus ACSAC, RAID, WOOT, AsiaCCS,
and the full IEEE/ACM proceedings catalog.
"""
from __future__ import annotations

import time

import httpx

from deep_researcher.models import Paper, ToolResult, clean_abstract
from deep_researcher.tools.base import Tool

DBLP_BASE = "https://dblp.org/search/publ/api"

_RETRIABLE_STATUSES = {429, 500, 502, 503}


class DblpSearchTool(Tool):
    name = "search_dblp"
    category = "index"
    quality_tier = 1  # Curated CS bibliography — peer-reviewed venues
    description = (
        "Search DBLP for computer science publications. Covers 7M+ articles from "
        "all major CS conferences and journals including security venues (USENIX "
        "Security, ACM CCS, NDSS, IEEE S&P, RAID, ACSAC, WOOT). No API key required. "
        "Strong for finding conference papers that Google Scholar sometimes misses."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query for DBLP.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (default 20, max 30).",
            },
        },
        "required": ["query"],
    }

    def execute(self, query: str, max_results: int = 20) -> ToolResult:
        max_results = min(max_results, 30)

        params: dict = {
            "q": query,
            "h": max_results,
            "format": "json",
        }

        try:
            resp = None
            for attempt in range(3):
                resp = httpx.get(
                    DBLP_BASE,
                    params=params,
                    timeout=30,
                    follow_redirects=True,
                )
                if resp.status_code in _RETRIABLE_STATUSES:
                    time.sleep(2 ** (attempt + 1))
                    continue
                break
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return ToolResult(text=f"Error searching DBLP: {e}")

        data = resp.json()
        result_data = data.get("result", {})
        hits = result_data.get("hits", {})
        hit_list = hits.get("hit", [])

        if not hit_list:
            return ToolResult(text="No papers found on DBLP for this query.")

        papers = self._filter_by_year(
            [p for h in hit_list if (p := _parse_dblp_hit(h)) is not None]
        )
        if not papers:
            return ToolResult(
                text="No papers found on DBLP for this query (after year filter)."
            )

        lines = [f"Found {len(papers)} papers on DBLP:\n"]
        for i, p in enumerate(papers, 1):
            lines.append(f"{i}. {p.to_summary()}\n")
        return ToolResult(text="\n".join(lines), papers=papers)


def _parse_dblp_hit(hit: dict) -> Paper | None:
    """Parse a single DBLP search hit into a Paper."""
    info = hit.get("info", {})
    title = info.get("title", "")
    if not title:
        return None
    # DBLP titles sometimes end with a trailing period
    title = title.rstrip(".")

    # Authors — DBLP returns either a dict (single author) or list (multiple)
    authors_raw = info.get("authors", {}).get("author", [])
    if isinstance(authors_raw, dict):
        authors_raw = [authors_raw]
    authors = []
    for a in authors_raw:
        name = a.get("text", "") if isinstance(a, dict) else str(a)
        if name:
            authors.append(name)

    year = None
    year_str = info.get("year")
    if year_str:
        try:
            year = int(year_str)
        except (ValueError, TypeError):
            pass

    doi = info.get("doi")
    url = info.get("ee")  # Electronic edition URL
    if isinstance(url, list):
        # Pick DOI link if available, else first
        doi_urls = [u for u in url if "doi.org" in u]
        url = doi_urls[0] if doi_urls else url[0]

    venue = info.get("venue")
    journal = venue if isinstance(venue, str) else None

    # DBLP doesn't provide abstracts — enrichment phase fills these in
    return Paper(
        title=title,
        authors=authors,
        year=year,
        abstract=None,
        doi=doi,
        url=url,
        source="dblp",
        journal=journal,
    )
