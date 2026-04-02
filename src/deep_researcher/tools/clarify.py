"""Query clarification tool.

Uses LLM to generate clarifying questions for a research query.
"""
from __future__ import annotations

import logging

from deep_researcher.llm import LLMClient
from deep_researcher.models import ToolResult
from deep_researcher.prompts import CLARIFY_PROMPT
from deep_researcher.tools.base import Tool

logger = logging.getLogger("deep_researcher")


class ClarifyTool(Tool):
    name = "clarify_query"
    description = "Generate clarifying questions for a research query"
    is_read_only = True
    category = "utility"
    quality_tier = 1
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Research query to clarify"},
        },
        "required": ["query"],
    }

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm

    def execute(self, query: str = "", **kwargs) -> ToolResult:
        if not self._llm:
            return ToolResult(text="")

        try:
            response = self._llm.chat([
                {"role": "system", "content": CLARIFY_PROMPT},
                {"role": "user", "content": query},
            ])
            questions = (response.content or "").strip()
            return ToolResult(text=questions)
        except Exception as e:
            logger.warning("Clarification failed: %s", e)
            return ToolResult(text="")
