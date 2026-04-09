"""Tests for compare folder replay."""
from __future__ import annotations

import json
import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from deep_researcher.config import Config
from deep_researcher.models import Paper, ToolResult


def _make_orchestrator():
    from deep_researcher.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.config = Config(model="m", base_url="http://x", api_key="k", output_dir="output")
    orch.console = MagicMock()
    orch._cancel = threading.Event()
    orch._output_folder = ""
    orch.last_report_paths = {}

    papers = [Paper(title=f"P{i}", citation_count=10 - i, year=2023) for i in range(3)]
    orch._search_tool = MagicMock()
    orch._search_tool.safe_execute.return_value = ToolResult(text="ok", papers=papers)
    orch._scopus_tool = MagicMock()
    orch._scopus_tool.safe_execute.return_value = ToolResult(text="", papers=[])
    orch._enrichment_tool = MagicMock()
    orch._enrichment_tool.safe_execute.return_value = ToolResult(text="enriched", papers=papers)
    orch._categorize_tool = MagicMock()
    orch._categorize_tool.safe_execute.return_value = ToolResult(text="ok", data={"Cat": [0, 1, 2]})
    orch._synthesize_tool = MagicMock()
    orch._synthesize_tool.safe_execute.return_value = ToolResult(text="synth")
    orch._cross_analysis_tool = MagicMock()
    orch._cross_analysis_tool.safe_execute.return_value = ToolResult(text="cross")
    orch._fallback_tool = MagicMock()
    orch._exec_summary_tool = MagicMock()
    orch._exec_summary_tool.safe_execute.return_value = ToolResult(text="summary")
    orch._clarify_tool = MagicMock()
    return orch


def test_compare_replay_raises_on_non_compare_folder(tmp_path):
    orch = _make_orchestrator()
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({"mode": "normal", "query": "q"}))
    (folder / "papers.json").write_text(json.dumps([]))
    with pytest.raises(ValueError, match="Not a compare folder"):
        orch.compare_replay(str(folder), {})


def test_compare_replay_raises_on_missing_metadata(tmp_path):
    orch = _make_orchestrator()
    folder = tmp_path / "run"
    folder.mkdir()
    with pytest.raises(FileNotFoundError):
        orch.compare_replay(str(folder), {})


def test_compare_replay_loads_papers_and_delegates(tmp_path):
    """compare_replay loads papers.json and calls compare_research with preloaded_papers."""
    orch = _make_orchestrator()
    folder = tmp_path / "run"
    folder.mkdir()

    papers = [Paper(title=f"P{i}", year=2023).to_dict() for i in range(3)]
    (folder / "papers.json").write_text(json.dumps(papers))
    (folder / "metadata.json").write_text(json.dumps({
        "mode": "compare",
        "providers": ["groq", "openai"],
        "query": "test query",
    }))

    providers = {
        "groq": {"base_url": "http://groq", "api_key": "k", "default_model": "qwen"},
        "openai": {"base_url": "http://openai", "api_key": "k2", "default_model": "gpt"},
    }

    with patch("deep_researcher.orchestrator.make_llm_client") as mock_make:
        mock_llm = MagicMock()
        mock_llm.chat_no_think.return_value = "synth text"
        mock_make.return_value = mock_llm
        report_a, report_b = orch.compare_replay(str(folder), providers)

    # Search should NOT have been called (replay skips it)
    assert orch._search_tool.safe_execute.call_count == 0
    assert orch._enrichment_tool.safe_execute.call_count == 0
    # Both reports should have content
    assert report_a
    assert report_b
