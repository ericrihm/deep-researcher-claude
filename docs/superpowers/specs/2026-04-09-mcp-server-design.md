# MCP Server for Deep Researcher

## Goal

Expose deep-researcher's literature review pipeline as an MCP (Model Context Protocol) server so that Claude Desktop, Claude Code, and any MCP-compatible client can perform academic research mid-conversation — searching papers, running synthesis, comparing providers, and browsing previous runs — without leaving the chat.

## Architecture

The server is a thin translation layer: MCP JSON-RPC requests map to existing Orchestrator methods. No new research logic is written; the MCP layer handles serialization, progress reporting, and headless execution. Two new files (`mcp_server.py` and `mcp_tools.py`) plus a console-script entry point.

The `mcp` Python SDK (FastMCP) provides the transport and protocol handling. The server runs as a stdio subprocess launched by the MCP client.

## Tech Stack

- `mcp[cli]>=1.0.0` — MCP Python SDK (FastMCP pattern)
- Existing deep-researcher internals (Orchestrator, Config, models, tools)
- No additional dependencies

---

## MCP Tools

### `research`

Full pipeline: search Google Scholar + Scopus, enrich via OpenAlex, synthesize with LLM.

**Parameters:**
- `query` (string, required) — research question
- `provider` (string, optional) — LLM provider name (default: from config)
- `model` (string, optional) — model override
- `start_year` (integer, optional) — filter papers from this year
- `end_year` (integer, optional) — filter papers to this year
- `no_elsevier` (boolean, optional, default false) — skip Scopus search

**Returns:** JSON object:
```json
{
  "report_markdown": "### ...",
  "paper_count": 87,
  "synthesis_paper_count": 75,
  "categories": {"Theme A": [0,1,2], "Theme B": [3,4]},
  "year_range": "2018-2026",
  "open_access_count": 34,
  "output_folder": "/abs/path/to/output/...",
  "files": {
    "html": "/abs/path/report.html",
    "markdown": "/abs/path/report.md",
    "bibtex": "/abs/path/references.bib",
    "papers_json": "/abs/path/papers.json",
    "papers_csv": "/abs/path/papers.csv"
  }
}
```

**Progress notifications** at phase boundaries: searching, enriching (with count), synthesizing (per-category).

### `search_papers`

Search only — returns paper metadata without running synthesis. Fast (30-60s). Useful for iterative exploration: search first, decide whether to synthesize.

**Parameters:**
- `query` (string, required)
- `start_year` (integer, optional)
- `end_year` (integer, optional)
- `max_results` (integer, optional, default 100)
- `no_elsevier` (boolean, optional, default false)

**Returns:** JSON object:
```json
{
  "papers": [
    {
      "title": "...",
      "authors": ["..."],
      "year": 2024,
      "abstract": "...",
      "doi": "10.1234/...",
      "journal": "...",
      "citation_count": 42,
      "open_access_url": "https://...",
      "source": "scholar,openalex"
    }
  ],
  "total_count": 87,
  "year_range": "2018-2026",
  "sources": {"scholar": 72, "scopus": 15}
}
```

### `synthesize`

Run synthesis on a previously-searched paper set (by output folder path). Equivalent to `--replay`.

**Parameters:**
- `folder` (string, required) — path to an existing output folder containing papers.json
- `provider` (string, optional)
- `model` (string, optional)

**Returns:** Same structure as `research`.

### `compare`

Dual-provider comparison: search once, synthesize with two LLMs in parallel, produce comparison analysis.

**Parameters:**
- `query` (string, required)
- `provider_a` (string, required)
- `provider_b` (string, required)
- `start_year` (integer, optional)
- `end_year` (integer, optional)

**Returns:** JSON object:
```json
{
  "report_a": "### ...",
  "report_b": "### ...",
  "comparison_analysis": "...",
  "provider_a": "claude",
  "provider_b": "openai",
  "paper_count": 87,
  "output_folder": "/abs/path/...",
  "compare_html": "/abs/path/compare.html"
}
```

### `list_runs`

Browse previous research output folders.

**Parameters:**
- `output_dir` (string, optional, default "./output")
- `limit` (integer, optional, default 20)

