from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from deep_researcher.config import Config
from deep_researcher.llm import LLMClient
from deep_researcher.models import Paper
from deep_researcher.report import get_output_folder, save_checkpoint, save_report
from deep_researcher.tools import build_tool_registry

import logging

logger = logging.getLogger("deep_researcher")


# --- Search phase prompt: focus on GATHERING papers, not writing ---

def _build_search_prompt(config: Config) -> str:
    year_note = ""
    if config.start_year is not None or config.end_year is not None:
        yr_start = config.start_year if config.start_year is not None else "any"
        yr_end = config.end_year if config.end_year is not None else "present"
        year_note = f"\n**Year filter active: {yr_start} to {yr_end}.** Results are automatically filtered, but you should still prioritize queries relevant to this period.\n"

    return f"""\
You are a research paper collector. Your ONLY job right now is to find as many relevant \
papers as possible on the given topic. Do NOT write a report. Just search.
{year_note}
## Strategy
1. Break the topic into {config.breadth} different search angles using varied terminology
2. Search at least 3 different databases per angle — use a mix of tool categories:
   - **preprint** tools for cutting-edge work (arXiv)
   - **index** tools for broad coverage (Semantic Scholar, PubMed)
   - **open_access** tools for free full-text (OpenAlex, CORE)
   - **publisher** tools for paywalled/established work (CrossRef, Scopus, IEEE)
   - **citation** tools to follow reference chains
3. When you find highly-cited papers (>50 citations), follow their citation chains
4. Use get_paper_details on papers that appear in multiple databases
5. Look for survey/review papers — they reference dozens of other relevant papers

## When to Stop
Stop searching (respond WITHOUT any tool calls) when:
- You have found 20+ relevant papers
- New searches mostly return papers you already found
- You have searched 3+ databases
- You have followed citation chains for the top-cited papers

## Rules
- Search aggressively — cast a wide net
- Use different terminology across databases (different fields use different terms)
- Prioritize recent work AND foundational papers
- Do NOT write any analysis or report yet — just search
- When you are done searching, simply respond without calling any tools

## Available Databases
- **arXiv** [preprint]: Preprints in CS, physics, math, engineering, biology
- **Semantic Scholar** [index]: 200M+ papers, citation counts, TLDR summaries
- **OpenAlex** [open_access]: 250M+ works, fully open metadata
- **CrossRef** [publisher]: 150M+ records from Elsevier, Springer, IEEE, Wiley
- **PubMed** [index]: 36M+ biomedical and life sciences
- **CORE** [open_access]: 300M+ open access articles
- **Scopus** [publisher]: 90M+ records from most major publishers (abstracts of paywalled papers too)
- **IEEE Xplore** [publisher]: 6M+ IEEE/IET engineering and CS papers
"""


# --- Synthesis prompt: categorize and synthesize from structured corpus ---

_SYNTHESIS_PROMPT = """\
You are a research analyst. Below is a corpus of {count} papers found across {db_count} \
academic databases on the topic: "{query}"

Your job: **categorize these papers and synthesize findings across categories.** \
Not a story. Not a history lesson. A structured analysis.

## The Paper Corpus

{corpus}

## Output Format

### {query}

#### Coverage
One line: how many papers, which databases, what year range.

#### Categories

For each category you identify (typically 3-6 categories):

##### Category Name (N papers)
**What this group does:** 1-2 sentences describing the shared approach/theme.
**Key methods:** List the specific methods/techniques used across papers in this group.
**Main findings:** What do papers in this group collectively show? Where do they agree? Disagree?
**Limitations:** What are the common weaknesses across this group?

| Ref | Paper | Year | Method | Key Finding | Citations |
|-----|-------|------|--------|-------------|-----------|
| [N] | Author et al. | Year | Approach | Result | Count |

(List ALL papers in this category in the table, not just the top ones)

#### Cross-Category Patterns
What patterns emerge across categories? Which categories are converging? \
What contradictions exist between groups? Which papers bridge multiple categories?

#### Gaps & Opportunities
Be specific. Name concrete research questions that nobody has addressed. \
Point to specific combinations of methods/domains that haven't been tried.

#### Open Access Papers
List papers with free full-text versions available (if any were found).

#### References
[N] Authors (Year). Title. *Journal*. DOI: xxx

(Number every paper. Include ALL papers from the corpus, not just the ones you discuss.)

## Rules
- EVERY paper in the corpus must appear in at least one category table AND in References
- Categorize by approach/theme, NOT by database source
- Synthesize ACROSS papers — don't summarize each paper individually
- Be direct. No filler. No "In recent years..." No hedging.
- If papers contradict each other, say so explicitly
- Do NOT invent papers that aren't in the corpus above
"""


