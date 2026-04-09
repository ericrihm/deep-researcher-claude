"""Elsevier/Scopus API key resolution + one-shot onboarding notice.

Precedence (highest to lowest):
    1. --elsevier-key CLI flag
    2. ELSEVIER_API_KEY environment variable
    3. elsevier_key / scopus_api_key in config.json
    4. Bundled default key (friendly onboarding)

Only the bundled default triggers the "borrowing mine" notice, which
prints at most once per process.
"""
from __future__ import annotations

import os
from typing import Optional

from rich.console import Console

from deep_researcher.tools.scopus import _BUNDLED_ELSEVIER_KEY


_borrowing_notice_printed = False


def _reset_borrowing_notice_state() -> None:
    """Test helper — clears the one-shot state between unit tests."""
    global _borrowing_notice_printed
    _borrowing_notice_printed = False


def resolve_elsevier_key(
    *, flag_key: Optional[str], config_key: Optional[str]
) -> tuple[str, bool]:
    """Return (api_key, is_bundled_default).

    Empty strings are treated as unset so `--elsevier-key ""` still falls
    through to the bundled default rather than disabling Elsevier.
    """
    if flag_key:
        return flag_key, False
    env_key = (os.environ.get("ELSEVIER_API_KEY") or "").strip()
    if env_key:
        return env_key, False
    if config_key:
        return config_key, False
    return _BUNDLED_ELSEVIER_KEY, True


def print_borrowing_notice_once(console: Console) -> None:
    """Print the friendly 'borrow mine' message exactly once per process.

    Safe to call multiple times — idempotent. The caller is responsible
    for only invoking this when is_bundled=True.
    """
    global _borrowing_notice_printed
    if _borrowing_notice_printed:
        return
    _borrowing_notice_printed = True
    console.print(
        "[dim]Borrowing Eric's Elsevier API key for Scopus search.[/dim]"
    )
    console.print(
        "[dim]It works for light personal use, but if you're running this a "
        "lot please grab your own free key — 2-minute signup, 20k requests/"
        "week quota:[/dim]"
    )
    console.print(
        "[dim]  https://dev.elsevier.com/apikey/manage   (create a key)[/dim]"
    )
    console.print(
        "[dim]  https://dev.elsevier.com/sd_api_spec.html  (API docs)[/dim]"
    )
