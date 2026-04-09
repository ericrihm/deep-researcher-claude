"""Tests for --compare CLI flag parsing."""
from __future__ import annotations

import subprocess
import sys


def test_compare_requires_query():
    """--compare without a query should fail."""
    result = subprocess.run(
        [sys.executable, "-m", "deep_researcher", "--compare", "groq", "openai"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "requires a query" in result.stderr or "error" in result.stderr.lower()


def test_compare_rejects_unknown_provider():
    """--compare with an unknown provider should fail."""
    result = subprocess.run(
        [sys.executable, "-m", "deep_researcher", "--compare", "groq", "fakeprov", "q"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0


def test_compare_rejects_replay_combination():
    """--compare and --replay are mutually exclusive."""
    result = subprocess.run(
        [sys.executable, "-m", "deep_researcher", "--compare", "groq", "openai",
         "--replay", "somefolder"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
