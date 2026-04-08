"""Persistent user state for returning researchers.

Separate from config.py on purpose: config.json is read-only user
preferences loaded at startup; state.json holds things the *tool* writes
back across runs (last-used query, whether the OAuth advisory was shown,
etc.) so the TUI can pre-fill sensible defaults.

Defensive by design — never raises on a missing or corrupt file. The
worst case is "you see the advisory twice," not a crashed run.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("deep_researcher")

STATE_PATH = Path.home() / ".deep-researcher" / "state.json"


def load_state() -> dict[str, Any]:
    """Return the state dict, or {} if anything is wrong."""
    try:
        if STATE_PATH.is_file():
            with open(STATE_PATH, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        logger.debug("state load failed", exc_info=True)
    return {}


def save_state(**updates: Any) -> None:
    """Merge updates into state.json. Silent on failure."""
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        current = load_state()
        current.update(updates)
        tmp = STATE_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception:
        logger.debug("state save failed", exc_info=True)


def clear_state_keys(*keys: str) -> None:
    """Remove specific keys from state.json. Silent on failure."""
    try:
        current = load_state()
        changed = False
        for k in keys:
            if k in current:
                del current[k]
                changed = True
        if changed:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2)
    except Exception:
        logger.debug("state clear failed", exc_info=True)
