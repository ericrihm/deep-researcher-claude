from __future__ import annotations

import httpx

from deep_researcher.models import Paper
from deep_researcher.tools.base import Tool

CORE_BASE = "https://api.core.ac.uk/v3"


class CoreSearchTool(Tool):
    name = "search_core"
    description = (
        "Search CORE for open access academic papers. Covers 300M+ open access articles "
        "and metadata from repositories worldwide. Good for finding free full-text versions "
        "of papers. Requires a free CORE API key (set CORE_API_KEY env var)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (default 10, max 20).",
            },
        },
        "required": ["query"],
    }

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def execute(self, query: str, max_results: int = 10) -> str:
        if not self._api_key:
            return "CORE search is not available — no API key configured. Set CORE_API_KEY environment variable with a free key from https://core.ac.uk/api-keys/register."

        max_results = min(max_results, 20)
        try:
            resp = httpx.get(
                f"{CORE_BASE}/search/works",
                params={"q": query, "limit": max_results},
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=30,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"Error searching CORE: {e}"

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return "No papers found on CORE for this query."

        papers = [_parse_core_work(w) for w in results]
        lines = [f"Found {len(papers)} open access papers on CORE:\n"]
        for i, p in enumerate(papers, 1):
            lines.append(f"{i}. {p.to_summary()}\n")
        return "\n".join(lines)


def _parse_core_work(data: dict) -> Paper:
    title = data.get("title", "")
    authors = [a.get("name", "") for a in data.get("authors", []) if a.get("name")]
    year = data.get("yearPublished")
    abstract = data.get("abstract")
    doi = data.get("doi")

    download_url = data.get("downloadUrl")
    source_url = data.get("sourceFulltextUrls", [None])
    url = download_url or (source_url[0] if source_url else None)

    journal_info = data.get("journals", [{}])
    journal = journal_info[0].get("title") if journal_info else None

    return Paper(
        title=title,
        authors=authors,
        year=year,
        abstract=abstract,
        doi=doi,
        url=url,
        source="core",
        journal=journal,
        open_access_url=download_url,
    )
