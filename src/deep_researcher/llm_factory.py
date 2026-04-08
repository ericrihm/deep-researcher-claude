"""LLM client factory.

Single dispatch point: Config.provider_kind -> concrete client.
Keeping this separate from llm.py and llm_claude.py means orchestrator.py
imports neither directly and the test suite's mocking pattern (which
patches Orchestrator's tools after construction) keeps working.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deep_researcher.config import Config


def make_llm_client(config: "Config"):
    """Pick the LLM client implementation based on config.provider_kind.

    Default ("openai") preserves the existing behavior so any code path
    that constructs Config() without setting provider_kind keeps using
    the OpenAI-compatible client (Ollama, OpenAI, Groq, etc.).
    """
    kind = getattr(config, "provider_kind", "openai")
    if kind == "claude_agent":
        from deep_researcher.llm_claude import ClaudeAgentLLMClient
        return ClaudeAgentLLMClient(config)
    from deep_researcher.llm import LLMClient
    return LLMClient(config)
