"""Tests for ChatGPT OAuth four-tier auth flow."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _write_auth_file(path: Path, *, access="tok", refresh="ref", expires_in=3600):
    payload = {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": int(time.time()) + expires_in,
        "client_id": "client-xyz",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return payload


def test_chatgpt_auth_dataclass_holds_token():
    from deep_researcher.auth_chatgpt import ChatGPTAuth
    a = ChatGPTAuth(
        access_token="tok",
        refresh_token="ref",
        expires_at=int(time.time()) + 3600,
        source_file=Path("/tmp/x.json"),
    )
    assert a.access_token == "tok"
    assert a.is_expired(skew=60) is False


def test_chatgpt_auth_is_expired_when_past_expiry():
    from deep_researcher.auth_chatgpt import ChatGPTAuth
    a = ChatGPTAuth(
        access_token="tok",
        refresh_token="ref",
        expires_at=int(time.time()) - 10,
        source_file=Path("/tmp/x.json"),
    )
    assert a.is_expired(skew=60) is True


def test_tier1_reads_codex_auth_file(tmp_path, monkeypatch):
    from deep_researcher.auth_chatgpt import _try_codex_files
    f = tmp_path / ".codex" / "auth.json"
    _write_auth_file(f)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CHATGPT_LOCAL_HOME", raising=False)

    auth = _try_codex_files()
    assert auth is not None
    assert auth.access_token == "tok"
    assert auth.source_file == f


def test_tier1_returns_none_when_no_codex_files(tmp_path, monkeypatch):
    from deep_researcher.auth_chatgpt import _try_codex_files
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CHATGPT_LOCAL_HOME", raising=False)
    assert _try_codex_files() is None


def test_tier1_skips_malformed_codex_file(tmp_path, monkeypatch):
    from deep_researcher.auth_chatgpt import _try_codex_files
    f = tmp_path / ".codex" / "auth.json"
    f.parent.mkdir(parents=True)
    f.write_text("not json {{{")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CHATGPT_LOCAL_HOME", raising=False)
    assert _try_codex_files() is None


def test_chatgpt_auth_error_is_distinct_exception():
    from deep_researcher.auth_chatgpt import ChatGPTAuthError
    with pytest.raises(ChatGPTAuthError):
        raise ChatGPTAuthError("nope")
