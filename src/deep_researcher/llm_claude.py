"""Claude Agent SDK-backed LLM client.

Drop-in replacement for LLMClient that routes chat calls through
claude_agent_sdk.query() instead of an OpenAI-compatible HTTP endpoint.
Authenticates via the local `claude` CLI session (OAuth credentials in
~/.claude/.credentials.json) — no ANTHROPIC_API_KEY required.

Exposes the same surface as LLMClient (chat_no_think, chat,
estimate_tokens) so orchestrator and tools can be swapped onto it via
the llm_factory without any other code change.

Concurrency: each chat_no_think() call spins up a fresh asyncio event
loop and runs query() to completion. This is safe under
ThreadPoolExecutor (each worker thread gets its own loop with no shared
state) and matches the existing per-category synthesis pattern in
orchestrator._run_synthesis (verified by smoke_test_p2.py).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator

from deep_researcher.constants import CHARS_PER_TOKEN

if TYPE_CHECKING:
    from deep_researcher.config import Config

logger = logging.getLogger("deep_researcher")


# Default model when --model isn't passed. Sonnet 4.5 is the right
# quality/cost balance for synthesis; users can override via --model.
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5"

# Tools we explicitly forbid the SDK from using during a chat call.
# deep-researcher's prompts are pure-text-generation tasks; we don't
# want Claude wandering off to read files or run commands.
_DISALLOWED_TOOLS = [
    "Read", "Write", "Edit", "Bash", "Glob", "Grep",
    "WebFetch", "WebSearch", "TodoWrite", "Task", "Skill",
]


@contextlib.contextmanager
def _scrub_anthropic_env() -> Iterator[None]:
    """Temporarily remove ANTHROPIC_API_KEY from the environment.

    Why: when --provider claude is selected the user's intent is "use
    OAuth from my `claude login` session". If ANTHROPIC_API_KEY happens
    to be set in env (commonly by other tools, or from a previous
    session), the SDK / bundled CLI will silently prefer it over the
    OAuth credentials — the opposite of what the flag says.

    Robust choice vs. passing env= to ClaudeAgentOptions: a context
    manager doesn't depend on whether the SDK merges or replaces the
    subprocess env, and it restores state on any exception path.
    """
    had_key = "ANTHROPIC_API_KEY" in os.environ
    old_value = os.environ.get("ANTHROPIC_API_KEY")
    if had_key:
        del os.environ["ANTHROPIC_API_KEY"]
    try:
        yield
    finally:
        if had_key:
            os.environ["ANTHROPIC_API_KEY"] = old_value  # type: ignore[assignment]


@dataclass
class _ShimResponse:
    """Mimics the openai ChatCompletionMessage shape that
    LLMClient.chat() returns. Only .content and .tool_calls are read by
    the existing tools (clarify.py:43)."""
    content: str
    tool_calls: list = field(default_factory=list)


class ClaudeAgentLLMClient:
    """LLMClient lookalike that uses claude_agent_sdk under the hood.

    Authentication is delegated to the bundled CLI, which uses OAuth
    credentials from `claude login`. No API key handling here.
    """

    def __init__(self, config: "Config") -> None:
        # Lazy import — only fails when this client is actually selected.
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "claude_agent_sdk is not installed. "
                "Install with: pip install claude-agent-sdk"
            ) from e

        self.model = config.model or DEFAULT_CLAUDE_MODEL
        self._max_retries = 3

    # ------------------------------------------------------------------
    # LLMClient-compatible surface
    # ------------------------------------------------------------------

    def chat_no_think(self, messages: list[dict]) -> str:
        """Sync entry used by all 4 synthesis-path tools.

        Same retry-with-exponential-backoff posture as LLMClient.chat_no_think
        (llm.py:90-107) so the orchestrator's recovery layers behave the same.
        """
        system, user = self._split_messages(messages)
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return self._run_async(self._aquery(system, user))
            except Exception as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(
                    "ClaudeAgentLLMClient call failed (attempt %d/%d): %s",
                    attempt + 1, self._max_retries, e,
                )
                time.sleep(wait)
        raise RuntimeError(
            f"ClaudeAgentLLMClient failed after {self._max_retries} retries: {last_error}"
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> _ShimResponse:
        """Compatibility shim for clarify.py.

        deep-researcher's tools= parameter is dead code in the current
        codebase (only LLMClient.chat() accepts it; the synthesis path
        uses chat_no_think which has no tools concept). We accept and
        ignore it for signature compatibility.
        """
        if tools:
            logger.debug(
                "ClaudeAgentLLMClient.chat() ignoring %d tools — Claude Agent "
                "SDK shim does not implement OpenAI-style function calling. "
                "deep-researcher does not exercise this path in v0.5.0.",
                len(tools),
            )
        text = self.chat_no_think(messages)
        return _ShimResponse(content=text, tool_calls=[])

    @staticmethod
    def estimate_tokens(messages: list[dict]) -> int:
        """Same heuristic as LLMClient.estimate_tokens (llm.py:111)."""
        total_chars = 0
        for msg in messages:
            content = msg.get("content") or ""
            total_chars += len(content)
            for tc in msg.get("tool_calls", []):
                total_chars += len(tc.get("function", {}).get("arguments", ""))
                total_chars += 200
        return total_chars // CHARS_PER_TOKEN

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _split_messages(messages: list[dict]) -> tuple[str, str]:
        """All 5 LLM call sites in deep-researcher pass exactly two
        messages: a system message and a user message. Concatenate
        anything unusual into the user prompt rather than dropping it.
        """
        system_parts: list[str] = []
        user_parts: list[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content") or ""
            if role == "system":
                system_parts.append(content)
            else:
                user_parts.append(content)
        return ("\n\n".join(system_parts), "\n\n".join(user_parts))

    async def _aquery(self, system: str, user: str) -> str:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            system_prompt=system,            # plain string -> fully replaces default
            model=self.model,
            max_turns=1,                     # one-shot completion, not an agent loop
            allowed_tools=[],
            disallowed_tools=_DISALLOWED_TOOLS,
            setting_sources=[],              # don't load CLAUDE.md or skills
        )

        chunks: list[str] = []
        async for msg in query(prompt=user, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
        return "".join(chunks).strip()

    @staticmethod
    def _run_async(coro: Any) -> Any:
        """Run an async coroutine in a fresh event loop.

        Why a fresh loop per call instead of asyncio.run(): asyncio.run()
        also creates a fresh loop, but errors out if there's already a
        running loop in the current thread. Using new_event_loop +
        run_until_complete is the same in practice but more robust if
        deep-researcher is ever embedded in an async host.
        """
        loop = asyncio.new_event_loop()
        try:
            with _scrub_anthropic_env():
                return loop.run_until_complete(coro)
        finally:
            loop.close()
