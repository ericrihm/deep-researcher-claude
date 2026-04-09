"""Executive summary tool.

Writes a 100-150 word paragraph TL;DR of the corpus for the top of
the HTML report. Runs in parallel with per-category synthesis.
Failure is silent: returns an empty ToolResult.text so the HTML
renderer can drop the block without error.
"""
from __future__ import annotations

import logging

from deep_researcher.llm import LLMClient
from deep_researcher.models import Paper, ToolResult
from deep_researcher.prompts import EXECUTIVE_SUMMARY_PROMPT
from deep_researcher.tools.base import Tool

logger = logging.getLogger("deep_researcher")


class ExecutiveSummaryTool(Tool):
    name = "executive_summary"
    description = "Write a 100-150 word paragraph TL;DR for the whole corpus"
    is_read_only = True
    is_concurrency_safe = True
    category = "utility"
    quality_tier = 1
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm

    def execute(
        self,
        query: str = "",
        synthesis_papers: list[Paper] | None = None,
        categories: dict[str, list[int]] | None = None,
        **kwargs,
    ) -> ToolResult:
        papers = synthesis_papers or []
        if not papers or not self._llm:
            return ToolResult(text="")

        cats = categories or {}
        cat_count = len(cats)
        category_list = "\n".join(
            f"- {name} ({len(ixs)} papers)" for name, ixs in cats.items()
        ) or "- (uncategorized)"

        top_n = min(10, len(papers))
        # Papers are already sorted by citation count desc in the orchestrator,
        # but re-sort defensively so this tool is safe to call standalone.
        top = sorted(
            papers,
            key=lambda p: (-(p.citation_count or 0), -(p.year or 0)),
        )[:top_n]
        top_papers = "\n".join(
            _format_top_paper(p) for p in top
        )

        prompt = EXECUTIVE_SUMMARY_PROMPT.format(
            query=query,
            count=len(papers),
            cat_count=cat_count,
            category_list=category_list,
            top_n=top_n,
            top_papers=top_papers,
        )

        try:
            content = self._llm.chat_no_think([
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Write the executive summary."},
            ])
            return ToolResult(text=(content or "").strip())
        except Exception as e:
            logger.warning("Executive summary failed: %s", e)
            return ToolResult(text="")


def _format_top_paper(p: Paper) -> str:
    title = (p.title or "(untitled)").strip()
    year = p.year if p.year is not None else "n.d."
    first_author = p.authors[0] if p.authors else "Unknown"
    cites = p.citation_count if p.citation_count is not None else 0
    return f"- {title} ({year}) — {first_author} — {cites} cites"
