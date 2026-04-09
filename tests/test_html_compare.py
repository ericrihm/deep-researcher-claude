"""Tests for the side-by-side comparison HTML renderer."""
from __future__ import annotations


def test_build_compare_html_contains_both_providers():
    from deep_researcher.html_compare import build_compare_html
    result = build_compare_html(
        query="test query",
        report_a="### Report A\nContent A",
        report_b="### Report B\nContent B",
        provider_a="claude",
        provider_b="groq",
        comparison_text="Claude was more thorough.",
    )
    assert "claude" in result
    assert "groq" in result
    assert "Content A" in result
    assert "Content B" in result
    assert "Claude was more thorough" in result
    assert "test query" in result


def test_build_compare_html_is_self_contained():
    from deep_researcher.html_compare import build_compare_html
    result = build_compare_html("q", "a", "b", "x", "y", "comp")
    assert "<style>" in result
    assert "<script>" in result
    assert "<!DOCTYPE html>" in result


def test_build_compare_html_has_synced_scroll():
    from deep_researcher.html_compare import build_compare_html
    result = build_compare_html("q", "a", "b", "x", "y", "comp")
    assert "col-a" in result
    assert "col-b" in result
    assert "sync" in result


def test_md_to_html_simple_headings():
    from deep_researcher.html_compare import _md_to_html_simple
    result = _md_to_html_simple("## Hello\nWorld")
    assert "<h2>" in result
    assert "Hello" in result
    assert "<p>" in result


def test_md_to_html_simple_bold_italic():
    from deep_researcher.html_compare import _md_to_html_simple
    result = _md_to_html_simple("This is **bold** and *italic*.")
    assert "<strong>bold</strong>" in result
    assert "<em>italic</em>" in result


def test_md_to_html_simple_empty():
    from deep_researcher.html_compare import _md_to_html_simple
    assert _md_to_html_simple("") == ""


def test_md_to_html_simple_table():
    from deep_researcher.html_compare import _md_to_html_simple
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = _md_to_html_simple(md)
    assert "<table>" in result
    assert "</table>" in result
