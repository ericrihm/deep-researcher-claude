"""Tests for the MCP server registration and basic tool dispatch."""
from __future__ import annotations

import pytest

# Gate: skip entire module if mcp is not installed
mcp = pytest.importorskip("mcp")


def test_server_instance_exists():
    """The mcp_server module exposes a FastMCP 'mcp' instance."""
    from deep_researcher.mcp_server import mcp as server
    from mcp.server.fastmcp import FastMCP
    assert isinstance(server, FastMCP)


def test_tools_registered():
    """All expected tools are registered on the server."""
    from deep_researcher.mcp_server import mcp as server
    # FastMCP stores tools in _tool_manager
    tool_names = set()
    for tool in server._tool_manager._tools.values():
        tool_names.add(tool.name)
    expected = {"research", "search_papers", "synthesize", "compare", "list_runs"}
    assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"


def test_prompts_registered():
    """Expected prompts are registered."""
    from deep_researcher.mcp_server import mcp as server
    prompt_names = set(server._prompt_manager._prompts.keys())
    expected = {"literature_review", "find_papers"}
    assert expected.issubset(prompt_names), f"Missing prompts: {expected - prompt_names}"


def test_console_script_entry_point():
    """The deep-researcher-mcp console script is configured in pyproject.toml."""
    import importlib.metadata
    eps = importlib.metadata.entry_points()
    # In Python 3.12+, entry_points() returns a dict-like; in 3.10-3.11
    # it returns SelectableGroups. Handle both.
    if hasattr(eps, "select"):
        scripts = eps.select(group="console_scripts")
    else:
        scripts = eps.get("console_scripts", [])
    names = [ep.name for ep in scripts]
    assert "deep-researcher-mcp" in names, f"Entry point not found. Available: {names}"
