"""Tests for Orchestrator.replay() — re-run synthesis on an existing folder."""
from __future__ import annotations

import json
import os
import threading
from unittest.mock import MagicMock

import pytest

from deep_researcher.config import Config
from deep_researcher.models import Paper, ToolResult


def _make_orchestrator():
    from deep_researcher.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.config = Config(model="m", base_url="x", api_key="x", output_dir="output")
    orch.console = MagicMock()
    orch._cancel = threading.Event()
    orch._output_folder = ""
    orch.last_report_paths = {}

    orch._search_tool = MagicMock()
    orch._enrichment_tool = MagicMock()
    orch._categorize_tool = MagicMock()
    orch._categorize_tool.safe_execute.return_value = ToolResult(
        text="ok", data={"Cat": [0, 1]},
    )
    orch._synthesize_tool = MagicMock()
    orch._synthesize_tool.safe_execute.return_value = ToolResult(text="category body")
    orch._cross_analysis_tool = MagicMock()
    orch._cross_analysis_tool.safe_execute.return_value = ToolResult(text="cross body")
    orch._fallback_tool = MagicMock()
    orch._exec_summary_tool = MagicMock()
    orch._exec_summary_tool.safe_execute.return_value = ToolResult(text="replay summary")
    return orch


def _write_corpus(folder, papers):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "papers.json"), "w", encoding="utf-8") as f:
        json.dump([p.to_dict() for p in papers], f)
    with open(os.path.join(folder, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump({"query": "original query", "total_papers": len(papers)}, f)


def test_replay_missing_folder_raises(tmp_path):
    orch = _make_orchestrator()
    with pytest.raises(FileNotFoundError):
        orch.replay(str(tmp_path / "does-not-exist"))


def test_replay_missing_papers_json_raises(tmp_path):
    orch = _make_orchestrator()
    os.makedirs(tmp_path / "folder")
    with pytest.raises(FileNotFoundError):
        orch.replay(str(tmp_path / "folder"))


def test_replay_malformed_papers_json_raises(tmp_path):
    orch = _make_orchestrator()
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / "papers.json").write_text("not json{")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        orch.replay(str(folder))


def test_replay_runs_synthesis_and_writes_versioned_outputs(tmp_path):
    orch = _make_orchestrator()
    folder = tmp_path / "run1"
    papers = [
        Paper(title="A", year=2023, citation_count=10, source="scholar"),
        Paper(title="B", year=2024, citation_count=5, source="openalex"),
    ]
    _write_corpus(folder, papers)
    (folder / "report.md").write_text("old report")
    (folder / "report.html").write_text("<html>old</html>")

    report = orch.replay(str(folder))

    assert report
    assert (folder / "report-2.md").exists()
    assert (folder / "report-2.html").exists()
    assert (folder / "report.md").read_text() == "old report"
    assert orch.last_report_paths.get("report", "").endswith("report-2.md")


def test_replay_tolerates_missing_metadata(tmp_path, capsys):
    orch = _make_orchestrator()
    folder = tmp_path / "run-no-meta"
    folder.mkdir()
    papers = [Paper(title="A", year=2023, citation_count=10, source="scholar")]
    with open(folder / "papers.json", "w") as f:
        json.dump([p.to_dict() for p in papers], f)

    report = orch.replay(str(folder))
    assert report


def test_replay_ignores_unknown_fields_in_papers_json(tmp_path):
    orch = _make_orchestrator()
    folder = tmp_path / "run-old-schema"
    folder.mkdir()
    with open(folder / "papers.json", "w") as f:
        json.dump([{
            "title": "A",
            "year": 2023,
            "citation_count": 3,
            "source": "scholar",
            "legacy_field_from_future": "ignored",
        }], f)
    with open(folder / "metadata.json", "w") as f:
        json.dump({"query": "q"}, f)

    report = orch.replay(str(folder))
    assert report
