from __future__ import annotations

import re

from rich.console import Console
from rich.panel import Panel

from deep_researcher.config import Config
from deep_researcher.llm import LLMClient
from deep_researcher.models import Paper
from deep_researcher.report import save_report
from deep_researcher.tools import build_tool_registry
from deep_researcher.tools.base import ToolRegistry

SYSTEM_PROMPT = """\
You are an academic research assistant that conducts systematic literature reviews. \
Given a research question, you search academic databases, analyze papers, and produce \
comprehensive reviews with proper citations.

## Your Research Process

1. **Understand** the research question — identify key concepts, relevant fields, and potential search terms
2. **Search broadly** — query multiple databases with varied search terms to ensure comprehensive coverage
3. **Analyze results** — read abstracts and metadata to identify the most relevant papers
4. **Follow citation chains** — for key papers, use get_citations to find foundational and recent related work
5. **Iterate** — refine your search based on what you discover. Use different terminology across databases.
6. **Check open access** — for important papers, use find_open_access to check for free versions
7. **Synthesize** — when you have sufficient coverage (typically 15-30 relevant papers), produce your review

## Available Databases

- **arXiv**: Preprints in physics, math, CS, engineering, biology, economics, statistics
- **Semantic Scholar**: 200M+ papers across all fields, with citation counts and TLDR summaries
- **OpenAlex**: 250M+ works, fully open, excellent metadata coverage
- **CrossRef**: 150M+ records from major publishers (Elsevier, Springer, IEEE, Wiley, etc.)
- **PubMed**: 36M+ biomedical and life sciences citations
- **CORE**: 300M+ open access articles (requires API key)

## Research Strategies

- Start with broad queries, then narrow based on findings
- Use different terminology/synonyms across databases — different fields use different terms
- Search at least 3 different databases for comprehensive coverage
- Follow citation chains from the most relevant papers you find
- Look for review/survey papers — they provide excellent overviews of a field
- Note the chronological development of the field

## Final Report Format

When you have gathered enough information, produce a structured literature review as your final response:

### [Research Topic]: A Literature Review

#### 1. Introduction
Brief overview of the research question and why it matters.

#### 2. Methodology
Databases searched, search queries used, number of papers reviewed, selection criteria.

#### 3. Thematic Analysis
Organize findings by theme or sub-topic (NOT paper-by-paper). For each theme:
- Key findings across papers
- Notable methodologies
- Points of agreement and debate

#### 4. Chronological Development
How has this field evolved? Key milestones and shifts.

#### 5. Research Gaps and Future Directions
What hasn't been studied? Where are the opportunities?

#### 6. Key Papers
List the 10-15 most important papers with brief annotations explaining why each matters.

#### 7. References
Full numbered reference list. Format: [N] Authors (Year). Title. Journal. DOI.

## Important Guidelines

- Be thorough but focused — quality over quantity
- Always cite specific papers when making claims
- Note methodology types (experimental, computational, review, case study, etc.)
- Identify seminal/foundational papers vs. recent developments
- Flag contradictions or debates in the literature
- Be honest about limitations of your search
- Do NOT make up or hallucinate papers — only cite papers you actually found via the tools
"""


class ResearchAgent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.llm = LLMClient(config)
        self.registry = build_tool_registry(config)
        self.papers: dict[str, Paper] = {}
        self.console = Console()

    def research(self, query: str) -> str:
        self.console.print(Panel(f"[bold]{query}[/bold]", title="Research Query", border_style="blue"))

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Please conduct a comprehensive literature review on the following research question:\n\n{query}"},
        ]
        tool_schemas = self.registry.schemas()

        for iteration in range(1, self.config.max_iterations + 1):
            self.console.print(f"\n[dim]--- Iteration {iteration}/{self.config.max_iterations} ---[/dim]")

            try:
                response = self.llm.chat(messages, tools=tool_schemas)
            except Exception as e:
                self.console.print(f"[red]LLM error: {e}[/red]")
                break

            if not response.tool_calls:
                report = response.content or ""
                self.console.print(f"\n[green]Research complete. Collected {len(self.papers)} unique papers.[/green]")
                self._save(query, report)
                return report

            messages.append(_message_to_dict(response))

            for tool_call in response.tool_calls:
                name = tool_call.function.name
                args = tool_call.function.arguments
                self.console.print(f"  [cyan]Calling {name}[/cyan] with {_truncate(args, 100)}")

                result = self.registry.execute(name, args)
                self._extract_papers(result)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })
                self.console.print(f"  [dim]  -> {_truncate(result, 120)}[/dim]")

            self.console.print(f"  [yellow]Papers collected: {len(self.papers)}[/yellow]")

        self.console.print("\n[yellow]Max iterations reached — forcing synthesis...[/yellow]")
        messages.append({
            "role": "user",
            "content": (
                "You have reached the maximum number of search iterations. "
                "Please synthesize all the papers you have found so far into "
                "your final literature review report now."
            ),
        })
        try:
            response = self.llm.chat(messages)
            report = response.content or ""
        except Exception as e:
            report = f"Error generating final report: {e}"

        self._save(query, report)
        return report

    def _extract_papers(self, tool_result: str) -> None:
        """Rough extraction of paper info from tool results to track unique papers."""
        if tool_result.startswith("Error") or "No papers found" in tool_result:
            return

        # Papers are already tracked by the tools themselves,
        # but we do a lightweight parse to count unique papers by title
        for line in tool_result.split("\n"):
            if line.startswith("**") and line.endswith("**"):
                title = line.strip("*").strip()
                if title:
                    p = Paper(title=title, source="extracted")
                    key = p.unique_key
                    if key not in self.papers:
                        self.papers[key] = p
            elif "DOI:" in line:
                doi_match = re.search(r"DOI:\s*(\S+)", line)
                if doi_match:
                    doi = doi_match.group(1)
                    key = f"doi:{doi.lower()}"
                    if key not in self.papers:
                        self.papers[key] = Paper(title="", doi=doi, source="extracted")

    def _save(self, query: str, report: str) -> None:
        if not report.strip():
            return
        try:
            paths = save_report(query, report, self.papers, self.config.output_dir)
            self.console.print(f"\n[green bold]Report saved:[/green bold]")
            for label, path in paths.items():
                self.console.print(f"  {label}: [blue]{path}[/blue]")
        except Exception as e:
            self.console.print(f"[red]Error saving report: {e}[/red]")


def _message_to_dict(msg) -> dict:
    d: dict = {"role": msg.role, "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    return d


def _truncate(s: str, n: int) -> str:
    s = s.replace("\n", " ").strip()
    return s[:n] + "..." if len(s) > n else s
