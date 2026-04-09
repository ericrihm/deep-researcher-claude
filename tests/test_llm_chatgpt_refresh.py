"""LLMClient refreshes the ChatGPT OAuth token before each call."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_chat_refreshes_expired_chatgpt_token_before_call(tmp_path):
    from deep_researcher.auth_chatgpt import ChatGPTAuth
    from deep_researcher.config import Config
    from deep_researcher.llm import LLMClient

    f = tmp_path / "auth.json"
    f.write_text("{}")
    auth = ChatGPTAuth(
        access_token="old",
        refresh_token="ref",
        expires_at=int(time.time()) - 10,
        source_file=f,
    )
    config = Config(
        model="gpt-5",
        base_url="https://chatgpt.com/backend-api/codex",
        api_key=auth.access_token,
    )
    client = LLMClient(config)
    client._chatgpt_auth = auth

    new_auth = ChatGPTAuth(
        access_token="new",
        refresh_token="ref",
        expires_at=int(time.time()) + 3600,
        source_file=f,
    )

    msg = MagicMock()
    msg.choices = [MagicMock(message=MagicMock())]

    with patch("deep_researcher.llm._ensure_fresh", return_value=new_auth) as mock_fresh, \
         patch.object(client.client.chat.completions, "create", return_value=msg):
        client.chat([{"role": "user", "content": "hi"}])

    mock_fresh.assert_called_once_with(auth)
    assert client._chatgpt_auth.access_token == "new"
    assert client.client.api_key == "new"


def test_chat_skips_refresh_when_no_chatgpt_auth():
    from deep_researcher.config import Config
    from deep_researcher.llm import LLMClient

    config = Config(model="m", base_url="http://x", api_key="y")
    client = LLMClient(config)
    msg = MagicMock()
    msg.choices = [MagicMock(message=MagicMock())]
    with patch("deep_researcher.llm._ensure_fresh") as mock_fresh, \
         patch.object(client.client.chat.completions, "create", return_value=msg):
        client.chat([{"role": "user", "content": "hi"}])
    mock_fresh.assert_not_called()
