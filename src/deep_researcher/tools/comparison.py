"""Comparison tool.

Receives two reports (from different LLM providers on the same corpus)
and produces a structured comparison. Uses the more capable model.
Failure is non-fatal: returns an empty ToolResult.text.
"""
from __future__ import annotations

import logging

from deep_researcher.models import ToolResult
from deep_researcher.prompts import COMPARISON_PROMPT
from deep_researcher.tools.base import Tool

logger = logging.getLogger("deep_researcher")


class ComparisonTool(Tool):
    name = "comparison"
    description = "Compare two literature reviews from different LLM providers"
    is_read_only = True
    is_concurrency_safe = False
    category = "utility"
    quality_tier = 1
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self, llm=None) -> None:
        self._llm = llm

    def execute(
        self,
        query: str = "",
        report_a: str = "",
        report_b: str = "",
        provider_a: str = "Provider A",
        provider_b: str = "Provider B",
        paper_count: int = 0,
        **kwargs,
    ) -> ToolResult:
        if not report_a or not report_b or not self._llm:
            return ToolResult(text="")

        # Truncate reports to ~4000 chars each to stay within context
        max_chars = 4000
        ra = report_a[:max_chars] if len(report_a) > max_chars else report_a
        rb = report_b[:max_chars] if len(report_b) > max_chars else report_b

        prompt = COMPARISON_PROMPT.format(
            query=query,
            paper_count=paper_count,
            provider_a=provider_a,
            report_a=ra,
            provider_b=provider_b,
            report_b=rb,
        )

        try:
            content = self._llm.chat_no_think([
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Write the comparison analysis."},
            ])
            return ToolResult(text=(content or "").strip())
        except Exception as e:
            logger.warning("Comparison analysis failed: %s", e)
            return ToolResult(text="")
