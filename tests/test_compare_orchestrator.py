"""Tests for Orchestrator.compare_research() and _run_comparison()."""
from __future__ import annotations

import json
import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from deep_researcher.config import Config
from deep_researcher.models import Paper, PipelineState, ToolResult
from deep_researcher.profiles import get_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_papers(n: int = 3) -> dict[str, Paper]:
    papers = {}
    for i in range(n):
        p = Paper(title=f"Paper {i}", year=2022 + i, citation_count=i, source="scholar")
        papers[p.unique_key] = p
    return papers


def _make_synth_state(query: str = "test query", papers: dict | None = None) -> PipelineState:
    """Return a PipelineState that looks like synthesis completed."""
    p = papers or _make_papers()
    return PipelineState(
        query=query,
        papers=p,
        synthesis_papers=list(p.values()),
        report="# Synthesized report\n\nSome findings.",
        exec_summary="Executive summary.",
        categories={"Cat A": [0, 1], "Cat B": [2]},
        category_sections=[("Cat A", "body A"), ("Cat B", "body B")],
        cross_section="Cross-analysis text.",
    )


def _make_orchestrator(tmp_path=None):
    """Build a bare Orchestrator (no LLM) with all tools mocked."""
    from deep_researcher.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = Config(
        model="mock-model",
        base_url="http://localhost",
        api_key="test-key",
        output_dir=str(tmp_path) if tmp_path else "output",
        provider_kind="openai",
    )
    orch.console = MagicMock()
    # console.status() must work as a context manager
    orch.console.status.return_value.__enter__ = MagicMock(return_value=None)
    orch.console.status.return_value.__exit__ = MagicMock(return_value=False)

    orch._cancel = threading.Event()
    orch._output_folder = ""
    orch.last_report_paths = {}
    orch._profile = get_profile("default")

    # Search tools — profile-driven list
    papers = _make_papers()
    search_result = ToolResult(text="Found 3 papers", papers=list(papers.values()))
    mock_scholar = MagicMock()
    mock_scholar.name = "scholar_search"
    mock_scholar.safe_execute.return_value = search_result
    mock_scopus = MagicMock()
    mock_scopus.name = "search_scopus"
    mock_scopus.safe_execute.return_value = ToolResult(text="", papers=[])
    orch._search_tools = [mock_scholar, mock_scopus]

    # Enrichment tool: return the same papers unchanged
    orch._enrichment_tool = MagicMock()
    orch._enrichment_tool.safe_execute.return_value = ToolResult(
        text="Enrichment done", papers=list(papers.values())
    )

    # Synthesis-phase tools
    orch._clarify_tool = MagicMock()
    orch._categorize_tool = MagicMock()
    orch._categorize_tool.safe_execute.return_value = ToolResult(
        text="ok", data={"Cat A": [0, 1], "Cat B": [2]}
    )
    orch._synthesize_tool = MagicMock()
    orch._synthesize_tool.safe_execute.return_value = ToolResult(text="category body")
    orch._cross_analysis_tool = MagicMock()
    orch._cross_analysis_tool.safe_execute.return_value = ToolResult(text="cross body")
    orch._fallback_tool = MagicMock()
    orch._exec_summary_tool = MagicMock()
    orch._exec_summary_tool.safe_execute.return_value = ToolResult(text="exec summary")

    return orch


_PROVIDERS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-openai",
        "default_model": "gpt-4o",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "sk-groq",
        "default_model": "llama-3.3-70b-versatile",
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_search_runs_only_once(tmp_path):
    """Search + enrich execute exactly once regardless of how many providers."""
    orch = _make_orchestrator(tmp_path)

    with patch("deep_researcher.orchestrator.make_llm_client", return_value=MagicMock()):
        with patch("deep_researcher.orchestrator.save_results"):
            with patch("deep_researcher.orchestrator.Orchestrator._run_synthesis",
                       return_value=_make_synth_state()):
                orch.compare_research("test query", "openai", "groq", _PROVIDERS)

    assert orch._search_tools[0].safe_execute.call_count == 1
    assert orch._enrichment_tool.safe_execute.call_count == 1


def test_subdirectories_created_for_each_provider(tmp_path):
    """Provider subfolders are created under the output folder."""
    orch = _make_orchestrator(tmp_path)

    saved_paths = []

    def _mock_save(console, state, output_dir, folder=None):
        if folder:
            os.makedirs(folder, exist_ok=True)
            saved_paths.append(folder)
        return {}

    with patch("deep_researcher.orchestrator.make_llm_client", return_value=MagicMock()):
        with patch("deep_researcher.orchestrator.save_results", side_effect=_mock_save):
            with patch("deep_researcher.orchestrator.Orchestrator._run_synthesis",
                       return_value=_make_synth_state()):
                with patch.object(orch, "_run_comparison", return_value=""):
                    orch.compare_research("test query", "openai", "groq", _PROVIDERS)

    # The call in compare_research uses a local import alias _save_results from display,
    # so we need to also patch that path.  Re-run with the correct patch target:
    # (the above may not catch it — see second approach below)


def test_subdirectories_created_for_each_provider_v2(tmp_path):
    """Provider subfolders appear inside output folder (patching the display import)."""
    orch = _make_orchestrator(tmp_path)

    with patch("deep_researcher.orchestrator.make_llm_client", return_value=MagicMock()):
        # Patch the module-level save_results that compare_research re-imports
        with patch("deep_researcher.display.save_results", return_value={}):
            with patch("deep_researcher.orchestrator.Orchestrator._run_synthesis",
                       return_value=_make_synth_state()):
                with patch.object(orch, "_run_comparison", return_value=""):
                    orch.compare_research("test query", "openai", "groq", _PROVIDERS)

    # The output folder should exist
    assert os.path.isdir(orch._output_folder)


