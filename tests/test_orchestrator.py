"""Tests for the research pipeline orchestrator."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

from deep_researcher.config import Config
from deep_researcher.models import Paper, PipelineState, ToolResult
from deep_researcher.profiles import get_profile


class TestOrchestrator:
    def _make_orchestrator(self):
        from deep_researcher.orchestrator import Orchestrator
        config = Config(
            model="test-model",
            base_url="http://localhost:11434/v1",
            api_key="test",
        )
        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config
        orch.console = MagicMock()
        orch._cancel = threading.Event()
        orch._output_folder = ""
        orch._profile = get_profile("default")

        # Mock search tools as a list (profile-driven)
        mock_scholar = MagicMock()
        mock_scholar.name = "scholar_search"
        mock_scholar.safe_execute.return_value = ToolResult(text="", papers=[])
        mock_scopus = MagicMock()
        mock_scopus.name = "search_scopus"
        mock_scopus.safe_execute.return_value = ToolResult(text="", papers=[])
        orch._search_tools = [mock_scholar, mock_scopus]

        # Mock other tools
        orch._enrichment_tool = MagicMock()
        orch._categorize_tool = MagicMock()
        orch._synthesize_tool = MagicMock()
        orch._cross_analysis_tool = MagicMock()
        orch.llm = MagicMock()
        return orch

    def test_search_phase_returns_papers(self):
        orch = self._make_orchestrator()
        papers = [Paper(title="Paper A"), Paper(title="Paper B")]
        orch._search_tools[0].safe_execute.return_value = ToolResult(text="Found 2", papers=papers)
        state = PipelineState(query="test")
        new_state = orch._run_search(state)
        assert len(new_state.papers) == 2

    def test_enrich_phase_returns_enriched_papers(self):
        orch = self._make_orchestrator()
        papers = {"k1": Paper(title="Paper A"), "k2": Paper(title="Paper B")}
        enriched = [Paper(title="Paper A", doi="10.1/a"), Paper(title="Paper B", doi="10.1/b")]
        orch._enrichment_tool.safe_execute.return_value = ToolResult(text="Enriched", papers=enriched)
        state = PipelineState(query="test", papers=papers)
        new_state = orch._run_enrichment(state)
        assert all(p.doi for p in new_state.papers.values())

    def test_search_failure_returns_empty_state(self):
        orch = self._make_orchestrator()
        # Both tools return empty
        for tool in orch._search_tools:
            tool.safe_execute.return_value = ToolResult(text="Found 0", papers=[])
        state = PipelineState(query="test")
        new_state = orch._run_search(state)
        assert len(new_state.papers) == 0

    def test_synthesis_fallback_on_categorization_failure(self):
        orch = self._make_orchestrator()
        orch._categorize_tool.safe_execute.return_value = ToolResult(text="Failed", data=None)
        orch._fallback_tool = MagicMock()
        orch._fallback_tool.safe_execute.return_value = ToolResult(text="Fallback synthesis content")
        papers = [Paper(title=f"P{i}", citation_count=10 - i) for i in range(5)]
        state = PipelineState(
            query="test",
            papers={p.unique_key: p for p in papers},
            synthesis_papers=papers,
        )
        report = orch._run_synthesis(state)
        assert report.report  # should have fallback content

    def test_state_immutability(self):
        orch = self._make_orchestrator()
        papers = [Paper(title="Paper A")]
        orch._search_tools[0].safe_execute.return_value = ToolResult(text="Found 1", papers=papers)
        state = PipelineState(query="test")
        original_papers = state.papers
        new_state = orch._run_search(state)
        assert state.papers is original_papers  # original unchanged
        assert len(new_state.papers) == 1

    def test_init_propagates_year_range_to_search_tools(self):
        from deep_researcher.orchestrator import Orchestrator
        config = Config(
            model="test-model",
            base_url="http://localhost:11434/v1",
            api_key="test",
            start_year=2020,
            end_year=2025,
        )
        orch = Orchestrator(config)
        for tool in orch._search_tools:
            assert tool._start_year == 2020
            assert tool._end_year == 2025

    def test_assemble_report_format(self):
        from deep_researcher.orchestrator import _assemble_report
        from deep_researcher.tools.scholar_search import ScholarSearchTool
        papers = [
            Paper(title="Paper A", authors=["Alice"], year=2023, doi="10.1/a"),
            Paper(title="Paper B", authors=["Bob", "Carol"], year=2024),
        ]
        state = PipelineState(
            query="test query",
            papers={p.unique_key: p for p in papers},
            synthesis_papers=papers,
            categories={"Group A": [0, 1]},
            category_sections=[("Group A", "Section content here")],
            cross_section="Cross patterns here",
        )
        profile = get_profile("default")
        search_tools = [ScholarSearchTool()]
        report = _assemble_report(state, profile, search_tools)
        assert "### test query" in report
        assert "#### Coverage" in report
        assert "##### Group A" in report
        assert "Section content here" in report
        assert "Cross patterns here" in report
        assert "#### References" in report
        assert "[1] Alice (2023)" in report
        assert "[2] Bob et al. (2024)" in report

    def test_assemble_report_security_profile_note(self):
        from deep_researcher.orchestrator import _assemble_report
        from deep_researcher.tools.dblp import DblpSearchTool
        papers = [Paper(title="Paper A", authors=["Alice"], year=2023)]
        state = PipelineState(
            query="test query",
            papers={p.unique_key: p for p in papers},
            synthesis_papers=papers,
            categories={"Group A": [0]},
            category_sections=[("Group A", "Content")],
            cross_section="Cross",
        )
        profile = get_profile("security")
        report = _assemble_report(state, profile, [DblpSearchTool()])
        assert "Profile: security" in report

    def test_exec_summary_runs_during_synthesis(self):
        """Exec summary tool is called during _run_synthesis and its text
        ends up on state.exec_summary."""
        orch = self._make_orchestrator()
        orch._categorize_tool.safe_execute.return_value = ToolResult(
            text="ok", data={"Cat A": [0, 1]},
        )
        orch._synthesize_tool.safe_execute.return_value = ToolResult(text="cat body")
        orch._cross_analysis_tool.safe_execute.return_value = ToolResult(text="cross body")
        orch._fallback_tool = MagicMock()
        orch._exec_summary_tool = MagicMock()
        orch._exec_summary_tool.safe_execute.return_value = ToolResult(text="my summary")

        papers = [Paper(title=f"P{i}", citation_count=10 - i) for i in range(2)]
        state = PipelineState(
            query="q",
            papers={p.unique_key: p for p in papers},
        )
        new_state = orch._run_synthesis(state)
        assert new_state.exec_summary == "my summary"
        assert orch._exec_summary_tool.safe_execute.called

    def test_exec_summary_failure_does_not_break_synthesis(self):
        orch = self._make_orchestrator()
        orch._categorize_tool.safe_execute.return_value = ToolResult(
            text="ok", data={"Cat A": [0, 1]},
        )
        orch._synthesize_tool.safe_execute.return_value = ToolResult(text="cat body")
        orch._cross_analysis_tool.safe_execute.return_value = ToolResult(text="cross body")
        orch._fallback_tool = MagicMock()
        orch._exec_summary_tool = MagicMock()
        orch._exec_summary_tool.safe_execute.return_value = ToolResult(text="")

        papers = [Paper(title=f"P{i}", citation_count=10 - i) for i in range(2)]
        state = PipelineState(
            query="q",
            papers={p.unique_key: p for p in papers},
        )
        new_state = orch._run_synthesis(state)
        assert new_state.exec_summary == ""
        assert new_state.report  # report still assembled
