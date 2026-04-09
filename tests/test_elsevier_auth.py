"""Tests for Elsevier API key resolution and borrowing notice."""
from __future__ import annotations

import pytest


def test_resolve_flag_overrides_everything(monkeypatch):
    from deep_researcher.elsevier_auth import resolve_elsevier_key
    monkeypatch.setenv("ELSEVIER_API_KEY", "env-key")
    key, is_bundled = resolve_elsevier_key(
        flag_key="flag-key", config_key="cfg-key"
    )
    assert key == "flag-key"
    assert is_bundled is False


def test_resolve_env_overrides_config_and_bundled(monkeypatch):
    from deep_researcher.elsevier_auth import resolve_elsevier_key
    monkeypatch.setenv("ELSEVIER_API_KEY", "env-key")
    key, is_bundled = resolve_elsevier_key(flag_key=None, config_key="cfg-key")
    assert key == "env-key"
    assert is_bundled is False


def test_resolve_config_overrides_bundled(monkeypatch):
    from deep_researcher.elsevier_auth import resolve_elsevier_key
    monkeypatch.delenv("ELSEVIER_API_KEY", raising=False)
    key, is_bundled = resolve_elsevier_key(flag_key=None, config_key="cfg-key")
    assert key == "cfg-key"
    assert is_bundled is False


def test_resolve_falls_back_to_bundled(monkeypatch):
    from deep_researcher.elsevier_auth import resolve_elsevier_key
    from deep_researcher.tools.scopus import _BUNDLED_ELSEVIER_KEY
    monkeypatch.delenv("ELSEVIER_API_KEY", raising=False)
    key, is_bundled = resolve_elsevier_key(flag_key=None, config_key=None)
    assert key == _BUNDLED_ELSEVIER_KEY
    assert is_bundled is True


def test_resolve_empty_strings_treated_as_unset(monkeypatch):
    from deep_researcher.elsevier_auth import resolve_elsevier_key
    from deep_researcher.tools.scopus import _BUNDLED_ELSEVIER_KEY
    monkeypatch.setenv("ELSEVIER_API_KEY", "")
    key, is_bundled = resolve_elsevier_key(flag_key="", config_key="")
    assert key == _BUNDLED_ELSEVIER_KEY
    assert is_bundled is True


def test_borrowing_notice_prints_once(capsys):
    from deep_researcher.elsevier_auth import (
        print_borrowing_notice_once,
        _reset_borrowing_notice_state,
    )
    _reset_borrowing_notice_state()
    from rich.console import Console
    console = Console(force_terminal=False, no_color=True)
    print_borrowing_notice_once(console)
    print_borrowing_notice_once(console)
    print_borrowing_notice_once(console)
    out = capsys.readouterr().out
    assert out.count("Borrowing") == 1


def test_borrowing_notice_does_not_print_when_not_bundled(capsys):
    from deep_researcher.elsevier_auth import (
        print_borrowing_notice_once,
        _reset_borrowing_notice_state,
    )
    _reset_borrowing_notice_state()
    out = capsys.readouterr().out
    assert "Borrowing" not in out
