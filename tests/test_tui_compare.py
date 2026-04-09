"""Tests for the TUI compare submenu."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_compare_submenu_picks_two_providers():
    from deep_researcher.tui import _compare_submenu
    console = MagicMock()
    providers = {"ollama": {}, "groq": {}, "openai": {}}
    with patch("deep_researcher.tui.Prompt") as mock_prompt:
        mock_prompt.ask.return_value = "2 3"
        result = _compare_submenu(console, providers, "ollama")
    assert result == ("groq", "openai")


def test_compare_submenu_back():
    from deep_researcher.tui import _compare_submenu
    console = MagicMock()
    providers = {"ollama": {}, "groq": {}}
    with patch("deep_researcher.tui.Prompt") as mock_prompt:
        mock_prompt.ask.return_value = "b"
        result = _compare_submenu(console, providers, "ollama")
    assert result is None


def test_compare_submenu_rejects_same_provider():
    from deep_researcher.tui import _compare_submenu
    console = MagicMock()
    providers = {"ollama": {}, "groq": {}}
    with patch("deep_researcher.tui.Prompt") as mock_prompt:
        mock_prompt.ask.return_value = "1 1"
        result = _compare_submenu(console, providers, "ollama")
    assert result is None
