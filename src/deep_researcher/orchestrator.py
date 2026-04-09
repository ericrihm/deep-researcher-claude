# src/deep_researcher/orchestrator.py
"""Research pipeline orchestrator.

Pure orchestration: calls tools, manages PipelineState flow, provides
layered error recovery. Never makes raw API/library calls (Principle 2).
Display and persistence are delegated to display.py and report.py.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import threading

from rich.console import Console
from rich.panel import Panel

from deep_researcher.config import Config
from deep_researcher.constants import (
    CATEGORY_SYNTHESIS_TIMEOUT,
    MAX_SYNTHESIS_CONCURRENCY,
    MAX_SYNTHESIS_PAPERS,
)
from deep_researcher.display import print_summary, save_results
from deep_researcher.llm_factory import make_llm_client
from deep_researcher.models import Paper, PipelineState
from deep_researcher.report import get_output_folder, save_checkpoint
from deep_researcher.tools.categorize import CategorizeTool
from deep_researcher.tools.clarify import ClarifyTool
from deep_researcher.tools.cross_analysis import CrossAnalysisTool
from deep_researcher.tools.enrichment import EnrichmentTool
from deep_researcher.tools.executive_summary import ExecutiveSummaryTool
from deep_researcher.tools.fallback_synthesis import FallbackSynthesisTool
from deep_researcher.tools.scholar_search import ScholarSearchTool
from deep_researcher.tools.scopus import ScopusSearchTool
from deep_researcher.tools.synthesize import SynthesisTool

logger = logging.getLogger("deep_researcher")


class Orchestrator:
    """Pipeline orchestrator — calls tools only, never raw APIs.

    Each phase:
    1. Calls a tool via safe_execute() (validation + error wrapping)
    2. Handles errors with recovery (retry -> fallback -> degrade)
    3. Returns new PipelineState (never mutates input)
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.console = Console()
        self._cancel = threading.Event()
        self._output_folder: str = ""
        self.last_report_paths: dict[str, str] = {}

        # All tools (Principle 1: tools as unit of action)
        llm = make_llm_client(config)
        self._search_tool = ScholarSearchTool()
        self._search_tool.set_year_range(config.start_year, config.end_year)

        # Scopus (Elsevier) runs as a second search pass alongside Scholar.
        # config.scopus_api_key is resolved by __main__'s _setup_elsevier()
        # before Orchestrator is constructed, so it always holds either the
        # user's key or the bundled default (unless no_elsevier is True, in
        # which case _run_search skips it entirely).
        self._scopus_tool = ScopusSearchTool(api_key=config.scopus_api_key)
        self._scopus_tool.set_year_range(config.start_year, config.end_year)

        self._enrichment_tool = EnrichmentTool()
        self._clarify_tool = ClarifyTool(llm=llm)
        self._categorize_tool = CategorizeTool(llm=llm)
        self._synthesize_tool = SynthesisTool(llm=llm)
        self._cross_analysis_tool = CrossAnalysisTool(llm=llm)
        self._fallback_tool = FallbackSynthesisTool(llm=llm)
        self._exec_summary_tool = ExecutiveSummaryTool(llm=llm)

    def cancel(self) -> None:
        """Signal the orchestrator to stop gracefully."""
        self._cancel.set()

    def clarify(self, query: str) -> str:
        """Ask clarifying questions and return an enhanced query."""
        self.console.print("\n[bold]Generating clarifying questions...[/bold]\n")
        result = self._clarify_tool.safe_execute(query=query)
        questions = result.text.strip()

        if not questions:
            self.console.print("[yellow]Could not generate questions. Proceeding with original query.[/yellow]")
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

        enhanced = f"{query}\n\nAdditional context from the researcher:\n"
        enhanced += "\n".join(f"- {a}" for a in answers)
        self.console.print(f"\n[green]Enhanced query ready.[/green]")
        return enhanced

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    def research(self, query: str) -> str:
        """Run the full research pipeline.

        State flows immutably through phases (Principle 3):
        search -> enrich -> synthesize -> report
        """
        self.console.print(Panel(
            f"[bold]{query}[/bold]",
            title="Deep Researcher",
            border_style="blue",
        ))

        state = PipelineState(query=query)
        self._output_folder = get_output_folder(query, self.config.output_dir)

        # Phase 1: Search
        self.console.print("\n[bold blue]Phase 1: Searching Google Scholar + Scopus...[/bold blue]")
        with self.console.status("[cyan]Searching academic databases…", spinner="dots"):
            state = self._run_search(state)

        if not state.papers:
            self.console.print("[yellow]No papers found.[/yellow]")
            return "No papers were found for this query."

        # Phase 2: Enrich
        self.console.print(f"\n[bold blue]Phase 2: Enriching {len(state.papers)} papers...[/bold blue]")
        with self.console.status("[cyan]Fetching metadata from OpenAlex/CrossRef/Unpaywall…", spinner="dots"):
            state = self._run_enrichment(state)

        # Checkpoint
        if state.papers and self._output_folder:
            try:
                save_checkpoint(state.papers, self._output_folder)
            except Exception:
                logger.debug("Checkpoint save failed", exc_info=True)

        # Phase 3: Synthesize
        self.console.print(f"\n[bold blue]Phase 3: Synthesizing {len(state.papers)} papers...[/bold blue]")
        with self.console.status("[cyan]Asking the model to read and write…", spinner="dots"):
            state = self._run_synthesis(state)

        print_summary(self.console, state)
        paths = save_results(self.console, state, self.config.output_dir, self._output_folder or None)
        if paths:
            self.last_report_paths = paths
        return state.report

    def replay(self, folder: str) -> str:
        """Re-run synthesis on an existing run's papers.json.

        Skips search + enrich entirely. Loads papers.json + metadata.json,
        reconstructs PipelineState, calls _run_synthesis(), saves versioned
        outputs into the same folder. Returns the report markdown.
        """
        if not os.path.isdir(folder):
            raise FileNotFoundError(f"Replay folder does not exist: {folder}")
        papers_path = os.path.join(folder, "papers.json")
        if not os.path.exists(papers_path):
            raise FileNotFoundError(
                f"No papers.json in {folder} — is this a deep-researcher output folder?"
            )
        try:
            with open(papers_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Malformed papers.json in {folder}: {e}") from e

        # Defensive rehydration — drop unknown keys so a newer-version
        # corpus can still be replayed on an older install.
        known = set(Paper.__dataclass_fields__.keys())
        rehydrated: list[Paper] = []
        for d in raw:
            if not isinstance(d, dict):
                continue
            rehydrated.append(Paper(**{k: v for k, v in d.items() if k in known}))

        # Pick up the original query from metadata.json when available.
        query = ""
        meta_path = os.path.join(folder, "metadata.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                query = (meta.get("query") or "").strip()
            except Exception:
                self.console.print(
                    f"  [yellow]Could not read metadata.json in {folder} — "
                    f"using folder name as query.[/yellow]"
                )
        if not query:
            from deep_researcher.report import _make_slug
            base = os.path.basename(os.path.normpath(folder)) or "replay"
            query = _make_slug(base).replace("-", " ") or "replay"

        self.console.print(Panel(
            f"[bold]Replay:[/bold] {query}\n[dim]Folder:[/dim] {folder}",
            title="Deep Researcher — Replay",
            border_style="blue",
        ))

        state = PipelineState(query=query, papers={p.unique_key: p for p in rehydrated})
        self._output_folder = folder

        self.console.print(f"\n[bold blue]Re-running synthesis on {len(state.papers)} papers...[/bold blue]")
        with self.console.status("[cyan]Asking the model to read and write…", spinner="dots"):
            state = self._run_synthesis(state)

        print_summary(self.console, state)
        paths = save_results(self.console, state, self.config.output_dir, folder)
        if paths:
            self.last_report_paths = paths
        return state.report

    # ------------------------------------------------------------------
    # Phase implementations (each calls tools, returns new state)
    # ------------------------------------------------------------------

    def _run_search(self, state: PipelineState) -> PipelineState:
        """Phase 1: Search Google Scholar and Scopus in parallel.

        The two searches are independent HTTP calls so they fan out on a
        small thread pool. Wall-clock for the search phase is now max(scholar,
        scopus) instead of scholar + scopus, which on a typical run shaves
        ~10-30s off the slowest source.
        """
        scopus_enabled = not self.config.no_elsevier

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            scholar_future = pool.submit(
                self._search_tool.safe_execute,
                query=state.query,
                cancel=self._cancel,
            )
            scopus_future = (
                pool.submit(
                    self._scopus_tool.safe_execute,
                    query=state.query,
                    cancel=self._cancel,
                )
                if scopus_enabled
                else None
            )

            try:
                scholar_result = scholar_future.result()
            except Exception as e:
                logger.debug("Scholar search failed: %s", e)
                from deep_researcher.models import ToolResult
                scholar_result = ToolResult(text=f"Scholar error: {e}", papers=[])

            scopus_result = None
            if scopus_future is not None:
                try:
                    scopus_result = scopus_future.result()
                except Exception as e:
                    logger.debug("Scopus search failed: %s", e)

        papers: dict[str, Paper] = {}
        for paper in scholar_result.papers:
            key = paper.unique_key
            if key not in papers:
                papers[key] = paper
        scholar_count = len(papers)

        new_from_scopus = 0
        if scopus_result is not None:
            for paper in scopus_result.papers:
                key = paper.unique_key
                if key not in papers:
                    papers[key] = paper
                    new_from_scopus += 1

        if new_from_scopus:
            self.console.print(
                f"  [green]Found {scholar_count} papers on Scholar + "
                f"{new_from_scopus} new on Scopus[/green]"
            )
        else:
            self.console.print(f"  [green]Found {scholar_count} papers[/green]")

        return state.evolve(papers=papers)

    def _run_enrichment(self, state: PipelineState) -> PipelineState:
        """Phase 2: Enrich papers via tool (concurrent HTTP)."""
        total = len(state.papers)

        def _on_enrichment_progress(msg: str, current: int, _total: int) -> None:
            if current % 10 == 0 or current == total:
                self.console.print(f"  [dim]{msg}[/dim]")

        result = self._enrichment_tool.safe_execute(
            on_progress=_on_enrichment_progress,
            papers=list(state.papers.values()),
            email=self.config.email,
            cancel=self._cancel,
        )

        # Rebuild papers dict preserving original keys (match old behavior:
        # enrichment adds metadata but doesn't change which papers exist)
        original_keys = list(state.papers.keys())
        enriched: dict[str, Paper] = {}
        for i, paper in enumerate(result.papers):
            if i < len(original_keys):
                enriched[original_keys[i]] = paper
            else:
                enriched[paper.unique_key] = paper

        self.console.print(f"  [green]{result.text}[/green]")
        return state.evolve(papers=enriched)

    def _run_synthesis(self, state: PipelineState) -> PipelineState:
        """Phase 3: Multi-step synthesis with layered error recovery.

        Recovery layers (Principle 4):
        1. Per-category timeout/skip
        2. Categorization failure -> fallback tool
        3. All categories fail -> fallback tool
        """
        def _sort_key(p: Paper) -> tuple:
            return (-(p.citation_count or 0), -(p.year or 0))

        all_papers = state.papers
        if len(all_papers) > MAX_SYNTHESIS_PAPERS:
            sorted_all = sorted(all_papers.values(), key=_sort_key)
            synthesis_papers = sorted_all[:MAX_SYNTHESIS_PAPERS]
            self.console.print(
                f"  [yellow]Corpus capped: synthesizing top {MAX_SYNTHESIS_PAPERS} of "
                f"{len(all_papers)} papers (all saved to papers.json)[/yellow]"
            )
        else:
            synthesis_papers = sorted(all_papers.values(), key=_sort_key)

        state = state.evolve(synthesis_papers=synthesis_papers)

        # Step 1: Categorize (via tool)
        self.console.print("  [cyan]Step 1/3: Categorizing papers...[/cyan]")
        cat_result = self._categorize_tool.safe_execute(
            papers=synthesis_papers,
            query=state.query,
        )
        categories = cat_result.data

        if not categories:
            self.console.print("  [yellow]Categorization failed — falling back to single-pass synthesis[/yellow]")
            fb_result = self._fallback_tool.safe_execute(papers=synthesis_papers, query=state.query)
            return state.evolve(report=fb_result.text)

        state = state.evolve(categories=categories)
        self.console.print(f"  [green]Found {len(categories)} categories[/green]")
        for name, indices in categories.items():
            self.console.print(f"    {name}: {len(indices)} papers")

        # Step 2: Per-category synthesis (concurrent, Claude Code parallel tool pattern)
        self.console.print("  [cyan]Step 2/3: Synthesizing per category...[/cyan]")

        # Build work items preserving category order
        work_items: list[tuple[str, list[tuple[int, Paper]]]] = []
        for cat_name, paper_indices in categories.items():
            cat_indexed = [(i, synthesis_papers[i]) for i in paper_indices if i < len(synthesis_papers)]
            if cat_indexed:
                work_items.append((cat_name, cat_indexed))
                self.console.print(f"    [cyan]{cat_name}[/cyan] ({len(cat_indexed)} papers)")

        # Submit all categories concurrently (isConcurrencySafe=True)
        results_by_name: dict[str, str] = {}
        exec_summary_text = ""
        _EXEC_SENTINEL = "__exec_summary__"
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_SYNTHESIS_CONCURRENCY) as pool:
            future_to_name: dict[concurrent.futures.Future, str] = {}
            for cat_name, cat_indexed in work_items:
                if self._cancel.is_set():
                    break
                future = pool.submit(
                    self._synthesize_tool.safe_execute,
                    indexed_papers=cat_indexed,
                    query=state.query,
                    category_name=cat_name,
                )
                future_to_name[future] = cat_name

            # Submit the executive summary in parallel with the per-category
            # calls. Running here (and not after the pool drains) keeps the
            # wall-clock impact at ~0 as long as MAX_SYNTHESIS_CONCURRENCY >
            # len(categories), which is the common case.
            if not self._cancel.is_set():
                exec_future = pool.submit(
                    self._exec_summary_tool.safe_execute,
                    query=state.query,
                    synthesis_papers=synthesis_papers,
                    categories=categories,
                )
                future_to_name[exec_future] = _EXEC_SENTINEL

            for future in concurrent.futures.as_completed(future_to_name):
                if self._cancel.is_set():
                    self.console.print("  [yellow]Synthesis cancelled.[/yellow]")
                    break
                cat_name = future_to_name[future]
                if cat_name == _EXEC_SENTINEL:
                    try:
                        result = future.result(timeout=CATEGORY_SYNTHESIS_TIMEOUT)
                        exec_summary_text = result.text or ""
                    except Exception as e:
                        logger.debug("Executive summary failed: %s", e)
                        exec_summary_text = ""
                    continue
                try:
                    result = future.result(timeout=CATEGORY_SYNTHESIS_TIMEOUT)
                    if not result.text.startswith("Synthesis failed"):
                        results_by_name[cat_name] = result.text
                        self.console.print(f"    [green]{cat_name} done[/green]")
                    else:
                        self.console.print(f"    [red]{result.text}[/red]")
                except concurrent.futures.TimeoutError:
                    self.console.print(f"    [red]{cat_name}: timed out[/red]")
                except Exception as e:
                    self.console.print(f"    [red]{cat_name}: {e}[/red]")

        # Reassemble in original category order
        category_sections: list[tuple[str, str]] = [
            (name, results_by_name[name])
            for name, _ in work_items
            if name in results_by_name
        ]

        if not category_sections:
            self.console.print("  [yellow]All categories failed — falling back[/yellow]")
            fb_result = self._fallback_tool.safe_execute(papers=synthesis_papers, query=state.query)
            return state.evolve(report=fb_result.text, exec_summary=exec_summary_text)

        state = state.evolve(category_sections=category_sections)

        # Step 3: Cross-category analysis (via tool)
        self.console.print("  [cyan]Step 3/3: Cross-category analysis...[/cyan]")
        cross_result = self._cross_analysis_tool.safe_execute(
            sections=category_sections,
            query=state.query,
        )
        state = state.evolve(cross_section=cross_result.text)

        # Step 4: Assemble report (programmatic — not LLM)
        state = state.evolve(exec_summary=exec_summary_text)
        report = _assemble_report(state)
        return state.evolve(report=report)


# ------------------------------------------------------------------
# Report assembly (pure function, no side effects)
# ------------------------------------------------------------------

def _assemble_report(state: PipelineState) -> str:
    """Assemble the final report programmatically."""
    papers = state.synthesis_papers
    categories = state.categories or {}
    sections = state.category_sections
    cross_section = state.cross_section

    years = [p.year for p in papers if p.year]
    yr_range = f"{min(years)}-{max(years)}" if years else "unknown"
    total = len(state.papers)

    has_doi = sum(1 for p in papers if p.doi)
    parts = [
        f"### {state.query}\n",
        f"#### Coverage",
        f"{total} papers found via Google Scholar, enriched via OpenAlex. "
        f"Years {yr_range}. {has_doi} with DOIs.\n",
        "#### Categories\n",
    ]

    for cat_name, content in sections:
        cat_indices = categories.get(cat_name, [])
        parts.append(f"##### {cat_name} ({len(cat_indices)} papers)\n")
        parts.append(content)
        parts.append("")

    parts.append(cross_section)
    parts.append("")

    # References (generated programmatically — never by LLM)
    parts.append("#### References\n")
    for i, p in enumerate(papers, 1):
        author = p.authors[0] if p.authors else "Unknown"
        if len(p.authors) > 1:
            author += " et al."
        year = p.year or "n.d."
        journal = f" *{p.journal}*." if p.journal else ""
        doi = f" DOI: {p.doi}" if p.doi else ""
        oa = f" [Open Access]({p.open_access_url})" if p.open_access_url else ""
        parts.append(f"[{i}] {author} ({year}). {p.title}.{journal}{doi}{oa}")

    return "\n".join(parts)