# Token budget for search phase context (leave room for tool schemas + response)
_MAX_SEARCH_TOKENS = 80_000


def _compact_messages(messages: list[dict], token_estimate_fn) -> list[dict]:
    """Token-aware context compression (Claude Code autoCompact pattern).

    Instead of counting messages, estimates token usage and compresses
    old tool results when approaching the context limit. Preserves
    paper identifiers so the model knows what it already found.
    """
    estimated = token_estimate_fn(messages)
    if estimated < _MAX_SEARCH_TOKENS:
        return messages

    # Find tool messages, compress oldest ones
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if not tool_indices:
        return messages

    # Compress from oldest until we're under budget
    compacted = list(messages)
    for idx in tool_indices:
        if token_estimate_fn(compacted) < _MAX_SEARCH_TOKENS:
            break
        content = compacted[idx]["content"]
        if len(content) > 300:
            # Keep the summary line + paper count, drop details
            lines = content.split("\n")
            summary = lines[0] if lines else "Search results"
            compacted[idx] = {**compacted[idx], "content": f"{summary}\n[Results compressed — papers tracked separately]"}

    return compacted


def _build_paper_corpus(papers: dict[str, Paper]) -> str:
    """Build a structured paper corpus for the synthesis prompt."""
    if not papers:
        return "(No papers found)"

    # Sort by citation count (highest first), then by year (newest first)
    sorted_papers = sorted(
        papers.values(),
        key=lambda p: (-(p.citation_count or 0), -(p.year or 0)),
    )

    lines = []
    for i, p in enumerate(sorted_papers, 1):
        entry = f"[{i}] {p.title}"
        parts = []
        if p.authors:
            author_str = p.authors[0]
            if len(p.authors) > 1:
                author_str += " et al."
            parts.append(author_str)
        if p.year:
            parts.append(str(p.year))
        if p.journal:
            parts.append(p.journal)
        if p.citation_count is not None:
            parts.append(f"{p.citation_count} citations")
        if p.doi:
            parts.append(f"DOI: {p.doi}")
        if p.open_access_url:
            parts.append(f"OA: {p.open_access_url}")

        entry += f"\n   {' | '.join(parts)}"

        if p.abstract:
            abstract = p.abstract[:250]
            if len(p.abstract) > 250:
                cut = abstract.rfind(". ")
                abstract = abstract[:cut + 1] if cut > 150 else abstract + "..."
            entry += f"\n   Abstract: {abstract}"

        # Track which databases found this paper
        if p.source:
            entry += f"\n   Found in: {p.source}"

        lines.append(entry)

    return "\n\n".join(lines)


