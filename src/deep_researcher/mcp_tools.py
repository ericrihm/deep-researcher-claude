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
