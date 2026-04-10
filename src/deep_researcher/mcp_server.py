"""MCP server for Deep Researcher.

Exposes the literature review pipeline as MCP tools so Claude Desktop,
Claude Code, and other MCP clients can perform academic research
mid-conversation.

Entry points:
    deep-researcher-claude-mcp   (console script)
    python -m deep_researcher.mcp_server
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Context

from deep_researcher.mcp_tools import (
    handle_compare,
    handle_list_runs,
    handle_research,
    handle_search_papers,
    handle_synthesize,
)

mcp = FastMCP(
    name="deep-researcher",
    instructions=(
        "Academic literature review server. Search Google Scholar, "
        "enrich papers with OpenAlex metadata, and synthesize structured "
        "literature reviews using LLMs. Tools run long (2-5 min for full "
        "research) — use search_papers for quick lookups."
    ),
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="research",
    description=(
        "Run a full academic literature review: search Google Scholar + Scopus "
        "for up to 100 papers, enrich with OpenAlex metadata, then synthesize "
        "a structured review with categories, cross-analysis, and references. "
        "Takes 2-5 minutes. Returns the report markdown and output file paths."
    ),
)
async def tool_research(
    query: str,
    provider: str | None = None,
    model: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    no_elsevier: bool = False,
    ctx: Context | None = None,
) -> dict:
    if ctx:
        await ctx.info("Starting research pipeline...")
        await ctx.report_progress(0, 3, "Searching academic databases")

    result = handle_research(
        query=query,
        provider=provider,
        model=model,
        start_year=start_year,
        end_year=end_year,
        no_elsevier=no_elsevier,
    )

    if ctx and "error" not in result:
        await ctx.report_progress(3, 3, "Research complete")

    return result


@mcp.tool(
    name="search_papers",
    description=(
        "Search Google Scholar + Scopus and return enriched paper metadata "
        "WITHOUT running synthesis. Fast (30-60s). Use this for quick lookups, "
        "exploring a topic, or deciding whether to run a full synthesis."
    ),
)
async def tool_search_papers(
    query: str,
    start_year: int | None = None,
    end_year: int | None = None,
    no_elsevier: bool = False,
    ctx: Context | None = None,
) -> dict:
    if ctx:
        await ctx.info("Searching academic databases...")

    return handle_search_papers(
        query=query,
        start_year=start_year,
        end_year=end_year,
        no_elsevier=no_elsevier,
    )


@mcp.tool(
    name="synthesize",
    description=(
        "Re-run LLM synthesis on an existing research output folder "
        "(papers.json must exist). Equivalent to --replay. Useful for "
        "re-synthesizing with a different model or after code changes."
    ),
)
async def tool_synthesize(
    folder: str,
    provider: str | None = None,
    model: str | None = None,
    ctx: Context | None = None,
) -> dict:
    if ctx:
        await ctx.info(f"Re-synthesizing papers in {folder}...")

    return handle_synthesize(folder=folder, provider=provider, model=model)


@mcp.tool(
    name="compare",
    description=(
        "Compare two LLM providers on the same paper corpus. Searches once, "
        "then synthesizes in parallel with both providers and produces a "
        "comparison analysis. Returns both reports plus the analysis."
    ),
)
async def tool_compare(
    query: str,
    provider_a: str,
    provider_b: str,
    start_year: int | None = None,
    end_year: int | None = None,
    ctx: Context | None = None,
) -> dict:
    if ctx:
        await ctx.info(f"Comparing {provider_a} vs {provider_b}...")

    return handle_compare(
        query=query,
        provider_a=provider_a,
        provider_b=provider_b,
        start_year=start_year,
        end_year=end_year,
    )


@mcp.tool(
    name="list_runs",
    description=(
        "List previous research runs from the output directory. "
        "Returns folder paths, queries, dates, and paper counts."
    ),
)
async def tool_list_runs(
    output_dir: str = "./output",
    limit: int = 20,
) -> dict:
    return handle_list_runs(output_dir=output_dir, limit=limit)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource(
    "research://runs",
    name="research_runs",
    description="List all research output folders with metadata",
    mime_type="application/json",
)
async def resource_runs() -> str:
    import json
    result = handle_list_runs(output_dir="./output", limit=100)
    return json.dumps(result, indent=2)


@mcp.resource(
    "research://run/{folder_name}/report.md",
    name="run_report_md",
    description="Markdown report from a specific run",
    mime_type="text/markdown",
)
async def resource_report_md(folder_name: str) -> str:
    import os
    path = os.path.join("./output", folder_name, "report.md")
    if not os.path.exists(path):
        return f"No report.md found in {folder_name}"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@mcp.resource(
    "research://run/{folder_name}/papers.json",
    name="run_papers_json",
    description="Paper metadata from a specific run",
    mime_type="application/json",
)
async def resource_papers_json(folder_name: str) -> str:
    import os
    path = os.path.join("./output", folder_name, "papers.json")
    if not os.path.exists(path):
        return "[]"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@mcp.prompt(
    name="literature_review",
    description="Run a full literature review on a topic and summarize findings",
)
async def prompt_literature_review(topic: str) -> str:
    return (
        f"You are an academic research assistant. The user wants a literature "
        f"review on: {topic}\n\n"
        f"Use the `research` tool to search Google Scholar, enrich papers with "
        f"OpenAlex metadata, and produce a structured synthesis. After the "
        f"research completes:\n\n"
        f"1. Summarize the key findings in 2-3 paragraphs\n"
        f"2. Highlight the most-cited papers\n"
        f"3. Note any gaps or contradictions the synthesis identified\n"
        f"4. Offer to drill into specific categories or open the HTML report"
    )


@mcp.prompt(
    name="find_papers",
    description="Search for papers on a topic and summarize what you find",
)
async def prompt_find_papers(topic: str, year_range: str = "") -> str:
    year_line = f"\nLimit to papers published {year_range}." if year_range else ""
    return (
        f"Search for academic papers on: {topic}{year_line}\n\n"
        f"Use the `search_papers` tool to find relevant papers. After searching:\n"
        f"1. List the top 10 most-cited papers with title, year, and citation count\n"
        f"2. Summarize the main research themes you see\n"
        f"3. Ask if the user wants to run a full synthesis on these papers"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
