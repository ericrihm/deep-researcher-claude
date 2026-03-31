from __future__ import annotations

import httpx

from deep_researcher.models import Paper
from deep_researcher.tools.base import Tool

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,authors,year,abstract,doi,url,citationCount,journal,externalIds,tldr"


class PaperDetailsTool(Tool):
    name = "get_paper_details"
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

    def execute(self, paper_id: str) -> str:
        if "/" in paper_id and not paper_id.startswith(("ARXIV:", "DOI:", "PMID:", "ACL:")):
            paper_id = f"DOI:{paper_id}"

        try:
            resp = httpx.get(
                f"{S2_BASE}/paper/{paper_id}",
                params={"fields": S2_FIELDS},
                timeout=30,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Paper not found: {paper_id}"
            return f"Error fetching paper details: {e}"
        except httpx.HTTPError as e:
            return f"Error fetching paper details: {e}"

        data = resp.json()
        external_ids = data.get("externalIds") or {}
        authors = [a.get("name", "") for a in data.get("authors", []) if a.get("name")]
        journal_info = data.get("journal")
        journal = journal_info.get("name") if isinstance(journal_info, dict) else None
        tldr = data.get("tldr")
        tldr_text = tldr.get("text") if isinstance(tldr, dict) else None

        paper = Paper(
            title=data.get("title", ""),
            authors=authors,
            year=data.get("year"),
            abstract=data.get("abstract"),
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
        return "\n".join(parts)
