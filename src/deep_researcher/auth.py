"""Auth detection helpers for the Claude Agent SDK provider.

Keeps environment / credential checks out of llm_claude.py so they're
independently testable.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def detect_claude_code_session() -> bool:
    """True when running as a child of the `claude` CLI harness.

    The CLI sets CLAUDECODE=1 in subprocess env. Also commonly seen:
    CLAUDE_CODE_ENTRYPOINT, CLAUDE_AGENT_SDK_VERSION. We anchor on
    CLAUDECODE since it's the most stable signal across versions.
    """
    return os.environ.get("CLAUDECODE") == "1"


def detect_claude_oauth_credentials() -> bool:
    """True when `claude login` has populated OAuth credentials on this machine.

    The bundled CLI inside claude_agent_sdk reads from the same store, so
    this also indicates the SDK can authenticate without an API key.
    """
    return Path.home().joinpath(".claude", ".credentials.json").is_file()


def claude_cli_installed() -> bool:
    """True when a system `claude` binary is on PATH (independent of the
    one bundled with claude_agent_sdk)."""
    return shutil.which("claude") is not None


def print_oauth_advisory(console, force: bool = False) -> None:
    """One-line advisory printed when --provider claude is selected.

    Surfaces the policy ambiguity around using consumer-plan OAuth
    credentials with third-party tools, so the user is making an informed
    choice rather than discovering it later.

    Shown once per machine by default — non-technical users running this
    several times a week don't need to re-read the same disclaimer. Pass
    force=True (or use --show-advisory / --reset-auth) to re-print.
    """
    from deep_researcher.state import load_state, save_state

    if not force:
        state = load_state()
        if state.get("advisory_seen"):
            return

    console.print(
        "[dim]Using Claude OAuth credentials from your local `claude` session. "
        "Anthropic's policy reserves OAuth tokens for individual subscribers' "
        "native application use; the supported third-party path is an API key. "
        "You decide whether your usage qualifies.[/dim]"
    )
    console.print(
        "[dim](This notice is shown once. Re-show with --show-advisory.)[/dim]"
    )
    save_state(advisory_seen=True)
