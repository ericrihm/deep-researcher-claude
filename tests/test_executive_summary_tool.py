"""Tests for the ExecutiveSummaryTool and its prompt."""
from __future__ import annotations


def test_executive_summary_prompt_has_required_placeholders():
    from deep_researcher.prompts import EXECUTIVE_SUMMARY_PROMPT
    required = ["{query}", "{count}", "{cat_count}", "{category_list}", "{top_n}", "{top_papers}"]
    for ph in required:
        assert ph in EXECUTIVE_SUMMARY_PROMPT, f"missing {ph}"


from unittest.mock import MagicMock

from deep_researcher.models import Paper, ToolResult


def _papers(n: int) -> list[Paper]:
    return [
        Paper(
            title=f"Paper {i}",
            authors=[f"Author {i}"],
            year=2020 + (i % 5),
            citation_count=100 - i,
        )
        for i in range(n)
    ]


def test_executive_summary_tool_formats_prompt_and_calls_llm():
    from deep_researcher.tools.executive_summary import ExecutiveSummaryTool
    llm = MagicMock()
    llm.chat_no_think.return_value = "A concise summary."
    tool = ExecutiveSummaryTool(llm=llm)

    result = tool.execute(
        query="neural cryptanalysis",
        synthesis_papers=_papers(25),
        categories={"A": [0, 1, 2], "B": [3, 4]},
    )

    assert isinstance(result, ToolResult)
    assert result.text == "A concise summary."
    assert llm.chat_no_think.called
    msgs = llm.chat_no_think.call_args[0][0]
    assert any("neural cryptanalysis" in m["content"] for m in msgs)
    assert any("25" in m["content"] for m in msgs)  # count
    assert any("A (3 papers)" in m["content"] for m in msgs)


def test_executive_summary_tool_empty_on_no_papers():
    from deep_researcher.tools.executive_summary import ExecutiveSummaryTool
    llm = MagicMock()
    tool = ExecutiveSummaryTool(llm=llm)
    result = tool.execute(query="q", synthesis_papers=[], categories={})
    assert result.text == ""
    assert not llm.chat_no_think.called


def test_executive_summary_tool_empty_on_llm_failure():
    from deep_researcher.tools.executive_summary import ExecutiveSummaryTool
    llm = MagicMock()
    llm.chat_no_think.side_effect = RuntimeError("boom")
    tool = ExecutiveSummaryTool(llm=llm)
    result = tool.execute(
        query="q",
        synthesis_papers=_papers(3),
        categories={"A": [0, 1, 2]},
    )
    assert result.text == ""


def test_safe_execute_never_raises():
    from deep_researcher.tools.executive_summary import ExecutiveSummaryTool
    llm = MagicMock()
    llm.chat_no_think.side_effect = RuntimeError("boom")
    tool = ExecutiveSummaryTool(llm=llm)
    result = tool.safe_execute(
        query="q",
        synthesis_papers=_papers(3),
        categories={"A": [0, 1, 2]},
    )
    assert result.text == ""
