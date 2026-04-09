"""ChatGPT OAuth four-tier auth resolution.

Tier 1: reuse a Codex CLI / ChatGPT-Local auth.json on disk
Tier 2: reuse our own stored token at ~/.deep-researcher/chatgpt-auth.json
Tier 3: PKCE browser login (writes to Tier 2 location for next time)
Tier 4: fall back to OPENAI_API_KEY against api.openai.com (handled by caller)

This module is provider-agnostic: it returns a ChatGPTAuth that the
LLMClient hooks into for per-call token refresh.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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
