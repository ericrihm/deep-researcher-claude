# Deep Researcher

An agentic academic research assistant that searches multiple databases and produces structured literature reviews. Inspired by the agentic loop architecture of [Claude Code](https://github.com/anthropics/claude-code).

Unlike simple search-and-summarize tools, Deep Researcher uses a **real agentic loop** — the LLM decides what to search, reads results, refines queries, follows citation chains, and only synthesizes when it has enough material. Like a real researcher would.

## Features

- **9 tools** across 6 academic databases (arXiv, Semantic Scholar, OpenAlex, CrossRef, PubMed, CORE)
- **Agentic loop** — the LLM decides search strategy, iterates, and follows citation chains
- **Model agnostic** — works with Ollama (local), OpenAI, Anthropic, LMStudio, or any OpenAI-compatible API
- **Structured output** — Markdown literature review + BibTeX references + JSON paper data
- **Minimal dependencies** — just `openai`, `httpx`, and `rich`. No LangChain, no framework bloat
- **~800 lines of code** — clean, hackable, easy to extend

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
  --version              Show version
```

## Output

Each research session produces three files in `./output/<date>-<topic>/`:

| File | Contents |
|---|---|
| `report.md` | Structured literature review with thematic analysis |
| `references.bib` | BibTeX entries for all papers found |
| `papers.json` | Full metadata for all papers (for programmatic use) |

## Architecture

```
User query
    |
    v
+---------------------------+
|  Agentic Loop             |
|  while not done:          |
|    1. LLM decides action  |
|    2. Execute tool        |
|    3. Append result       |
|    4. LLM decides: more?  |
|       -> refine & loop    |
|       -> or synthesize    |
+---------------------------+
    |
    v
Markdown report + BibTeX + JSON
```

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

### How it differs from local-deep-research

| | local-deep-research | Deep Researcher |
|---|---|---|
| Architecture | LangChain pipeline | Raw agentic loop (Claude Code pattern) |
| Research loop | LangGraph strategy | LLM-driven iterative search with citation following |
| Academic sources | 3 (arXiv, PubMed, S2) | 6+ (+ OpenAlex, CrossRef, CORE, Unpaywall) |
| Dependencies | ~50+ (LangChain ecosystem) | 3 (openai, httpx, rich) |
| Codebase | ~15K+ lines | ~800 lines |
| Output | Web UI focused | CLI-first with structured files |
| Citation format | Links | BibTeX + numbered references |

## Configuration

All settings can be configured via environment variables:

| Variable | Default | Description |
|---|---|---|
| `DEEP_RESEARCH_MODEL` | `llama3.1` | LLM model name |
| `OPENAI_BASE_URL` | `http://localhost:11434/v1` | API endpoint |
| `OPENAI_API_KEY` | `ollama` | API key |
| `DEEP_RESEARCH_MAX_ITER` | `20` | Max agentic loop iterations |
| `DEEP_RESEARCH_OUTPUT` | `./output` | Output directory |
| `DEEP_RESEARCH_EMAIL` | (empty) | Email for polite API access |
| `CORE_API_KEY` | (empty) | Free API key from [CORE](https://core.ac.uk/api-keys/register) |

## Recommended Models

For best results with function calling:

| Provider | Model | Notes |
|---|---|---|
| Ollama | `llama3.1` | Good function calling, runs locally |
| Ollama | `qwen2.5:14b` | Excellent function calling |
| Ollama | `mistral-nemo` | Fast, decent results |
| OpenAI | `gpt-4o` | Best overall quality |
| OpenAI | `gpt-4o-mini` | Good balance of speed and quality |
| Anthropic | `claude-sonnet-4-20250514` | Excellent research synthesis |

## Extending

Adding a new database is straightforward. Create a tool in `src/deep_researcher/tools/`:

```python
from deep_researcher.tools.base import Tool
from deep_researcher.models import Paper

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

    def execute(self, query: str) -> str:
        # Call the API, parse results into Paper objects
        papers = call_my_api(query)
        return format_results(papers)
```

Then register it in `src/deep_researcher/tools/__init__.py`.

## License

MIT
