"""Tests for ComparisonTool."""
from __future__ import annotations

from unittest.mock import MagicMock

from deep_researcher.models import ToolResult


def test_comparison_tool_calls_llm_with_both_reports():
    from deep_researcher.tools.comparison import ComparisonTool
    llm = MagicMock()
    llm.chat_no_think.return_value = "Model A is better because..."
    tool = ComparisonTool(llm=llm)

    result = tool.execute(
        query="neural nets",
        report_a="Report A content",
        report_b="Report B content",
        provider_a="claude",
        provider_b="groq",
        paper_count=42,
    )

    assert isinstance(result, ToolResult)
    assert result.text == "Model A is better because..."
    assert llm.chat_no_think.called
    msgs = llm.chat_no_think.call_args[0][0]
    assert any("neural nets" in m["content"] for m in msgs)
    assert any("claude" in m["content"] for m in msgs)
    assert any("groq" in m["content"] for m in msgs)


def test_comparison_tool_empty_on_missing_report():
    from deep_researcher.tools.comparison import ComparisonTool
    llm = MagicMock()
    tool = ComparisonTool(llm=llm)
    result = tool.execute(query="q", report_a="", report_b="some text")
    assert result.text == ""
    assert not llm.chat_no_think.called


def test_comparison_tool_empty_on_llm_failure():
    from deep_researcher.tools.comparison import ComparisonTool
    llm = MagicMock()
    llm.chat_no_think.side_effect = RuntimeError("boom")
    tool = ComparisonTool(llm=llm)
    result = tool.execute(
        query="q", report_a="A", report_b="B",
        provider_a="x", provider_b="y", paper_count=1,
    )
    assert result.text == ""


def test_comparison_tool_truncates_long_reports():
    from deep_researcher.tools.comparison import ComparisonTool
    llm = MagicMock()
    llm.chat_no_think.return_value = "comparison"
    tool = ComparisonTool(llm=llm)
    long_report = "x" * 10000
    result = tool.execute(
        query="q", report_a=long_report, report_b=long_report,
        provider_a="a", provider_b="b", paper_count=1,
    )
    assert result.text == "comparison"
    msgs = llm.chat_no_think.call_args[0][0]
    system_msg = [m for m in msgs if m["role"] == "system"][0]["content"]
    assert len(system_msg) < 12000


def test_safe_execute_never_raises():
    from deep_researcher.tools.comparison import ComparisonTool
    llm = MagicMock()
    llm.chat_no_think.side_effect = RuntimeError("boom")
    tool = ComparisonTool(llm=llm)
    result = tool.safe_execute(
        query="q", report_a="A", report_b="B",
        provider_a="x", provider_b="y", paper_count=1,
    )
    assert result.text == ""