**Returns:** JSON array:
```json
{
  "runs": [
    {
      "folder": "/abs/path/to/output/2026-04-09-...",
      "query": "CRISPR gene editing",
      "generated": "2026-04-09T16:18:23",
      "paper_count": 87,
      "mode": "single",
      "has_html": true
    }
  ]
}
```

---

## MCP Resources

Resources provide read access to research artifacts without re-running anything.

| URI Template | Description |
|---|---|
| `research://runs` | JSON list of all output folders with metadata |
| `research://run/{folder_name}/report.md` | Markdown report from a specific run |
| `research://run/{folder_name}/papers.json` | Paper metadata from a specific run |
| `research://run/{folder_name}/report.html` | HTML report (self-contained) |

`{folder_name}` is the basename of the output folder (e.g., `2026-04-09-161823-crispr-gene-editing`).

---

## MCP Prompts

### `literature_review`

Pre-built prompt that guides the client through a research workflow.

**Arguments:**
- `topic` (string, required) — the research area

**Prompt template:**
```
You are an academic research assistant. The user wants a literature review on: {topic}

Use the `research` tool to search Google Scholar, enrich papers with OpenAlex metadata, and produce a structured synthesis. After the research completes:

1. Summarize the key findings in 2-3 paragraphs
2. Highlight the most-cited papers
3. Note any gaps or contradictions the synthesis identified
4. Offer to open the HTML report or export specific sections
```

### `find_papers`

Quick search prompt for paper discovery.

**Arguments:**
- `topic` (string, required)
- `year_range` (string, optional) — e.g., "2020-2025"

**Prompt template:**
```
Search for academic papers on: {topic}
{if year_range: Limit to papers published {year_range}.}

Use the `search_papers` tool to find relevant papers. After searching:
1. List the top 10 most-cited papers with title, year, and citation count
2. Summarize the main research themes you see
3. Ask if the user wants to run a full synthesis on these papers
```

---

## File Structure

```
src/deep_researcher/
    mcp_server.py       # FastMCP server: tool/resource/prompt registration, entry point
    mcp_tools.py        # Tool handler implementations (Orchestrator wrappers)
    ...existing files unchanged...
```

## Headless Orchestrator

The existing Orchestrator prints Rich console output (panels, spinners, status). For MCP:

- Pass `Console(file=io.StringIO(), quiet=True)` to suppress terminal output
- Extract progress info and emit MCP progress notifications instead
- The Orchestrator's public API (`research()`, `replay()`, `compare_research()`) returns data; the MCP handler converts it to JSON

No changes to Orchestrator itself — the MCP layer controls what Console it receives.

## Config Resolution

The MCP server uses the same config chain as the CLI:
1. Environment variables (`OPENAI_API_KEY`, `DEEP_RESEARCH_MODEL`, etc.)
2. Config file (`~/.deep-researcher/config.json`)
3. Per-tool-call overrides (provider, model params)

For `--provider claude`, the MCP server auto-detects `claude login` credentials the same way the CLI does. The user must have run `claude login` before starting the server.

## Entry Points

**Console script:** `deep-researcher-mcp` (added to pyproject.toml)

**Module:** `python -m deep_researcher.mcp_server`

**Claude Desktop config (example):**
```json
{
  "mcpServers": {
    "deep-researcher": {
      "command": "deep-researcher-mcp"
    }
  }
}
```

**Claude Code config (example):**
```json
{
  "mcpServers": {
    "deep-researcher": {
      "command": "deep-researcher-mcp",
      "args": []
    }
  }
}
```

## Dependency

Add `mcp[cli]>=1.0.0` as an optional dependency:

```toml
[project.optional-dependencies]
mcp = ["mcp[cli]>=1.0.0"]
dev = ["pytest>=8.0.0"]
```

The CLI tool (`deep-researcher`) does not require `mcp`. The MCP server entry point (`deep-researcher-mcp`) requires `pip install deep-researcher[mcp]`.

## Error Handling

- Tools return structured error JSON on failure: `{"error": "message", "phase": "search"}`
- Long timeouts (10 min) — research pipelines are slow by nature
- Graceful degradation: if one search source fails, proceed with the other
- If the LLM provider is unavailable, return the error immediately rather than retrying indefinitely

## Testing

- Unit tests for `mcp_tools.py` handlers with mocked Orchestrator
- Integration test: start MCP server, call each tool via the `mcp` SDK test client
- All existing tests remain unchanged (no modifications to existing code)
