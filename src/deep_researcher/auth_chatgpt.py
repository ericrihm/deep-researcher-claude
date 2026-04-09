"""ChatGPT OAuth four-tier auth resolution.

Tier 1: reuse a Codex CLI / ChatGPT-Local auth.json on disk
Tier 2: reuse our own stored token at ~/.deep-researcher/chatgpt-auth.json
Tier 3: PKCE browser login (writes to Tier 2 location for next time)
Tier 4: fall back to OPENAI_API_KEY against api.openai.com (handled by caller)

This module is provider-agnostic: it returns a ChatGPTAuth that the
LLMClient hooks into for per-call token refresh.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx


# The OAuth client ID used by the upstream Codex CLI / openai-oauth /
# OpenClaw projects. Not officially published by OpenAI as a third-party
# constant, but it is the de facto value the open ecosystem standardized
# on. If it rotates we ship a patch release that updates this constant.
_OPENAI_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

CHATGPT_BACKEND_BASE_URL = "https://chatgpt.com/backend-api/codex"
_OAUTH_AUTHORIZE_URL = "https://auth.openai.com/authorize"
_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
_OAUTH_REDIRECT_URI = "http://127.0.0.1:1455/auth/callback"
_OAUTH_SCOPE = "openid profile email offline_access"


class ChatGPTAuthError(RuntimeError):
    """Raised when no usable ChatGPT auth source can be obtained."""


@dataclass
class ChatGPTAuth:
    access_token: str
    refresh_token: str
    expires_at: int
    source_file: Path
    client_id: str = _OPENAI_OAUTH_CLIENT_ID
    extra: dict = field(default_factory=dict)

    def is_expired(self, *, skew: int = 60) -> bool:
        return self.expires_at - time.time() <= skew


def _home() -> Path:
    return Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or ".")


def _codex_probe_paths() -> list[Path]:
    """Order matters: most-specific env var first."""
    paths: list[Path] = []
    for env_var, suffix in (
        ("CHATGPT_LOCAL_HOME", "auth.json"),
        ("CODEX_HOME", "auth.json"),
    ):
        base = os.environ.get(env_var)
        if base:
            paths.append(Path(base) / suffix)
    home = _home()
    paths.append(home / ".chatgpt-local" / "auth.json")
    paths.append(home / ".codex" / "auth.json")
    return paths


def _parse_auth_file(path: Path) -> Optional[ChatGPTAuth]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    access = raw.get("access_token")
    refresh = raw.get("refresh_token", "")
    if not access:
        return None
    expires_at = int(raw.get("expires_at") or 0)
    return ChatGPTAuth(
        access_token=access,
        refresh_token=refresh,
        expires_at=expires_at,
        source_file=path,
        client_id=raw.get("client_id") or _OPENAI_OAUTH_CLIENT_ID,
        extra={k: v for k, v in raw.items()
               if k not in {"access_token", "refresh_token", "expires_at", "client_id"}},
    )


def _try_codex_files() -> Optional[ChatGPTAuth]:
    """Tier 1 — first existing Codex/ChatGPT-Local file with a usable token."""
    for path in _codex_probe_paths():
        if not path.exists():
            continue
        auth = _parse_auth_file(path)
        if auth is not None:
            return auth
    return None


def _stored_auth_path() -> Path:
    return _home() / ".deep-researcher" / "chatgpt-auth.json"


def _try_stored_token() -> Optional[ChatGPTAuth]:
    """Tier 2 — our own cached token from a previous PKCE flow."""
    path = _stored_auth_path()
    if not path.exists():
        return None
    return _parse_auth_file(path)


@contextlib.contextmanager
def _file_lock(target: Path):
    """Cross-platform exclusive file lock keyed off a sidecar .lock file.

    Two deep-researcher processes refreshing the same token at once would
    race; this serializes them on a per-file basis.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(target.suffix + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _save_auth_file(target: Path, payload: dict) -> None:
    """Atomic write via tmpfile + os.replace, with 0600 perms on POSIX."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # Windows
    os.replace(tmp, target)


def _refresh_tokens(refresh_token: str, client_id: str) -> dict:
    resp = httpx.post(
        _OAUTH_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "scope": _OAUTH_SCOPE,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def _ensure_fresh(auth: ChatGPTAuth, *, skew: int = 60) -> ChatGPTAuth:
    """If the token is near-expiry, refresh it and persist back to source_file."""
    if not auth.is_expired(skew=skew):
        return auth
    if not auth.refresh_token:
        raise ChatGPTAuthError(
            "ChatGPT access token expired and no refresh token available. "
            "Run: deep-researcher auth-chatgpt"
        )
    with _file_lock(auth.source_file):
        # Re-read in case another process refreshed us first
        if auth.source_file.exists():
            on_disk = _parse_auth_file(auth.source_file)
            if on_disk is not None and not on_disk.is_expired(skew=skew):
                return on_disk
        try:
            new = _refresh_tokens(auth.refresh_token, auth.client_id)
        except httpx.HTTPError as e:
            raise ChatGPTAuthError(
                f"Failed to refresh ChatGPT token: {e}. "
                "Run: deep-researcher auth-chatgpt"
            )
        merged_payload = {
            "access_token": new["access_token"],
            "refresh_token": new.get("refresh_token", auth.refresh_token),
            "expires_at": int(time.time() + int(new.get("expires_in", 3600))),
            "client_id": auth.client_id,
        }
        _save_auth_file(auth.source_file, merged_payload)
        return ChatGPTAuth(
            access_token=merged_payload["access_token"],
            refresh_token=merged_payload["refresh_token"],
            expires_at=merged_payload["expires_at"],
            source_file=auth.source_file,
            client_id=auth.client_id,
        )
