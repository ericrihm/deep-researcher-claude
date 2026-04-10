"""Tests for Scopus (Elsevier) integration in the orchestrator search phase."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

from deep_researcher.config import Config
from deep_researcher.models import Paper, PipelineState, ToolResult
from deep_researcher.profiles import get_profile


def _orch():
    from deep_researcher.orchestrator import Orchestrator
    o = Orchestrator.__new__(Orchestrator)
    o.config = Config(
        model="m", base_url="x", api_key="x", output_dir="output",
    )
    o.console = MagicMock()
    o._cancel = threading.Event()
    o._output_folder = ""
    o.last_report_paths = {}
    o._profile = get_profile("default")
    return o


def test_run_search_merges_scholar_and_scopus_papers():
    o = _orch()
    scholar_papers = [
        Paper(title="Alpha", year=2023, source="scholar"),
        Paper(title="Beta",  year=2024, source="scholar"),
    ]
    scopus_papers = [
        Paper(title="Beta",  year=2024, source="scopus"),   # duplicate by title
        Paper(title="Gamma", year=2022, source="scopus"),
    ]
    mock_scholar = MagicMock()
    mock_scholar.name = "scholar_search"
    mock_scholar.safe_execute.return_value = ToolResult(text="", papers=scholar_papers)
    mock_scopus = MagicMock()
    mock_scopus.name = "search_scopus"
    mock_scopus.safe_execute.return_value = ToolResult(text="", papers=scopus_papers)
    o._search_tools = [mock_scholar, mock_scopus]

    state = PipelineState(query="test")
    result = o._run_search(state)

    assert len(result.papers) == 3
    titles = {p.title for p in result.papers.values()}
    assert titles == {"Alpha", "Beta", "Gamma"}


def test_run_search_skips_scopus_when_no_elsevier():
    o = _orch()
    o.config.no_elsevier = True
    scholar_papers = [Paper(title="Alpha", year=2023, source="scholar")]
    mock_scholar = MagicMock()
    mock_scholar.name = "scholar_search"
    mock_scholar.safe_execute.return_value = ToolResult(text="", papers=scholar_papers)
    # Only scholar — no scopus in the list (profile respects no_elsevier at build time)
    o._search_tools = [mock_scholar]

    state = PipelineState(query="test")
    result = o._run_search(state)

    assert len(result.papers) == 1


def test_run_search_tolerates_scopus_failure():
    """If Scopus returns empty (e.g. invalid key), Scholar results still come through."""
    o = _orch()
    scholar_papers = [Paper(title="Alpha", year=2023, source="scholar")]
    mock_scholar = MagicMock()
    mock_scholar.name = "scholar_search"
    mock_scholar.safe_execute.return_value = ToolResult(text="", papers=scholar_papers)
    mock_scopus = MagicMock()
    mock_scopus.name = "search_scopus"
    mock_scopus.safe_execute.return_value = ToolResult(
        text="Scopus API key is invalid.", papers=[]
    )
    o._search_tools = [mock_scholar, mock_scopus]

    state = PipelineState(query="test")
    result = o._run_search(state)

    assert len(result.papers) == 1
    assert next(iter(result.papers.values())).title == "Alpha"
