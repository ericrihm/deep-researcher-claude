from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from deep_researcher.config import Config
from deep_researcher.llm import LLMClient
from deep_researcher.models import Paper
from deep_researcher.report import save_report
from deep_researcher.tools import build_tool_registry


def _build_system_prompt(config: Config) -> str:
    return f"""\
You are an academic research assistant that conducts systematic literature reviews. \
Given a research question, you search academic databases, analyze papers, and produce \
comprehensive reviews with proper citations.

## Your Research Process — Three Phases

### Phase 1: DISCOVERY (first ~{config.breadth * 2} tool calls)
- Break the research question into {config.breadth} different search queries using varied terminology
- Search at least 3 different databases per query variant
- Aim to discover 20-40 candidate papers across all databases
- Use broad queries first, then narrow based on what you find

### Phase 2: DEEP DIVE (next ~{config.depth * 3} tool calls)
- For the top {config.depth * 3} most-cited or most-relevant papers, follow citation chains using get_citations
- Get detailed info with get_paper_details on papers that appear across multiple searches
- Look specifically for survey/review papers — they provide excellent field overviews
- Check find_open_access for key papers

### Phase 3: SYNTHESIS (final response)
- You now have enough material — stop searching and write the literature review
- Organize by theme, not paper-by-paper
- Include proper numbered citations

## When to Stop Searching
Stop and synthesize when ANY of these are true:
- You have found 15-30 relevant papers with good coverage
- New searches mostly return papers you have already seen (diminishing returns)
- You have covered at least 3 databases
- You have followed citation chains for the most important papers

## Available Databases

- **arXiv**: Preprints in physics, math, CS, engineering, biology, economics, statistics
- **Semantic Scholar**: 200M+ papers across all fields, with citation counts and TLDR summaries
- **OpenAlex**: 250M+ works, fully open, excellent metadata coverage
- **CrossRef**: 150M+ records from major publishers (Elsevier, Springer, Wiley, IEEE, etc.)
- **PubMed**: 36M+ biomedical and life sciences citations
- **CORE**: 300M+ open access articles (requires API key)

## Research Strategies

- Use different terminology/synonyms across databases — different fields use different terms for similar concepts
- Pay attention to citation counts as a signal of influence
- Look for both seminal/foundational papers AND recent developments
- Note contradictions or debates between papers
- Identify the most common methodologies used in the field

## Final Report Format

Produce a structured literature review as your final response (no tool calls):

### [Research Topic]: A Literature Review

#### 1. Introduction
Brief overview of the research question and its significance.

#### 2. Methodology
Databases searched, search queries used, number of papers reviewed.

#### 3. Thematic Analysis
Organize findings by theme or sub-topic (NOT paper-by-paper). For each theme:
- Key findings across multiple papers
- Notable methodologies used
- Points of agreement and debate

#### 4. Chronological Development
How has this field evolved? Key milestones and paradigm shifts.

#### 5. Research Gaps and Future Directions
What remains understudied? Where are the opportunities?

#### 6. Key Papers
List the 10-15 most important papers with brief annotations explaining significance.

#### 7. References
Full numbered reference list. Format: [N] Authors (Year). Title. Journal. DOI: xxx

## Critical Rules

- ONLY cite papers you actually found via the search tools — never hallucinate references
- Be honest about limitations of your search coverage
- Note methodology types: experimental, computational, review, theoretical, case study
- Flag peer-review status where known (preprint vs. published)
"""


class ResearchAgent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.llm = LLMClient(config)
        self.registry = build_tool_registry(config)
        self.papers: dict[str, Paper] = {}
        self.console = Console()
        self._databases_used: set[str] = set()
        self._tool_call_count = 0

    def research(self, query: str) -> str:
        self.console.print(Panel(
            f"[bold]{query}[/bold]\n[dim]breadth={self.config.breadth}  depth={self.config.depth}  max_iter={self.config.max_iterations}[/dim]",
            title="Deep Researcher",
            border_style="blue",
        ))

        system_prompt = _build_system_prompt(self.config)
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Please conduct a comprehensive literature review on:\n\n{query}"},
        ]
        tool_schemas = self.registry.schemas()

        for iteration in range(1, self.config.max_iterations + 1):
            self.console.print(f"\n[dim]--- Iteration {iteration}/{self.config.max_iterations} | {len(self.papers)} papers | {len(self._databases_used)} databases ---[/dim]")

            try:
                response = self.llm.chat(messages, tools=tool_schemas)
            except Exception as e:
                self.console.print(f"[red]LLM error: {e}[/red]")
                break

            # No tool calls = LLM is done, final report
            if not response.tool_calls:
                report = response.content or ""
                self._print_summary()
                self._save(query, report)
                return report

            messages.append(_message_to_dict(response))

            # Execute tool calls concurrently (Claude Code pattern)
            tc_list = [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                for tc in response.tool_calls
            ]

            if len(tc_list) > 1:
                self.console.print(f"  [cyan]Executing {len(tc_list)} tools concurrently...[/cyan]")
                results = self.registry.execute_concurrent(tc_list)
            else:
                results = []
                for tc in tc_list:
                    result = self.registry.execute(tc["name"], tc["arguments"])
                    results.append((tc["id"], result))

            for call_id, result in results:
                tc_info = next(tc for tc in tc_list if tc["id"] == call_id)
                self._tool_call_count += 1
                self.console.print(f"  [cyan]{tc_info['name']}[/cyan] -> {_truncate(result.text, 100)}")

                # Collect structured papers (no more regex parsing!)
                for paper in result.papers:
                    self._track_paper(paper)

                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result.text,
                })

            self.console.print(f"  [yellow]Total: {len(self.papers)} unique papers from {len(self._databases_used)} databases[/yellow]")

        # Max iterations — force synthesis
        self.console.print("\n[yellow]Max iterations reached — synthesizing...[/yellow]")
        messages.append({
            "role": "user",
            "content": (
                "You have reached the maximum number of search iterations. "
                f"You have found {len(self.papers)} papers across {len(self._databases_used)} databases. "
                "Please synthesize all findings into your final literature review now."
            ),
        })
        try:
            response = self.llm.chat(messages)
            report = response.content or ""
        except Exception as e:
            report = f"Error generating final report: {e}"

        self._print_summary()
        self._save(query, report)
        return report

    def _track_paper(self, paper: Paper) -> None:
        key = paper.unique_key
        if key in self.papers:
            self.papers[key].merge(paper)
        else:
            self.papers[key] = paper
        for src in paper.source.split(","):
            src = src.strip()
            if src:
                self._databases_used.add(src)

    def _print_summary(self) -> None:
        self.console.print(f"\n[green]Research complete.[/green]")
        table = Table(title="Research Summary", show_header=False, border_style="green")
        table.add_row("Papers found", str(len(self.papers)))
        table.add_row("Databases searched", ", ".join(sorted(self._databases_used)))
        table.add_row("Tool calls made", str(self._tool_call_count))

        years = [p.year for p in self.papers.values() if p.year]
        if years:
            table.add_row("Year range", f"{min(years)}-{max(years)}")

        oa_count = sum(1 for p in self.papers.values() if p.open_access_url)
        if oa_count:
            table.add_row("Open access", f"{oa_count}/{len(self.papers)}")

        self.console.print(table)

    def _save(self, query: str, report: str) -> None:
        if not report.strip():
            return
        try:
            paths = save_report(query, report, self.papers, self.config.output_dir)
            self.console.print(f"\n[green bold]Files saved:[/green bold]")
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
