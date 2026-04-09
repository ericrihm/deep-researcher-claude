# MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose deep-researcher's literature review pipeline as an MCP server so Claude Desktop/Code can perform academic research mid-conversation.

**Architecture:** Thin FastMCP wrapper over the existing Orchestrator. Two new files — `mcp_server.py` (server registration + entry point) and `mcp_tools.py` (tool handlers that build Config, run Orchestrator headlessly, return structured JSON). No changes to existing orchestrator/tool code.

**Tech Stack:** `mcp[cli]>=1.0.0` (FastMCP), existing deep-researcher internals.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/deep_researcher/mcp_tools.py` | Create | Tool handler functions: `handle_research`, `handle_search_papers`, `handle_synthesize`, `handle_compare`, `handle_list_runs`. Each builds a headless Config+Orchestrator, runs it, returns a dict. |
| `src/deep_researcher/mcp_server.py` | Create | FastMCP server instance. Registers tools, resources, prompts. Entry point (`main()`). |
| `pyproject.toml` | Modify | Add `mcp` optional dep, add `deep-researcher-mcp` console script. |
| `tests/test_mcp_tools.py` | Create | Unit tests for all tool handlers with mocked Orchestrator. |
| `tests/test_mcp_server.py` | Create | Integration tests: server starts, tools are registered, basic call/response. |

---

### Task 1: mcp_tools.py — headless research handler

**Files:**
- Create: `src/deep_researcher/mcp_tools.py`
- Test: `tests/test_mcp_tools.py`

This task builds the core `handle_research()` function that wraps the Orchestrator for headless (no-terminal) use, plus `handle_list_runs()` which is pure filesystem logic.

- [ ] **Step 1: Write the failing test for handle_research**

Create `tests/test_mcp_tools.py`:

```python
"""Tests for MCP tool handlers."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest


def test_handle_research_returns_structured_result(tmp_path):
    """handle_research runs Orchestrator.research() and returns structured JSON."""
    from deep_researcher.mcp_tools import handle_research

    # Mock the Orchestrator so we don't make real API calls
    mock_orch_instance = MagicMock()
    mock_orch_instance.research.return_value = "### Test Report\n\nSome content"
    mock_orch_instance.last_report_paths = {
        "html": str(tmp_path / "report.html"),
        "markdown": str(tmp_path / "report.md"),
        "bibtex": str(tmp_path / "references.bib"),
        "papers_json": str(tmp_path / "papers.json"),
        "papers_csv": str(tmp_path / "papers.csv"),
    }

    with patch("deep_researcher.mcp_tools.Orchestrator", return_value=mock_orch_instance):
        result = handle_research(
            query="test query",
            output_dir=str(tmp_path),
        )

    assert result["report_markdown"] == "### Test Report\n\nSome content"
    assert "files" in result
    mock_orch_instance.research.assert_called_once_with("test query")


def test_handle_research_with_provider_override(tmp_path):
    """handle_research applies provider/model overrides to Config."""
    from deep_researcher.mcp_tools import handle_research

    mock_orch_instance = MagicMock()
    mock_orch_instance.research.return_value = "report"
    mock_orch_instance.last_report_paths = {}

    with patch("deep_researcher.mcp_tools.Orchestrator") as MockOrch:
        MockOrch.return_value = mock_orch_instance
        result = handle_research(
            query="test",
            provider="openai",
            model="gpt-4o",
            output_dir=str(tmp_path),
        )

    # Verify Config was built with the right provider settings
    config_arg = MockOrch.call_args[0][0]
    assert config_arg.model == "gpt-4o"
    assert "openai" in config_arg.base_url


def test_handle_research_error_returns_error_dict(tmp_path):
    """handle_research returns error dict on failure, not exception."""
    from deep_researcher.mcp_tools import handle_research

    mock_orch_instance = MagicMock()
    mock_orch_instance.research.side_effect = RuntimeError("LLM down")
    mock_orch_instance.last_report_paths = {}

    with patch("deep_researcher.mcp_tools.Orchestrator", return_value=mock_orch_instance):
        result = handle_research(query="test", output_dir=str(tmp_path))

    assert "error" in result
    assert "LLM down" in result["error"]


def test_handle_list_runs_reads_metadata(tmp_path):
    """handle_list_runs finds output folders and reads their metadata."""
    from deep_researcher.mcp_tools import handle_list_runs

    # Create a fake run folder with metadata
    run_dir = tmp_path / "2026-04-09-161823-test-query"
    run_dir.mkdir()
    meta = {
        "query": "test query",
        "generated": "2026-04-09T16:18:23",
        "total_papers": 42,
    }
    (run_dir / "metadata.json").write_text(json.dumps(meta))
    (run_dir / "report.html").write_text("<html></html>")

    result = handle_list_runs(output_dir=str(tmp_path), limit=10)

    assert len(result["runs"]) == 1
    run = result["runs"][0]
    assert run["query"] == "test query"
    assert run["paper_count"] == 42
    assert run["has_html"] is True


def test_handle_list_runs_empty_dir(tmp_path):
    """handle_list_runs returns empty list for empty output dir."""
    from deep_researcher.mcp_tools import handle_list_runs

    result = handle_list_runs(output_dir=str(tmp_path), limit=10)
    assert result["runs"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude && python -m pytest tests/test_mcp_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_researcher.mcp_tools'`

- [ ] **Step 3: Implement mcp_tools.py**

Create `src/deep_researcher/mcp_tools.py`:

```python
"""MCP tool handler implementations.