_CLARIFY_PROMPT = """\
You are a research assistant helping to refine a research question before searching academic databases.

Given the user's research topic, generate exactly 3 short, focused clarifying questions that would \
help narrow the search and produce better results. Focus on:
- Specific subfield or application domain
- Time period or recency preferences
- Methodological focus (theoretical, empirical, computational, etc.)

Format: Return ONLY the 3 questions, one per line, numbered 1-3. No preamble.
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
        self._output_folder: str = ""

    def clarify(self, query: str) -> str:
        """Ask clarifying questions and return an enhanced query."""
        self.console.print("\n[bold]Generating clarifying questions...[/bold]\n")
        try:
            response = self.llm.chat([
                {"role": "system", "content": _CLARIFY_PROMPT},
                {"role": "user", "content": query},
            ])
            questions = (response.content or "").strip()
        except Exception as e:
            self.console.print(f"[yellow]Could not generate questions: {e}. Proceeding with original query.[/yellow]")
            return query

        if not questions:
            return query

        self.console.print(questions)
        self.console.print("\n[dim]Answer each question (press Enter to skip):[/dim]\n")

        answers = []
        for line in questions.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                answer = input(f"  {line}\n  > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if answer:
                answers.append(answer)

        if not answers:
            return query

        # Combine original query + answers into enhanced query
        enhanced = f"{query}\n\nAdditional context from the researcher:\n"
        enhanced += "\n".join(f"- {a}" for a in answers)
        self.console.print(f"\n[green]Enhanced query ready.[/green]")
        return enhanced

    def research(self, query: str) -> str:
        self.console.print(Panel(
            f"[bold]{query}[/bold]\n[dim]breadth={self.config.breadth}  depth={self.config.depth}  max_iter={self.config.max_iterations}[/dim]",
            title="Deep Researcher",
            border_style="blue",
        ))

        # Create output folder early for checkpoints
        self._output_folder = get_output_folder(query, self.config.output_dir)

        # === PHASE 1 & 2: Search (gather papers) ===
        self.console.print("\n[bold blue]Phase 1-2: Searching databases...[/bold blue]")
        self._search_phase(query)

        # === PHASE 3: Synthesize (categorize + analyze) ===
        self.console.print(f"\n[bold blue]Phase 3: Synthesizing {len(self.papers)} papers...[/bold blue]")
        report = self._synthesis_phase(query)

        self._print_summary()
        self._save(query, report)
        return report

    def _search_phase(self, query: str) -> None:
        """Run the agentic search loop to collect papers."""
        search_prompt = _build_search_prompt(self.config)
        messages: list[dict] = [
            {"role": "system", "content": search_prompt},
            {"role": "user", "content": f"Find all relevant papers on:\n\n{query}"},
        ]
        tool_schemas = self.registry.schemas()
        compact_failures = 0  # Circuit breaker (Claude Code pattern)
        _MAX_COMPACT_FAILURES = 3

        for iteration in range(1, self.config.max_iterations + 1):
            self.console.print(f"\n[dim]--- Search {iteration}/{self.config.max_iterations} | {len(self.papers)} papers | {len(self._databases_used)} databases ---[/dim]")

            messages = _compact_messages(messages, LLMClient.estimate_tokens)

            try:
                response = self.llm.chat(messages, tools=tool_schemas)
                compact_failures = 0  # Reset on success
            except Exception as e:
                self.console.print(f"[red]LLM error: {e}[/red]")
                # If context is likely too long, try compacting and retrying once
                if "too long" in str(e).lower() or "context" in str(e).lower():
                    compact_failures += 1
                    if compact_failures >= _MAX_COMPACT_FAILURES:
                        self.console.print("[red]Context compression failed repeatedly. Proceeding to synthesis.[/red]")
                        break
                    self.console.print("[yellow]Attempting context compression recovery...[/yellow]")
                    messages = _compact_messages(messages, lambda m: _MAX_SEARCH_TOKENS + 1)  # Force compress
                    try:
                        response = self.llm.chat(messages, tools=tool_schemas)
                    except Exception:
                        self.console.print("[red]Recovery failed. Proceeding to synthesis with papers found so far.[/red]")
                        break
                else:
                    break

            # No tool calls = LLM says it's done searching
            if not response.tool_calls:
                content = (response.content or "").strip()
                if content:
                    self.console.print(f"  [dim]{_truncate(content, 100)}[/dim]")
                break

            messages.append(_message_to_dict(response))

            # Execute tool calls concurrently
            tc_list = [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                for tc in response.tool_calls
            ]

            if len(tc_list) > 1:
                self.console.print(f"  [cyan]Executing {len(tc_list)} tools (partitioned)...[/cyan]")
                results = self.registry.execute_partitioned(tc_list)
            else:
                results = []
                for tc in tc_list:
                    result = self.registry.execute(tc["name"], tc["arguments"])
                    results.append((tc["id"], result))

            papers_before = len(self.papers)
            for call_id, result in results:
                tc_info = next(tc for tc in tc_list if tc["id"] == call_id)
                self._tool_call_count += 1
                self.console.print(f"  [cyan]{tc_info['name']}[/cyan] -> {_truncate(result.text, 100)}")

                for paper in result.papers:
                    self._track_paper(paper)

                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result.text,
                })
            new_papers = len(self.papers) - papers_before

            new_label = f" (+{new_papers} new)" if new_papers else " (no new)"
            self.console.print(f"  [yellow]Total: {len(self.papers)} unique papers{new_label} from {len(self._databases_used)} databases[/yellow]")

            # Inject reflection prompt (adapts strategy: broad → focused → gap-filling)
            progress = iteration / self.config.max_iterations
            if progress < 0.35:
                phase_hint = (
                    "You're in the BROAD EXPLORATION phase. Cast a wide net — use different databases, "
                    "vary your terminology, and try different search angles."
                )
            elif progress < 0.7:
                phase_hint = (
                    "You're in the FOCUSED DRILLING phase. Follow citation chains for your most-cited papers. "
                    "Narrow your queries to specific methods, sub-topics, or time periods you haven't covered."
                )
            else:
                phase_hint = (
                    "You're in the GAP-FILLING phase. Look at what's missing — specific methods, recent work, "
                    "foundational papers, or sub-topics with few results. Fill those specific gaps."
                )

            reflection = (
                f"Status: {len(self.papers)} papers from {len(self._databases_used)} databases "
                f"({', '.join(sorted(self._databases_used))}). "
                f"{phase_hint} "
                f"What topics, methods, or time periods are underrepresented? "
                f"Search to fill those gaps, or stop if you have good coverage."
            )
            messages.append({"role": "user", "content": reflection})

            # Checkpoint: save papers collected so far
            if self.papers and self._output_folder:
                try:
                    save_checkpoint(self.papers, self._output_folder)
                except Exception:
                    pass  # Non-critical — don't break the search loop
        else:
            # Loop completed without LLM stopping (hit max iterations)
            self.console.print(f"\n[yellow]Reached iteration limit ({self.config.max_iterations}). Proceeding to synthesis with {len(self.papers)} papers.[/yellow]")

    def _synthesis_phase(self, query: str) -> str:
        """Synthesize all collected papers into a categorized analysis."""
        if not self.papers:
            return "No papers were found for this query."

        # Cap synthesis corpus to prevent context overflow on smaller models
        _MAX_SYNTHESIS_PAPERS = 200
        all_papers = self.papers
        if len(all_papers) > _MAX_SYNTHESIS_PAPERS:
            # Take top papers by citation count, keeping all year ranges represented
            sorted_all = sorted(
                all_papers.values(),
                key=lambda p: (-(p.citation_count or 0), -(p.year or 0)),
            )
            synthesis_papers = {p.unique_key: p for p in sorted_all[:_MAX_SYNTHESIS_PAPERS]}
            self.console.print(
                f"  [yellow]Corpus capped: synthesizing top {_MAX_SYNTHESIS_PAPERS} of {len(all_papers)} papers "
                f"(all {len(all_papers)} saved to papers.json)[/yellow]"
            )
        else:
            synthesis_papers = all_papers

        corpus = _build_paper_corpus(synthesis_papers)

        extra_note = ""
        if len(all_papers) > len(synthesis_papers):
            extra_note = (
                f"\n\nNote: {len(all_papers)} total papers were found, but only the top "
                f"{len(synthesis_papers)} by citation count are shown above. "
                f"The full corpus is available in papers.json."
            )

        synthesis_prompt = _SYNTHESIS_PROMPT.format(
            count=len(synthesis_papers),
            db_count=len(self._databases_used),
            query=query,
            corpus=corpus + extra_note,
        )

        messages: list[dict] = [
            {"role": "system", "content": synthesis_prompt},
            {"role": "user", "content": "Categorize and synthesize these papers now."},
        ]

        try:
            response = self.llm.chat(messages)
            return response.content or ""
        except Exception as e:
            return f"Error during synthesis: {e}"

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
            paths = save_report(query, report, self.papers, self.config.output_dir, folder=self._output_folder or None)
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
