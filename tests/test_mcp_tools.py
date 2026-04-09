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