Each function builds a headless Config + Orchestrator, runs a pipeline
method, and returns a plain dict suitable for JSON serialization.
No MCP imports here — this module is pure business logic so it can be
tested without the MCP SDK installed.
"""
from __future__ import annotations

import io
import json
import logging
import os

from rich.console import Console

from deep_researcher.config import Config
from deep_researcher.orchestrator import Orchestrator

logger = logging.getLogger("deep_researcher.mcp")

# Provider presets — duplicated from __main__.py to avoid importing the
# CLI module (which has side effects like argparse setup). Kept in sync
# manually; the canonical list lives in __main__.PROVIDERS.
PROVIDERS: dict[str, dict[str, str]] = {
    "ollama": {"base_url": "http://localhost:11434/v1", "api_key": "ollama", "default_model": "qwen3.5:9b"},
    "lmstudio": {"base_url": "http://localhost:1234/v1", "api_key": "lm-studio", "default_model": "default"},
    "openai": {"base_url": "https://api.openai.com/v1", "api_key": "", "default_model": "gpt-5.4-mini"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "api_key": "", "default_model": "claude-sonnet-4-6"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "api_key": "", "default_model": "qwen/qwen3-32b"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": "", "default_model": "deepseek-chat"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key": "", "default_model": "anthropic/claude-sonnet-4-6"},
    "together": {"base_url": "https://api.together.xyz/v1", "api_key": "", "default_model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct"},
    "claude": {"base_url": "", "api_key": "", "default_model": "claude-sonnet-4-5"},
    "chatgpt": {"base_url": "https://chatgpt.com/backend-api/codex", "api_key": "", "default_model": "gpt-5"},
}


def _build_config(
    *,
    provider: str | None = None,
    model: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    no_elsevier: bool = False,
    output_dir: str = "./output",
) -> Config:
    """Build a Config with optional provider/model overrides."""
    config = Config(output_dir=output_dir)

    if provider and provider in PROVIDERS:
        preset = PROVIDERS[provider]
        config.base_url = preset["base_url"]
        if preset["api_key"]:
            config.api_key = preset["api_key"]
        config.model = preset["default_model"]
        if provider == "claude":
            config.provider_kind = "claude_agent"
        elif provider == "chatgpt":
            config.provider_kind = "chatgpt_oauth"
        else:
            config.provider_kind = "openai"

    if model:
        config.model = model
    if start_year is not None:
        config.start_year = start_year
    if end_year is not None:
        config.end_year = end_year
    config.no_elsevier = no_elsevier

    return config


def _quiet_console() -> Console:
    """Console that discards output (headless mode)."""
    return Console(file=io.StringIO(), quiet=True)


def _extract_result(orchestrator: Orchestrator, report: str) -> dict:
    """Build the structured result dict from orchestrator state."""
    return {
        "report_markdown": report,
        "files": dict(orchestrator.last_report_paths),
    }


def handle_research(
    query: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    no_elsevier: bool = False,
    output_dir: str = "./output",
) -> dict:
    """Run the full research pipeline and return structured results."""
    try:
        config = _build_config(
            provider=provider,
            model=model,
            start_year=start_year,
            end_year=end_year,
            no_elsevier=no_elsevier,
            output_dir=output_dir,
        )
        orch = Orchestrator(config)
        orch.console = _quiet_console()

        report = orch.research(query)
        return _extract_result(orch, report)
    except Exception as e:
        logger.exception("handle_research failed")
        return {"error": str(e), "phase": "research"}


def handle_search_papers(
    query: str,
    *,
    start_year: int | None = None,
    end_year: int | None = None,
    no_elsevier: bool = False,
    output_dir: str = "./output",
) -> dict:
    """Search + enrich only, return paper metadata without synthesis."""
    try:
        config = _build_config(
            start_year=start_year,
            end_year=end_year,
            no_elsevier=no_elsevier,
            output_dir=output_dir,
        )
        orch = Orchestrator(config)
        orch.console = _quiet_console()

        from deep_researcher.models import PipelineState
        state = PipelineState(query=query)

        # Phase 1: Search
        state = orch._run_search(state)
        if not state.papers:
            return {"papers": [], "total_count": 0, "year_range": "", "sources": {}}

        # Phase 2: Enrich
        state = orch._run_enrichment(state)

        # Build response
        papers_list = []
        source_counts: dict[str, int] = {}
        for p in state.papers.values():
            papers_list.append({
                "title": p.title,
                "authors": p.authors,
                "year": p.year,
                "abstract": p.abstract or "",
                "doi": p.doi or "",
                "journal": p.journal or "",
                "citation_count": p.citation_count or 0,
                "open_access_url": p.open_access_url or "",
                "source": p.source or "",
            })
            for s in (p.source or "").split(","):
                s = s.strip()
                if s:
                    source_counts[s] = source_counts.get(s, 0) + 1

        years = [p.year for p in state.papers.values() if p.year]
        yr_range = f"{min(years)}-{max(years)}" if years else ""

        return {
            "papers": papers_list,
            "total_count": len(papers_list),
            "year_range": yr_range,
            "sources": source_counts,
        }
    except Exception as e:
        logger.exception("handle_search_papers failed")
        return {"error": str(e), "phase": "search"}


def handle_synthesize(
    folder: str,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """Re-run synthesis on an existing output folder (replay)."""
    try:
        config = _build_config(provider=provider, model=model)
        orch = Orchestrator(config)
        orch.console = _quiet_console()

        report = orch.replay(folder)
        return _extract_result(orch, report)
    except Exception as e:
        logger.exception("handle_synthesize failed")
        return {"error": str(e), "phase": "synthesize"}


def handle_compare(
    query: str,
    provider_a: str,
    provider_b: str,
    *,
    start_year: int | None = None,
    end_year: int | None = None,
    output_dir: str = "./output",
) -> dict:
    """Run dual-provider comparison research."""
    try:
        config = _build_config(
            provider=provider_a,
            start_year=start_year,
            end_year=end_year,
            output_dir=output_dir,
        )
        orch = Orchestrator(config)
        orch.console = _quiet_console()

        report_a, report_b = orch.compare_research(
            query, provider_a, provider_b, PROVIDERS,
        )
        return {
            "report_a": report_a,
            "report_b": report_b,
            "provider_a": provider_a,
            "provider_b": provider_b,
            "output_folder": orch.last_report_paths.get("compare_folder", ""),
            "compare_html": orch.last_report_paths.get("html", ""),
        }
    except Exception as e:
        logger.exception("handle_compare failed")
        return {"error": str(e), "phase": "compare"}


def handle_list_runs(
    *,
    output_dir: str = "./output",
    limit: int = 20,
) -> dict:
    """List previous research runs from the output directory."""
    runs: list[dict] = []
    if not os.path.isdir(output_dir):
        return {"runs": []}

    folders = sorted(
        (
            entry
            for entry in os.scandir(output_dir)
            if entry.is_dir()
        ),
        key=lambda e: e.name,
        reverse=True,
    )

    for entry in folders[:limit]:
        meta_path = os.path.join(entry.path, "metadata.json")
        run_info: dict = {
            "folder": entry.path,
            "query": "",
            "generated": "",
            "paper_count": 0,
            "mode": "single",
            "has_html": os.path.exists(os.path.join(entry.path, "report.html")),
        }
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                run_info["query"] = meta.get("query", "")
                run_info["generated"] = meta.get("generated", "")
                run_info["paper_count"] = meta.get("total_papers", 0)
                run_info["mode"] = meta.get("mode", "single")
            except (json.JSONDecodeError, OSError):
                pass
        runs.append(run_info)

    return {"runs": runs}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude && python -m pytest tests/test_mcp_tools.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude
git add src/deep_researcher/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): add tool handler implementations"
```

---

### Task 2: mcp_tools.py — search_papers and compare handlers + tests

**Files:**
- Modify: `tests/test_mcp_tools.py`

Add tests for `handle_search_papers`, `handle_synthesize`, and `handle_compare`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_tools.py`:

```python
def test_handle_search_papers_returns_paper_list(tmp_path):
    """handle_search_papers returns enriched paper metadata without synthesis."""
    from deep_researcher.mcp_tools import handle_search_papers
    from deep_researcher.models import Paper, PipelineState

    papers = {
        "key1": Paper(title="Paper A", authors=["Auth1"], year=2024, source="scholar"),
        "key2": Paper(title="Paper B", authors=["Auth2"], year=2023, doi="10.1/x", source="scopus"),
    }

    def mock_search(state):
        return state.evolve(papers=papers)

    def mock_enrich(state):
        return state  # passthrough

    mock_orch = MagicMock()
    mock_orch._run_search = mock_search
    mock_orch._run_enrichment = mock_enrich
    mock_orch.console = MagicMock()

    with patch("deep_researcher.mcp_tools.Orchestrator", return_value=mock_orch):
        result = handle_search_papers(query="test", output_dir=str(tmp_path))

    assert result["total_count"] == 2
    assert result["year_range"] == "2023-2024"
    titles = {p["title"] for p in result["papers"]}
    assert titles == {"Paper A", "Paper B"}


def test_handle_search_papers_no_results(tmp_path):
    """handle_search_papers returns empty on no papers found."""
    from deep_researcher.mcp_tools import handle_search_papers
    from deep_researcher.models import PipelineState

    mock_orch = MagicMock()
    mock_orch._run_search = lambda state: state.evolve(papers={})
    mock_orch.console = MagicMock()

    with patch("deep_researcher.mcp_tools.Orchestrator", return_value=mock_orch):
        result = handle_search_papers(query="nothing", output_dir=str(tmp_path))

    assert result["total_count"] == 0
    assert result["papers"] == []


def test_handle_synthesize_calls_replay(tmp_path):
    """handle_synthesize delegates to Orchestrator.replay()."""
    from deep_researcher.mcp_tools import handle_synthesize

    mock_orch = MagicMock()
    mock_orch.replay.return_value = "Replayed report"
    mock_orch.last_report_paths = {"html": str(tmp_path / "report.html")}

    with patch("deep_researcher.mcp_tools.Orchestrator", return_value=mock_orch):
        result = handle_synthesize(folder=str(tmp_path))

    assert result["report_markdown"] == "Replayed report"
    mock_orch.replay.assert_called_once_with(str(tmp_path))


def test_handle_compare_calls_compare_research(tmp_path):
    """handle_compare delegates to Orchestrator.compare_research()."""
    from deep_researcher.mcp_tools import handle_compare

    mock_orch = MagicMock()
    mock_orch.compare_research.return_value = ("Report A", "Report B")
    mock_orch.last_report_paths = {
        "compare_folder": str(tmp_path),
        "html": str(tmp_path / "compare.html"),
    }

    with patch("deep_researcher.mcp_tools.Orchestrator", return_value=mock_orch):
        result = handle_compare(
            query="test",
            provider_a="claude",
            provider_b="openai",
            output_dir=str(tmp_path),
        )

    assert result["report_a"] == "Report A"
    assert result["report_b"] == "Report B"
    assert result["provider_a"] == "claude"
    assert result["provider_b"] == "openai"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude && python -m pytest tests/test_mcp_tools.py -v`
Expected: 9 PASSED

- [ ] **Step 3: Commit**

```bash
cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude
git add tests/test_mcp_tools.py
git commit -m "test(mcp): add tests for search, synthesize, and compare handlers"
```

---

### Task 3: mcp_server.py — FastMCP server with tools, resources, and prompts

**Files:**
- Create: `src/deep_researcher/mcp_server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_server.py`:

```python
"""Tests for the MCP server registration and basic tool dispatch."""
from __future__ import annotations

import pytest

# Gate: skip entire module if mcp is not installed
mcp = pytest.importorskip("mcp")


def test_server_instance_exists():
    """The mcp_server module exposes a FastMCP 'mcp' instance."""
    from deep_researcher.mcp_server import mcp as server
    from mcp.server.fastmcp import FastMCP
    assert isinstance(server, FastMCP)


def test_tools_registered():
    """All expected tools are registered on the server."""
    from deep_researcher.mcp_server import mcp as server
    # FastMCP stores tools in _tool_manager
    tool_names = set()
    for tool in server._tool_manager._tools.values():
        tool_names.add(tool.name)
    expected = {"research", "search_papers", "synthesize", "compare", "list_runs"}
    assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"


def test_prompts_registered():
    """Expected prompts are registered."""
    from deep_researcher.mcp_server import mcp as server
    prompt_names = set(server._prompt_manager._prompts.keys())
    expected = {"literature_review", "find_papers"}
    assert expected.issubset(prompt_names), f"Missing prompts: {expected - prompt_names}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude && python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_researcher.mcp_server'`

- [ ] **Step 3: Implement mcp_server.py**

Create `src/deep_researcher/mcp_server.py`:

```python
"""MCP server for Deep Researcher.