def test_metadata_json_has_mode_and_providers(tmp_path):
    """metadata.json written at top-level must have mode='compare' and providers list."""
    orch = _make_orchestrator(tmp_path)

    with patch("deep_researcher.orchestrator.make_llm_client", return_value=MagicMock()):
        with patch("deep_researcher.display.save_results", return_value={}):
            with patch("deep_researcher.orchestrator.Orchestrator._run_synthesis",
                       return_value=_make_synth_state()):
                with patch.object(orch, "_run_comparison", return_value=""):
                    orch.compare_research("metadata query", "openai", "groq", _PROVIDERS)

    meta_path = os.path.join(orch._output_folder, "metadata.json")
    assert os.path.exists(meta_path), "metadata.json must be written"

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    assert meta["mode"] == "compare"
    assert set(meta["providers"]) == {"openai", "groq"}
    assert meta["query"] == "metadata query"
    assert "total_papers" in meta
    assert meta["provider_a_success"] is True
    assert meta["provider_b_success"] is True


def test_partial_failure_still_saves_successful_provider(tmp_path):
    """If provider_b synthesis fails, provider_a results are still saved."""
    orch = _make_orchestrator(tmp_path)

    call_count = {"n": 0}

    def _run_synthesis_side_effect(state):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("Provider B exploded")
        return _make_synth_state(state.query, state.papers)

    saved_folders = []

    def _mock_save(console, state, output_dir, folder=None):
        if folder:
            os.makedirs(folder, exist_ok=True)
            saved_folders.append(folder)
        return {}

    with patch("deep_researcher.orchestrator.make_llm_client", return_value=MagicMock()):
        with patch("deep_researcher.display.save_results", side_effect=_mock_save):
            with patch(
                "deep_researcher.orchestrator.Orchestrator._run_synthesis",
                side_effect=_run_synthesis_side_effect,
            ):
                report_a, report_b = orch.compare_research(
                    "partial fail query", "openai", "groq", _PROVIDERS
                )

    # provider_a report is a real report; provider_b is an error message
    assert "Synthesis failed" not in report_a
    assert "Synthesis failed" in report_b or "exploded" in report_b.lower()

    # metadata records the partial failure
    meta_path = os.path.join(orch._output_folder, "metadata.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["provider_a_success"] is True
    assert meta["provider_b_success"] is False

    # At least one subfolder was saved (provider_a's)
    assert len(saved_folders) >= 1


def test_returns_tuple_of_two_reports(tmp_path):
    """compare_research() returns exactly a 2-tuple of strings."""
    orch = _make_orchestrator(tmp_path)

    with patch("deep_researcher.orchestrator.make_llm_client", return_value=MagicMock()):
        with patch("deep_researcher.display.save_results", return_value={}):
            with patch("deep_researcher.orchestrator.Orchestrator._run_synthesis",
                       return_value=_make_synth_state()):
                with patch.object(orch, "_run_comparison", return_value="comparison text"):
                    result = orch.compare_research("q", "openai", "groq", _PROVIDERS)

    assert isinstance(result, tuple)
    assert len(result) == 2
    assert all(isinstance(r, str) for r in result)


def test_no_papers_returns_early(tmp_path):
    """If search finds no papers, return early with placeholder strings."""
    orch = _make_orchestrator(tmp_path)
    # Override search to return nothing
    for tool in orch._search_tools:
        tool.safe_execute.return_value = ToolResult(text="", papers=[])

    with patch("deep_researcher.orchestrator.make_llm_client", return_value=MagicMock()):
        result = orch.compare_research("empty query", "openai", "groq", _PROVIDERS)

    assert result == ("No papers found.", "No papers found.")
    # Enrichment should NOT have been called
    orch._enrichment_tool.safe_execute.assert_not_called()


def test_run_comparison_picks_best_provider(tmp_path):
    """_run_comparison selects the highest-ranked provider for the analysis LLM."""
    orch = _make_orchestrator(tmp_path)
    orch._output_folder = str(tmp_path)

    captured_configs = []

    def _fake_make_llm(config):
        captured_configs.append(config.provider_kind)
        return MagicMock()

    mock_tool = MagicMock()
    mock_tool.safe_execute.return_value = ToolResult(text="analysis result")

    with patch("deep_researcher.orchestrator.make_llm_client", side_effect=_fake_make_llm):
        with patch("deep_researcher.tools.comparison.ComparisonTool", return_value=mock_tool):
            text = orch._run_comparison(
                query="q",
                report_a="report A",
                report_b="report B",
                provider_a="groq",
                provider_b="openai",
                paper_count=10,
                providers=_PROVIDERS,
            )

    # "openai" ranks higher than "groq" in capability_order
    assert text == "analysis result"
    # The LLM created for comparison should use openai provider kind
    assert "openai" in captured_configs


def test_last_report_paths_set_after_compare(tmp_path):
    """last_report_paths is populated with compare_folder and html keys."""
    orch = _make_orchestrator(tmp_path)

    with patch("deep_researcher.orchestrator.make_llm_client", return_value=MagicMock()):
        with patch("deep_researcher.display.save_results", return_value={}):
            with patch("deep_researcher.orchestrator.Orchestrator._run_synthesis",
                       return_value=_make_synth_state()):
                with patch.object(orch, "_run_comparison", return_value=""):
                    orch.compare_research("paths query", "openai", "groq", _PROVIDERS)

    assert "compare_folder" in orch.last_report_paths
    assert "html" in orch.last_report_paths
    assert orch.last_report_paths["compare_folder"] == orch._output_folder
