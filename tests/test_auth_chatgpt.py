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


def test_stored_auth_path_is_under_deep_researcher(tmp_path, monkeypatch):
    from deep_researcher.auth_chatgpt import _stored_auth_path
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    p = _stored_auth_path()
    assert p.name == "chatgpt-auth.json"
    assert ".deep-researcher" in str(p)


def test_tier2_reads_stored_token(tmp_path, monkeypatch):
    from deep_researcher.auth_chatgpt import _try_stored_token
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    f = tmp_path / ".deep-researcher" / "chatgpt-auth.json"
    _write_auth_file(f)
    auth = _try_stored_token()
    assert auth is not None and auth.access_token == "tok"


def test_save_auth_file_creates_directory_and_writes_atomically(tmp_path, monkeypatch):
    from deep_researcher.auth_chatgpt import _save_auth_file
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    target = tmp_path / ".deep-researcher" / "chatgpt-auth.json"
    _save_auth_file(target, {
        "access_token": "new",
        "refresh_token": "newref",
        "expires_at": int(time.time()) + 3600,
    })
    assert target.exists()
    assert json.loads(target.read_text())["access_token"] == "new"


def test_ensure_fresh_returns_unchanged_when_token_valid(tmp_path, monkeypatch):
    from deep_researcher.auth_chatgpt import ChatGPTAuth, _ensure_fresh
    f = tmp_path / "auth.json"
    _write_auth_file(f, expires_in=3600)
    auth = ChatGPTAuth(
        access_token="tok",
        refresh_token="ref",
        expires_at=int(time.time()) + 3600,
        source_file=f,
    )
    refreshed = _ensure_fresh(auth)
    assert refreshed.access_token == "tok"


def test_ensure_fresh_calls_refresh_endpoint_when_expired(tmp_path, monkeypatch):
    from deep_researcher.auth_chatgpt import ChatGPTAuth, _ensure_fresh
    f = tmp_path / "auth.json"
    _write_auth_file(f, expires_in=-10)
    auth = ChatGPTAuth(
        access_token="old",
        refresh_token="ref",
        expires_at=int(time.time()) - 10,
        source_file=f,
    )

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "access_token": "newtok",
        "refresh_token": "newref",
        "expires_in": 3600,
    }
    fake_resp.raise_for_status.return_value = None

    with patch("deep_researcher.auth_chatgpt.httpx.post", return_value=fake_resp) as mock_post:
        refreshed = _ensure_fresh(auth)

    assert refreshed.access_token == "newtok"
    assert refreshed.refresh_token == "newref"
    mock_post.assert_called_once()
    saved = json.loads(f.read_text())
    assert saved["access_token"] == "newtok"