Exposes the literature review pipeline as MCP tools so Claude Desktop,
Claude Code, and other MCP clients can perform academic research
mid-conversation.

Entry points:
    deep-researcher-mcp          (console script)
    python -m deep_researcher.mcp_server
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Context

from deep_researcher.mcp_tools import (
    handle_compare,
    handle_list_runs,
    handle_research,
    handle_search_papers,
    handle_synthesize,
)

mcp = FastMCP(
    name="deep-researcher",
    instructions=(
        "Academic literature review server. Search Google Scholar, "
        "enrich papers with OpenAlex metadata, and synthesize structured "
        "literature reviews using LLMs. Tools run long (2-5 min for full "
        "research) — use search_papers for quick lookups."
    ),
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="research",
    description=(
        "Run a full academic literature review: search Google Scholar + Scopus "
        "for up to 100 papers, enrich with OpenAlex metadata, then synthesize "
        "a structured review with categories, cross-analysis, and references. "
        "Takes 2-5 minutes. Returns the report markdown and output file paths."
    ),
)
async def tool_research(
    query: str,
    provider: str | None = None,
    model: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    no_elsevier: bool = False,
    ctx: Context | None = None,
) -> dict:
    if ctx:
        await ctx.info("Starting research pipeline...")
        await ctx.report_progress(0, 3, "Searching academic databases")

    result = handle_research(
        query=query,
        provider=provider,
        model=model,
        start_year=start_year,
        end_year=end_year,
        no_elsevier=no_elsevier,
    )

    if ctx and "error" not in result:
        await ctx.report_progress(3, 3, "Research complete")

    return result


