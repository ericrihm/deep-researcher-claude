"""Tests for the ExecutiveSummaryTool and its prompt."""
from __future__ import annotations


def test_executive_summary_prompt_has_required_placeholders():
    from deep_researcher.prompts import EXECUTIVE_SUMMARY_PROMPT
    required = ["{query}", "{count}", "{cat_count}", "{category_list}", "{top_n}", "{top_papers}"]
    for ph in required:
        assert ph in EXECUTIVE_SUMMARY_PROMPT, f"missing {ph}"
