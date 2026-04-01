from __future__ import annotations

import re
import time

import httpx

from deep_researcher.models import Paper, ToolResult, clean_abstract
from deep_researcher.tools.base import Tool

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,authors,year,abstract,doi,url,citationCount,journal,externalIds,tldr"

_RETRIABLE_STATUSES = {429, 500, 502, 503}


class PaperDetailsTool(Tool):
    name = "get_paper_details"
    category = "utility"
    description = (
        "Get detailed information about a specific paper by DOI, Semantic Scholar ID, "
        "or arXiv ID. Returns full metadata including abstract, citation count, and "
        "TLDR summary when available. Use this to get more details on a specific paper."
    )
    parameters = {
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "Paper identifier: DOI (e.g. '10.1234/example'), Semantic Scholar ID, or arXiv ID prefixed with 'ARXIV:' (e.g. 'ARXIV:2301.00001').",
            },
        },
        "required": ["paper_id"],
    }

    def execute(self, paper_id: str) -> ToolResult:
        # Better DOI detection: DOIs start with "10.<digits>/"
        if re.match(r"^10\.\d+/", paper_id) and not paper_id.startswith(("DOI:", "PMID:", "ACL:")):
            paper_id = f"DOI:{paper_id}"

        try:
            resp = None
            for attempt in range(3):
                resp = httpx.get(
                    f"{S2_BASE}/paper/{paper_id}",
                    params={"fields": S2_FIELDS},
                    timeout=30,
                )
                if resp.status_code in _RETRIABLE_STATUSES:
                    time.sleep(2 ** (attempt + 1))
                    continue
                break
            if resp.status_code == 404:
                return ToolResult(text=f"Paper not found: {paper_id}")
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return ToolResult(text=f"Paper not found: {paper_id}")
            return ToolResult(text=f"Error fetching paper details: {e}")
        except httpx.HTTPError as e:
            return ToolResult(text=f"Error fetching paper details: {e}")

        data = resp.json()
        external_ids = data.get("externalIds") or {}
        authors = [a.get("name", "") for a in data.get("authors", []) if a.get("name")]
        journal_info = data.get("journal")
        journal = journal_info.get("name") if isinstance(journal_info, dict) else None
        tldr = data.get("tldr")
        tldr_text = tldr.get("text") if isinstance(tldr, dict) else None

        abstract = clean_abstract(data.get("abstract"))

        paper = Paper(
            title=data.get("title", ""),
            authors=authors,
            year=data.get("year"),
            abstract=abstract,
            doi=external_ids.get("DOI") or data.get("doi"),
            url=data.get("url"),
            source="semantic_scholar",
            citation_count=data.get("citationCount"),
            journal=journal,
            arxiv_id=external_ids.get("ArXiv"),
            pmid=external_ids.get("PubMed"),
        )

        parts = ["Paper details:\n", paper.to_summary()]
        if tldr_text:
            parts.append(f"\nTLDR: {tldr_text}")
        return ToolResult(text="\n".join(parts), papers=[paper])