@mcp.tool(
    name="search_papers",
    description=(
        "Search Google Scholar + Scopus and return enriched paper metadata "
        "WITHOUT running synthesis. Fast (30-60s). Use this for quick lookups, "
        "exploring a topic, or deciding whether to run a full synthesis."
    ),
)
async def tool_search_papers(
    query: str,
    start_year: int | None = None,
    end_year: int | None = None,
    no_elsevier: bool = False,
    ctx: Context | None = None,
) -> dict:
    if ctx:
        await ctx.info("Searching academic databases...")

    return handle_search_papers(
        query=query,
        start_year=start_year,
        end_year=end_year,
        no_elsevier=no_elsevier,
    )


@mcp.tool(
    name="synthesize",
    description=(
        "Re-run LLM synthesis on an existing research output folder "
        "(papers.json must exist). Equivalent to --replay. Useful for "
        "re-synthesizing with a different model or after code changes."
    ),
)
async def tool_synthesize(
    folder: str,
    provider: str | None = None,
    model: str | None = None,
    ctx: Context | None = None,
) -> dict:
    if ctx:
        await ctx.info(f"Re-synthesizing papers in {folder}...")

    return handle_synthesize(folder=folder, provider=provider, model=model)


@mcp.tool(
    name="compare",
    description=(
        "Compare two LLM providers on the same paper corpus. Searches once, "
        "then synthesizes in parallel with both providers and produces a "
        "comparison analysis. Returns both reports plus the analysis."
    ),
)
async def tool_compare(
    query: str,
    provider_a: str,
    provider_b: str,
    start_year: int | None = None,
    end_year: int | None = None,
    ctx: Context | None = None,
) -> dict:
    if ctx:
        await ctx.info(f"Comparing {provider_a} vs {provider_b}...")

    return handle_compare(
        query=query,
        provider_a=provider_a,
        provider_b=provider_b,
        start_year=start_year,
        end_year=end_year,
    )


