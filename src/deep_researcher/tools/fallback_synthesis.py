"""Fallback synthesis tool.

Single-pass literature review when multi-step synthesis fails.
This is a recovery tool (Principle 4: layered error recovery).
"""
from __future__ import annotations

import logging

from deep_researcher.constants import FALLBACK_MAX_PAPERS, FALLBACK_TOKEN_BUDGET
from deep_researcher.llm import LLMClient
from deep_researcher.models import Paper, ToolResult
from deep_researcher.parsing import build_tiered_corpus
from deep_researcher.tools.base import Tool

logger = logging.getLogger("deep_researcher")


class FallbackSynthesisTool(Tool):
    name = "fallback_synthesis"
    description = "Single-pass literature review fallback when multi-step synthesis fails"
    is_read_only = True
    category = "utility"
    quality_tier = 1
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm

    def execute(
        self,
        papers: list[Paper] | None = None,
        query: str = "",
        **kwargs,
    ) -> ToolResult:
        if not papers or not self._llm:
            return ToolResult(text="Synthesis failed: no papers or LLM")

        top_papers = papers[:FALLBACK_MAX_PAPERS]
        corpus = build_tiered_corpus(
            list(enumerate(top_papers)),
            token_budget=FALLBACK_TOKEN_BUDGET,
        )
        prompt = (
            f'Write a brief literature review on "{query}" based on these '
            f"{len(top_papers)} papers. "
            f"Categorize by theme, include a table per category.\n\n{corpus}"
        )
        try:
            content = self._llm.chat_no_think([
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Write the review."},
            ])
            return ToolResult(text=content)
        except Exception as e:
            return ToolResult(text=f"Synthesis failed: {e}")
