"""Paper metadata enrichment tool.

Enriches papers via OpenAlex + CrossRef with concurrent HTTP
(Principle 6: declarative concurrency).

Returns NEW Paper objects — never mutates input (Principle 3: immutable state).
"""
from __future__ import annotations

import copy
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from deep_researcher.constants import MAX_TOOL_CONCURRENCY
from deep_researcher.models import Paper, ToolResult
from deep_researcher.parsing import titles_match
from deep_researcher.tools.base import Tool

logger = logging.getLogger("deep_researcher")


class EnrichmentTool(Tool):
    name = "enrich_papers"
    description = "Enrich papers with metadata from OpenAlex and CrossRef"
    is_read_only = True
    category = "utility"
    quality_tier = 1
    parameters = {
        "type": "object",
        "properties": {
            "email": {"type": "string", "description": "Email for polite API access"},
        },
        "required": [],
    }

    def execute(
        self,
        papers: list[Paper] | None = None,
        email: str = "",
        cancel: threading.Event | None = None,
    ) -> ToolResult:
        if not papers:
            return ToolResult(text="No papers to enrich", papers=[])

        email = email or "deep-researcher@example.com"
        enriched_papers: list[Paper] = []
        enriched_count = 0

        with ThreadPoolExecutor(max_workers=min(len(papers), MAX_TOOL_CONCURRENCY)) as pool:
            futures = {
                pool.submit(self._enrich_one, paper, email): paper
                for paper in papers
            }
            for future in as_completed(futures):
                if cancel and cancel.is_set():
                    break
                original = futures[future]
                try:
                    result_paper = future.result()
                    enriched_papers.append(result_paper)
                    if result_paper.doi and result_paper.doi != original.doi:
                        enriched_count += 1
                except Exception:
                    enriched_papers.append(copy.copy(original))

        has_abstract = sum(1 for p in enriched_papers if p.abstract and len(p.abstract) > 200)
        has_doi = sum(1 for p in enriched_papers if p.doi)
        return ToolResult(
            text=f"Enriched {enriched_count}/{len(papers)} | Full abstracts: {has_abstract} | DOIs: {has_doi}",
            papers=enriched_papers,
        )

    def _enrich_one(self, paper: Paper, email: str) -> Paper:
        """Enrich a single paper. Returns a new Paper (never mutates input)."""
        result = copy.copy(paper)
        ua_openalex = {"User-Agent": f"mailto:{email}"}
        ua_crossref = {"User-Agent": f"deep-researcher (mailto:{email})"}

        # Attempt 1: OpenAlex title search
        try:
            resp = httpx.get(
                "https://api.openalex.org/works",
                params={"filter": f"title.search:{paper.title[:100]}", "per_page": 1},
                headers=ua_openalex, timeout=10,
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if results and titles_match(paper.title, results[0].get("title", "")):
                    self._apply_openalex(result, results[0])
                    return result
        except Exception:
            logger.debug("OpenAlex lookup failed for '%s'", paper.title[:50], exc_info=True)

        # Attempt 2: CrossRef title -> DOI -> OpenAlex
        try:
            resp = httpx.get(
                "https://api.crossref.org/works",
                params={"query.title": paper.title, "rows": 1, "select": "DOI,title"},
                headers=ua_crossref, timeout=10,
            )
            if resp.status_code == 200:
                items = resp.json().get("message", {}).get("items", [])
                if items and items[0].get("DOI"):
                    cr_title = (items[0].get("title") or [""])[0]
                    if titles_match(paper.title, cr_title):
                        doi = items[0]["DOI"]
                        resp2 = httpx.get(
                            f"https://api.openalex.org/works/doi:{doi}",
                            headers=ua_openalex, timeout=10,
                        )
                        if resp2.status_code == 200:
                            self._apply_openalex(result, resp2.json())
                            return result
        except Exception:
            logger.debug("CrossRef lookup failed for '%s'", paper.title[:50], exc_info=True)

        return result

    @staticmethod
    def _apply_openalex(paper: Paper, work: dict) -> None:
        """Apply OpenAlex metadata to a Paper object."""
        doi = (work.get("doi") or "").replace("https://doi.org/", "")
        if doi:
            paper.doi = doi

        inv_idx = work.get("abstract_inverted_index")
        if inv_idx:
            words = [""] * (max(max(pos) for pos in inv_idx.values()) + 1)
            for word, positions in inv_idx.items():
                for pos in positions:
                    words[pos] = word
            full_abstract = " ".join(w for w in words if w)
            if full_abstract and len(full_abstract) > len(paper.abstract or ""):
                paper.abstract = full_abstract

        source = (work.get("primary_location") or {}).get("source") or {}
        if source.get("display_name"):
            paper.journal = source["display_name"]

        oa = work.get("open_access", {})
        if oa.get("oa_url"):
            paper.open_access_url = oa["oa_url"]

        oa_cites = work.get("cited_by_count", 0)
        if oa_cites and (paper.citation_count is None or oa_cites > paper.citation_count):
            paper.citation_count = oa_cites
