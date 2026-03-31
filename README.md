# Deep Researcher

An agentic academic research assistant that searches multiple databases and produces structured literature reviews.

Built using agentic design patterns studied from [Claude Code's architecture](https://github.com/anthropics/claude-code) — the same tool-call loop, concurrent execution, and structured tool result patterns used in production by Anthropic's CLI. Clean-room Python implementation; no code copied, just battle-tested architectural patterns applied to academic research.

Unlike simple search-and-summarize tools, Deep Researcher uses a **real agentic loop** — the LLM decides what to search, reads results, refines queries, follows citation chains, and only synthesizes when it has enough material. Like a real researcher would.

## Features

- **9 tools** across 6 academic databases (arXiv, Semantic Scholar, OpenAlex, CrossRef, PubMed, CORE)
- **Agentic loop** — the LLM decides search strategy, iterates, and follows citation chains
- **Concurrent tool execution** — multiple database searches run in parallel (Claude Code pattern)
- **Structured paper tracking** — automatic deduplication and metadata merging across databases
- **Multi-phase research** — guided discovery, deep dive, and synthesis phases
- **Depth/breadth controls** — tune research intensity with simple parameters
- **Model agnostic** — works with Ollama (local), OpenAI, Anthropic, LMStudio, or any OpenAI-compatible API
- **Structured output** — Markdown report + BibTeX + JSON + research metadata
- **Saves on interrupt** — Ctrl+C preserves all papers found so far
- **Config file support** — `~/.deep-researcher/config.json` for persistent settings
- **Minimal dependencies** — just `openai`, `httpx`, and `rich`. No LangChain, no framework bloat

## Quick Start

### Install

```bash
pip install -e .
```

### Run with Ollama (local, free)

Make sure [Ollama](https://ollama.com) is running with a model that supports function calling:

```bash
ollama pull llama3.1
deep-researcher "applications of transformer models in structural health monitoring"
```

### Run with OpenAI

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_API_KEY="sk-..."
export DEEP_RESEARCH_MODEL="gpt-4o"
deep-researcher "machine learning for drug discovery"
```

### Run with Anthropic

```bash
export OPENAI_BASE_URL="https://api.anthropic.com/v1"
export OPENAI_API_KEY="sk-ant-..."
export DEEP_RESEARCH_MODEL="claude-sonnet-4-20250514"
deep-researcher "CRISPR gene editing efficiency improvements"
```

## Usage

```
deep-researcher "your research question" [options]

Options:
  --model MODEL          LLM model name (default: llama3.1)
  --base-url URL         OpenAI-compatible API URL (default: http://localhost:11434/v1)
  --api-key KEY          API key (default: ollama)
  --max-iterations N     Max research iterations (default: 20)
  --output DIR           Output directory (default: ./output)
  --email EMAIL          Email for polite API access (recommended)
  --breadth N            Search breadth: query variations per topic (1-5, default: 3)
  --depth N              Search depth: citation chain rounds (0-5, default: 2)
  --version              Show version
```

### Depth & Breadth

These control research intensity without needing to understand the internals:

```bash
# Quick scan — fewer queries, shallow citations
deep-researcher "topic" --breadth 1 --depth 0

# Standard research (default)
deep-researcher "topic" --breadth 3 --depth 2

# Comprehensive deep dive — many query variations, deep citation chains
deep-researcher "topic" --breadth 5 --depth 4
```

## Output

Each research session produces four files in `./output/<timestamp>-<topic>/`:

| File | Contents |
|---|---|
| `report.md` | Literature review with metadata header and thematic analysis |
| `references.bib` | Deduplicated BibTeX entries for all papers |
| `papers.json` | Full metadata for all papers (for programmatic use) |
| `metadata.json` | Research statistics: query, databases, paper count, year range |

## Architecture

```
User query + depth/breadth config
    |
    v
+--------------------------------------+
|  Phase 1: DISCOVERY                  |
|  - Generate varied search queries    |
|  - Search 3+ databases concurrently  |
|  - Collect 20-40 candidate papers    |
+--------------------------------------+
    |
    v
+--------------------------------------+
|  Phase 2: DEEP DIVE                  |
|  - Follow citation chains            |
|  - Get details on key papers         |
|  - Check open access availability    |
+--------------------------------------+
    |
    v
+--------------------------------------+
|  Phase 3: SYNTHESIS                  |
|  - Structured literature review      |
|  - Thematic analysis                 |
|  - Numbered citations                |
+--------------------------------------+
    |
    v
report.md + references.bib + papers.json + metadata.json
```

### Design Patterns from Claude Code

| Pattern | Claude Code | Deep Researcher |
|---|---|---|
| **Agentic loop** | `queryLoop()` in query.ts | `research()` in agent.py |
| **Tool abstraction** | `buildTool()` with schema + execute | `Tool` class with schema + `execute()` → `ToolResult` |
| **Concurrent execution** | `partitionToolCalls()` batching | `execute_concurrent()` via ThreadPoolExecutor |
| **Structured results** | `ToolResult<T>` with data + messages | `ToolResult` with text + papers |
| **Paper dedup** | File content hashing | DOI/arXiv/PMID keys with metadata merging |
| **Retry/recovery** | Exponential backoff + reactive compact | Exponential backoff on 429/5xx per tool |

### Tools

| Tool | Database | Coverage |
|---|---|---|
| `search_arxiv` | arXiv | Preprints: CS, physics, math, engineering, biology |
| `search_semantic_scholar` | Semantic Scholar | 200M+ papers, all fields, citation counts |
| `search_openalex` | OpenAlex | 250M+ works, fully open metadata |
| `search_crossref` | CrossRef | 150M+ DOI records (Elsevier, Springer, IEEE, Wiley) |
| `search_pubmed` | PubMed | 36M+ biomedical and life sciences |
| `search_core` | CORE | 300M+ open access articles |
| `get_paper_details` | Semantic Scholar | Detailed info for a specific paper by DOI |
| `get_citations` | Semantic Scholar | Citation chains (who cites this / what this cites) |
| `find_open_access` | Unpaywall | Find free legal copies of paywalled papers |

### How it compares

| | GPT Researcher | STORM | local-deep-research | **Deep Researcher** |
|---|---|---|---|---|
| Architecture | LangChain + Tavily | DSPy pipeline | LangChain + LangGraph | Raw agentic loop |
| Academic DBs | 0 (web search) | 0 (web search) | 3 | **6+** |
| Dependencies | ~50+ | ~30+ | ~50+ | **3** |
| Codebase | ~15K+ lines | ~10K+ lines | ~15K+ lines | **~1.5K lines** |
| Citation output | Links | Inline | Links | **BibTeX + numbered** |
| Concurrent search | Yes | Yes | Partial | **Yes** |
| Paper dedup | No | No | No | **Yes (DOI/ID merge)** |
| Citation chains | No | No | No | **Yes** |
| Open access check | No | No | No | **Yes (Unpaywall)** |

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DEEP_RESEARCH_MODEL` | `llama3.1` | LLM model name |
| `OPENAI_BASE_URL` | `http://localhost:11434/v1` | API endpoint |
| `OPENAI_API_KEY` | `ollama` | API key |
| `DEEP_RESEARCH_MAX_ITER` | `20` | Max agentic loop iterations |
| `DEEP_RESEARCH_OUTPUT` | `./output` | Output directory |
| `DEEP_RESEARCH_EMAIL` | (empty) | Email for polite API access |
| `CORE_API_KEY` | (empty) | Free API key from [CORE](https://core.ac.uk/api-keys/register) |

### Config File

Create `~/.deep-researcher/config.json` for persistent settings:

```json
{
  "model": "gpt-4o",
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-...",
  "email": "you@university.edu",
  "output_dir": "~/research/output"
}
```

Environment variables override config file. CLI args override both.

## Recommended Models

| Provider | Model | Notes |
|---|---|---|
| Ollama | `llama3.1` | Good function calling, runs locally |
| Ollama | `qwen2.5:14b` | Excellent function calling |
| OpenAI | `gpt-4o` | Best overall quality |
| OpenAI | `gpt-4o-mini` | Good balance of speed and quality |
| Anthropic | `claude-sonnet-4-20250514` | Excellent research synthesis |

## Extending

Adding a new database:

```python
from deep_researcher.tools.base import Tool
from deep_researcher.models import Paper, ToolResult

class MyDatabaseTool(Tool):
    name = "search_my_database"
    description = "Search My Database for ..."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
    }

    def execute(self, query: str) -> ToolResult:
        papers = call_my_api(query)
        text = format_results(papers)
        return ToolResult(text=text, papers=papers)
```

Register it in `src/deep_researcher/tools/__init__.py`.

## License

MIT