@mcp.tool(
    name="list_runs",
    description=(
        "List previous research runs from the output directory. "
        "Returns folder paths, queries, dates, and paper counts."
    ),
)
async def tool_list_runs(
    output_dir: str = "./output",
    limit: int = 20,
) -> dict:
    return handle_list_runs(output_dir=output_dir, limit=limit)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource(
    "research://runs",
    name="research_runs",
    description="List all research output folders with metadata",
    mime_type="application/json",
)
async def resource_runs() -> str:
    import json
    result = handle_list_runs(output_dir="./output", limit=100)
    return json.dumps(result, indent=2)


@mcp.resource(
    "research://run/{folder_name}/report.md",
    name="run_report_md",
    description="Markdown report from a specific run",
    mime_type="text/markdown",
)
async def resource_report_md(folder_name: str) -> str:
    import os
    path = os.path.join("./output", folder_name, "report.md")
    if not os.path.exists(path):
        return f"No report.md found in {folder_name}"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@mcp.resource(
    "research://run/{folder_name}/papers.json",
    name="run_papers_json",
    description="Paper metadata from a specific run",
    mime_type="application/json",
)
async def resource_papers_json(folder_name: str) -> str:
    import os
    path = os.path.join("./output", folder_name, "papers.json")
    if not os.path.exists(path):
        return "[]"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@mcp.prompt(
    name="literature_review",
    description="Run a full literature review on a topic and summarize findings",
)
async def prompt_literature_review(topic: str) -> str:
    return (
        f"You are an academic research assistant. The user wants a literature "
        f"review on: {topic}\n\n"
        f"Use the `research` tool to search Google Scholar, enrich papers with "
        f"OpenAlex metadata, and produce a structured synthesis. After the "
        f"research completes:\n\n"
        f"1. Summarize the key findings in 2-3 paragraphs\n"
        f"2. Highlight the most-cited papers\n"
        f"3. Note any gaps or contradictions the synthesis identified\n"
        f"4. Offer to drill into specific categories or open the HTML report"
    )


