from __future__ import annotations

import httpx

from deep_researcher.models import Paper
from deep_researcher.tools.base import Tool

CROSSREF_BASE = "https://api.crossref.org"


class CrossrefSearchTool(Tool):
    name = "search_crossref"
    description = (
        "Search CrossRef for academic papers by DOI metadata. Covers 150M+ records "
        "from most major publishers (Elsevier, Springer, Wiley, IEEE, etc.). "
        "Best for finding papers from traditional publishers and getting accurate metadata."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query for finding papers."},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (default 10, max 20).",
            },
        },
        "required": ["query"],
    }

    def __init__(self, email: str = "") -> None:
        self._email = email

    def execute(self, query: str, max_results: int = 10) -> str:
        max_results = min(max_results, 20)
        headers = {}
        if self._email:
            headers["User-Agent"] = f"DeepResearcher/0.1 (mailto:{self._email})"

        try:
            resp = httpx.get(
                f"{CROSSREF_BASE}/works",
                params={"query": query, "rows": max_results, "sort": "relevance"},
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"Error searching CrossRef: {e}"

        data = resp.json()
        items = data.get("message", {}).get("items", [])
        if not items:
            return "No papers found on CrossRef for this query."

        papers = [_parse_crossref_item(item) for item in items if item.get("title", [""])[0]]
        lines = [f"Found {len(papers)} papers on CrossRef:\n"]
        for i, p in enumerate(papers, 1):
            lines.append(f"{i}. {p.to_summary()}\n")
        return "\n".join(lines)


def _parse_crossref_item(data: dict) -> Paper:
    title_list = data.get("title", [])
    title = title_list[0] if title_list else ""

    authors = []
    for a in data.get("author", []):
        given = a.get("given", "")
        family = a.get("family", "")
        name = f"{given} {family}".strip()
        if name:
            authors.append(name)

    year = None
    date_parts = data.get("published-print", data.get("published-online", {})).get("date-parts", [[]])
    if date_parts and date_parts[0]:
        year = date_parts[0][0]

    doi = data.get("DOI")
    abstract = data.get("abstract")
    if abstract:
        import re
        abstract = re.sub(r"<[^>]+>", "", abstract).strip()

    cited_by = data.get("is-referenced-by-count")

    container = data.get("container-title", [])
    journal = container[0] if container else None

    url = data.get("URL")

    return Paper(
        title=title,
        authors=authors,
        year=year,
        abstract=abstract,
        doi=doi,
        url=url,
        source="crossref",
        citation_count=cited_by,
        journal=journal,
    )
