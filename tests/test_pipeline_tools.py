"""Tests for pipeline tools (search, enrichment, categorize, synthesize, cross-analysis)."""
from __future__ import annotations

import threading
from unittest.mock import patch

from deep_researcher.models import Paper, ToolResult


class TestScholarSearchTool:
    def _make_tool(self):
        from deep_researcher.tools.scholar_search import ScholarSearchTool
        return ScholarSearchTool()

    def test_returns_tool_result_with_papers(self):
        tool = self._make_tool()
        mock_results = [
            {"bib": {"title": "Paper A", "author": ["Alice"], "pub_year": "2023", "abstract": "Abstract A", "venue": "Nature"}, "num_citations": 50, "pub_url": "http://a.com"},
            {"bib": {"title": "Paper B", "author": "Bob and Carol", "pub_year": "2024", "abstract": "Abstract B", "venue": "Science"}, "num_citations": 30, "pub_url": "http://b.com"},
        ]
        with patch("deep_researcher.tools.scholar_search.scholarly") as mock_scholarly:
            mock_scholarly.search_pubs.return_value = iter(mock_results)
            result = tool.execute(query="test query", max_results=10)
        assert isinstance(result, ToolResult)
        assert len(result.papers) == 2
        assert result.papers[0].title == "Paper A"
        assert result.papers[1].authors == ["Bob", "Carol"]

    def test_deduplicates_by_title(self):
        tool = self._make_tool()
        mock_results = [
            {"bib": {"title": "Same Paper", "author": ["A"], "pub_year": "2023"}, "num_citations": 10},
            {"bib": {"title": "Same Paper", "author": ["B"], "pub_year": "2023"}, "num_citations": 5},
        ]
        with patch("deep_researcher.tools.scholar_search.scholarly") as mock_scholarly:
            mock_scholarly.search_pubs.return_value = iter(mock_results)
            result = tool.execute(query="test", max_results=10)
        assert len(result.papers) == 1

    def test_handles_search_failure(self):
        tool = self._make_tool()
        with patch("deep_researcher.tools.scholar_search.scholarly") as mock_scholarly:
            mock_scholarly.search_pubs.side_effect = Exception("Network error")
            result = tool.execute(query="test", max_results=10)
        assert len(result.papers) == 0
        assert "0 papers" in result.text

    def test_respects_max_results(self):
        tool = self._make_tool()
        mock_results = [
            {"bib": {"title": f"Paper {i}", "author": [f"Author {i}"], "pub_year": "2023"}, "num_citations": i}
            for i in range(20)
        ]
        with patch("deep_researcher.tools.scholar_search.scholarly") as mock_scholarly:
            mock_scholarly.search_pubs.return_value = iter(mock_results)
            result = tool.execute(query="test", max_results=5)
        assert len(result.papers) == 5

    def test_respects_cancel_event(self):
        tool = self._make_tool()
        cancel = threading.Event()
        cancel.set()  # pre-cancelled
        mock_results = [
            {"bib": {"title": f"Paper {i}", "author": [f"A{i}"], "pub_year": "2023"}, "num_citations": i}
            for i in range(10)
        ]
        with patch("deep_researcher.tools.scholar_search.scholarly") as mock_scholarly:
            mock_scholarly.search_pubs.return_value = iter(mock_results)
            result = tool.execute(query="test", max_results=10, cancel=cancel)
        assert len(result.papers) == 0

    def test_is_read_only(self):
        tool = self._make_tool()
        assert tool.is_read_only is True