@mcp.prompt(
    name="find_papers",
    description="Search for papers on a topic and summarize what you find",
)
async def prompt_find_papers(topic: str, year_range: str = "") -> str:
    year_line = f"\nLimit to papers published {year_range}." if year_range else ""
    return (
        f"Search for academic papers on: {topic}{year_line}\n\n"
        f"Use the `search_papers` tool to find relevant papers. After searching:\n"
        f"1. List the top 10 most-cited papers with title, year, and citation count\n"
        f"2. Summarize the main research themes you see\n"
        f"3. Ask if the user wants to run a full synthesis on these papers"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude && python -m pytest tests/test_mcp_server.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude
git add src/deep_researcher/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add FastMCP server with tools, resources, and prompts"
```

---

### Task 4: pyproject.toml — optional dependency + console script

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mcp_server.py`:

```python
def test_console_script_entry_point():
    """The deep-researcher-mcp console script is configured in pyproject.toml."""
    import importlib.metadata
    eps = importlib.metadata.entry_points()
    # In Python 3.12+, entry_points() returns a dict-like; in 3.10-3.11
    # it returns SelectableGroups. Handle both.
    if hasattr(eps, "select"):
        scripts = eps.select(group="console_scripts")
    else:
        scripts = eps.get("console_scripts", [])
    names = [ep.name for ep in scripts]
    assert "deep-researcher-mcp" in names, f"Entry point not found. Available: {names}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude && python -m pytest tests/test_mcp_server.py::test_console_script_entry_point -v`
Expected: FAIL with `AssertionError: Entry point not found`

- [ ] **Step 3: Update pyproject.toml**

In `pyproject.toml`, add the `mcp` optional dependency and the new console script:

Change the `[project.optional-dependencies]` section to:
```toml
[project.optional-dependencies]
mcp = ["mcp[cli]>=1.0.0"]
dev = ["pytest>=8.0.0"]
```

Change the `[project.scripts]` section to:
```toml
[project.scripts]
deep-researcher = "deep_researcher.__main__:main"
deep-researcher-mcp = "deep_researcher.mcp_server:main"
```

- [ ] **Step 4: Re-install in editable mode and verify**

Run: `cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude && pip install -e ".[mcp,dev]" 2>&1 | tail -3`

- [ ] **Step 5: Run test to verify it passes**

Run: `cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude && python -m pytest tests/test_mcp_server.py -v`
Expected: 4 PASSED

- [ ] **Step 6: Run full test suite**

Run: `cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude && python -m pytest tests/ -q --tb=short`
Expected: All tests pass (193 existing + new MCP tests)

- [ ] **Step 7: Commit**

```bash
cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude
git add pyproject.toml
git commit -m "feat(mcp): add mcp optional dependency and deep-researcher-mcp entry point"
```

---

### Task 5: README update + push

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add MCP server section to README**

After the "Compare two providers" section and before "Run with Ollama", add:

```markdown
### Use as an MCP server (Claude Desktop / Claude Code)

Deep Researcher can run as an [MCP server](https://modelcontextprotocol.io), letting Claude search papers and write literature reviews mid-conversation:

```bash
pip install deep-researcher[mcp]
```

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "deep-researcher": {
      "command": "deep-researcher-mcp"
    }
  }
}
```

Or for Claude Code (`.claude/settings.json`):

```json
{
  "mcpServers": {
    "deep-researcher": {
      "command": "deep-researcher-mcp"
    }
  }
}
```

**Available tools:**

| Tool | What it does |
|---|---|
| `research` | Full pipeline: search + enrich + synthesize (2-5 min) |
| `search_papers` | Search only, return paper metadata (30-60s) |
| `synthesize` | Re-run synthesis on existing papers.json |
| `compare` | Dual-provider side-by-side comparison |
| `list_runs` | Browse previous research output folders |
```

- [ ] **Step 2: Update test badge count**

Update the badge to the new test count after all tests pass.

- [ ] **Step 3: Commit and push**

```bash
cd C:/Users/ericr/OneDrive/Documents/deep-researcher-claude
git add README.md
git commit -m "docs: add MCP server section to README"
git push origin main
```

---

## Self-Review

**Spec coverage check:**
- `research` tool: Task 1 (handler) + Task 3 (registration)
- `search_papers` tool: Task 1 (handler) + Task 2 (tests) + Task 3 (registration)
- `synthesize` tool: Task 1 (handler) + Task 2 (tests) + Task 3 (registration)
- `compare` tool: Task 1 (handler) + Task 2 (tests) + Task 3 (registration)
- `list_runs` tool: Task 1 (handler + tests) + Task 3 (registration)
- Resources (`research://runs`, `research://run/{}/report.md`, `research://run/{}/papers.json`): Task 3
- Prompts (`literature_review`, `find_papers`): Task 3
- Entry points (`deep-researcher-mcp`, `python -m`): Task 3 + Task 4
- Optional dependency (`mcp[cli]`): Task 4
- README docs: Task 5
- Headless Orchestrator (quiet Console): Task 1 (`_quiet_console()`)
- Error handling (structured error dicts): Task 1

**Placeholder scan:** None found.

**Type consistency:** `handle_research`, `handle_search_papers`, `handle_synthesize`, `handle_compare`, `handle_list_runs` — names consistent between `mcp_tools.py` and `mcp_server.py` imports. Return types are all `dict`. Context parameter is `ctx: Context | None = None` throughout.
