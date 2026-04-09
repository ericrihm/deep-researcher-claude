"""Tests for the COMPARISON_PROMPT template."""


def test_comparison_prompt_has_required_placeholders():
    from deep_researcher.prompts import COMPARISON_PROMPT
    required = ["{query}", "{paper_count}", "{provider_a}", "{report_a}",
                "{provider_b}", "{report_b}"]
    for ph in required:
        assert ph in COMPARISON_PROMPT, f"missing {ph}"
