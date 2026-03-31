from __future__ import annotations

import httpx

from deep_researcher.models import Paper
from deep_researcher.tools.base import Tool

OPENALEX_BASE = "https://api.openalex.org"


class OpenAlexSearchTool(Tool):
    name = "search_openalex"
    description = (
        "Search OpenAlex for academic papers. Covers 250M+ works across all fields. "
        "Fully open dataset with excellent metadata coverage. Good for broad searches "
        "and finding works that may not appear in other databases."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query for finding papers."},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (default 10, max 25).",
            },
        },
        "required": ["query"],
    }

    def __init__(self, email: str = "") -> None:
        self._email = email

    def execute(self, query: str, max_results: int = 10) -> str:
        max_results = min(max_results, 25)
        params: dict = {"search": query, "per_page": max_results}
        if self._email:
            params["mailto"] = self._email

        try:
            resp = httpx.get(f"{OPENALEX_BASE}/works", params=params, timeout=30)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"Error searching OpenAlex: {e}"

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return "No papers found on OpenAlex for this query."

        papers = [_parse_openalex_work(w) for w in results]
        lines = [f"Found {len(papers)} papers on OpenAlex:\n"]
        for i, p in enumerate(papers, 1):
            lines.append(f"{i}. {p.to_summary()}\n")
        return "\n".join(lines)


def _reconstruct_abstract(inverted_index: dict | None) -> str | None:
    if not inverted_index:
        return None
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(w for _, w in word_positions)


def _parse_openalex_work(data: dict) -> Paper:
    title = data.get("title") or ""
    authorships = data.get("authorships", [])
    authors = []
    for a in authorships:
        author = a.get("author", {})
        name = author.get("display_name")
        if name:
            authors.append(name)

    year = data.get("publication_year")
    doi_url = data.get("doi") or ""
    doi = doi_url.replace("https://doi.org/", "") if doi_url else None
    abstract = _reconstruct_abstract(data.get("abstract_inverted_index"))
    cited_by = data.get("cited_by_count")

    source = data.get("primary_location", {}) or {}
    source_info = source.get("source", {}) or {}
    journal = source_info.get("display_name")

    oa = data.get("open_access", {}) or {}
    oa_url = oa.get("oa_url")

    return Paper(
        title=title,
        authors=authors,
        year=year,
        abstract=abstract,
        doi=doi,
        url=doi_url or data.get("id"),
        source="openalex",
        citation_count=cited_by,
        journal=journal,
        open_access_url=oa_url,
    )
