"""Tests for TUI replay helpers."""
from __future__ import annotations

import json
import os
import time


def test_list_recent_runs_empty(tmp_path):
    from deep_researcher.tui import list_recent_runs
    assert list_recent_runs(str(tmp_path)) == []


def test_list_recent_runs_ignores_folders_without_papers(tmp_path):
    from deep_researcher.tui import list_recent_runs
    (tmp_path / "empty-folder").mkdir()
    (tmp_path / "has-papers").mkdir()
    (tmp_path / "has-papers" / "papers.json").write_text(
        json.dumps([{"title": "x"}])
    )
    runs = list_recent_runs(str(tmp_path))
    assert len(runs) == 1
    assert runs[0]["path"].endswith("has-papers")


def test_list_recent_runs_sort_newest_first(tmp_path):
    from deep_researcher.tui import list_recent_runs
    a = tmp_path / "older"
    b = tmp_path / "newer"
    a.mkdir()
    b.mkdir()
    (a / "papers.json").write_text("[]")
    (b / "papers.json").write_text("[]")
    old_time = time.time() - 1000
    os.utime(a, (old_time, old_time))
    runs = list_recent_runs(str(tmp_path))
    assert [r["path"].rsplit(os.sep, 1)[-1] for r in runs] == ["newer", "older"]


def test_list_recent_runs_reads_metadata(tmp_path):
    from deep_researcher.tui import list_recent_runs
    d = tmp_path / "run"
    d.mkdir()
    (d / "papers.json").write_text("[]")
    (d / "metadata.json").write_text(
        json.dumps({"query": "crispr off-targets", "total_papers": 42})
    )
    runs = list_recent_runs(str(tmp_path))
    assert runs[0]["query"] == "crispr off-targets"
    assert runs[0]["paper_count"] == 42


def test_list_recent_runs_fallback_when_no_metadata(tmp_path):
    from deep_researcher.tui import list_recent_runs
    d = tmp_path / "2026-04-08-143000-neural-nets"
    d.mkdir()
    (d / "papers.json").write_text(json.dumps([{"title": "x"}, {"title": "y"}]))
    runs = list_recent_runs(str(tmp_path))
    assert runs[0]["paper_count"] == 2
    assert "neural" in runs[0]["query"].lower()


def test_list_recent_runs_limit(tmp_path):
    from deep_researcher.tui import list_recent_runs
    for i in range(15):
        d = tmp_path / f"run-{i}"
        d.mkdir()
        (d / "papers.json").write_text("[]")
    runs = list_recent_runs(str(tmp_path), limit=5)
    assert len(runs) == 5
