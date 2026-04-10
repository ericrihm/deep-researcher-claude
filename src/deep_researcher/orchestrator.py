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
from deep_researcher.profiles import get_profile, SearchProfile
from deep_researcher.report import get_output_folder, save_checkpoint
from deep_researcher.tools.base import Tool
from deep_researcher.tools.categorize import CategorizeTool
from deep_researcher.tools.clarify import ClarifyTool
from deep_researcher.tools.cross_analysis import CrossAnalysisTool
from deep_researcher.tools.enrichment import EnrichmentTool
from deep_researcher.tools.executive_summary import ExecutiveSummaryTool
from deep_researcher.tools.fallback_synthesis import FallbackSynthesisTool
from deep_researcher.tools.synthesize import SynthesisTool

logger = logging.getLogger("deep_researcher")


# -- Search tool factory ----------------------------------------------------

def _build_search_tools(config: Config, profile: SearchProfile) -> list[Tool]:
    """Instantiate the search tools specified by the active profile.

    Tools that require API keys are silently skipped when the key is missing,
    so the profile degrades gracefully instead of failing.
    """
    from deep_researcher.tools.scholar_search import ScholarSearchTool
    from deep_researcher.tools.scopus import ScopusSearchTool
    from deep_researcher.tools.semantic_scholar import SemanticScholarSearchTool
    from deep_researcher.tools.arxiv_search import ArxivSearchTool
    from deep_researcher.tools.dblp import DblpSearchTool
    from deep_researcher.tools.ieee_xplore import IEEEXploreSearchTool
    from deep_researcher.tools.pubmed import PubMedSearchTool
    from deep_researcher.tools.core_search import CoreSearchTool
    from deep_researcher.tools.openalex import OpenAlexSearchTool
    from deep_researcher.tools.crossref import CrossrefSearchTool

    # Map profile source names → (class, kwargs)
    source_map: dict[str, tuple[type, dict]] = {
        "scholar": (ScholarSearchTool, {}),
        "scopus": (ScopusSearchTool, {"api_key": config.scopus_api_key}),
        "semantic_scholar": (SemanticScholarSearchTool, {}),
        "arxiv": (ArxivSearchTool, {}),
        "dblp": (DblpSearchTool, {}),
        "ieee": (IEEEXploreSearchTool, {"api_key": config.ieee_api_key}),
        "pubmed": (PubMedSearchTool, {}),
        "core": (CoreSearchTool, {"api_key": config.core_api_key}),
        "openalex": (OpenAlexSearchTool, {"email": config.email}),
        "crossref": (CrossrefSearchTool, {"email": config.email}),
    }

    tools: list[Tool] = []
    for source_name in profile.search_sources:
        # Respect --no-elsevier
        if source_name == "scopus" and config.no_elsevier:
            continue

        entry = source_map.get(source_name)
        if entry is None:
            logger.warning("Unknown search source in profile: %s", source_name)
            continue

        cls, kwargs = entry
        tool = cls(**kwargs)
        tool.set_year_range(config.start_year, config.end_year)
        tools.append(tool)

    return tools


# -- Prompt selection --------------------------------------------------------

def _get_prompts(profile: SearchProfile) -> dict[str, str]:
    """Return the prompt templates for the active profile's prompt_style."""
    from deep_researcher.prompts import (
        CATEGORIZE_PROMPT,
        CATEGORY_SYNTHESIS_PROMPT,
        CROSS_CATEGORY_PROMPT,
    )

    if profile.prompt_style == "security":
        from deep_researcher.prompts import (
            SECURITY_CATEGORIZE_PROMPT,
            SECURITY_SYNTHESIS_PROMPT,
            SECURITY_CROSS_CATEGORY_PROMPT,
        )
        return {
            "categorize": SECURITY_CATEGORIZE_PROMPT,
            "synthesis": SECURITY_SYNTHESIS_PROMPT,
            "cross_analysis": SECURITY_CROSS_CATEGORY_PROMPT,
        }

    return {
        "categorize": CATEGORIZE_PROMPT,
        "synthesis": CATEGORY_SYNTHESIS_PROMPT,
        "cross_analysis": CROSS_CATEGORY_PROMPT,
    }


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

        # Resolve the search profile
        self._profile = get_profile(config.profile)
        self._prompts = _get_prompts(self._profile)

        # Build search tools from profile
        self._search_tools = _build_search_tools(config, self._profile)

        # All tools (Principle 1: tools as unit of action)
        llm = make_llm_client(config)
        self._enrichment_tool = EnrichmentTool()
        self._clarify_tool = ClarifyTool(llm=llm)
        self._categorize_tool = CategorizeTool(
            llm=llm, prompt_template=self._prompts.get("categorize"),
        )
        self._synthesize_tool = SynthesisTool(
            llm=llm, prompt_template=self._prompts.get("synthesis"),
        )
        self._cross_analysis_tool = CrossAnalysisTool(
            llm=llm, prompt_template=self._prompts.get("cross_analysis"),
        )
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

        # Show profile info
        if self._profile.name != "default":
            self.console.print(
                f"  [dim]Profile: {self._profile.name} — {self._profile.description}[/dim]"
            )

        state = PipelineState(query=query)
        self._output_folder = get_output_folder(query, self.config.output_dir)

        # Phase 1: Search
        source_names = [t.name.replace("search_", "").replace("_", " ").title()
                        for t in self._search_tools]
        source_label = " + ".join(source_names) if source_names else "no sources"
        self.console.print(f"\n[bold blue]Phase 1: Searching {source_label}...[/bold blue]")
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
        if self._profile.prompt_style != "default":
            self.console.print(
                f"  [dim]Using {self._profile.prompt_style} prompt style[/dim]"
            )
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

    def compare_research(
        self,
        query: str,
        provider_a: str,
        provider_b: str,
        providers: dict[str, dict[str, str]],
        *,
        preloaded_papers: dict[str, "Paper"] | None = None,
        output_folder: str | None = None,
    ) -> tuple[str, str]:
        """Run search+enrich once, then synthesize with two providers in parallel.

        Returns (report_a, report_b). Each provider's outputs are saved into
        subdirectories: <output_folder>/provider_a/ and <output_folder>/provider_b/.
        A top-level metadata.json records the comparison. A compare.html shows
        a side-by-side diff.

        Partial-failure tolerance: if one provider fails, the other's results
        are still saved.

        When *preloaded_papers* is provided the search + enrich phases are
        skipped entirely (used by compare_replay).  *output_folder* pins the
        output directory instead of creating a new timestamped one.
        """
        self.console.print(Panel(
            f"[bold]{query}[/bold]\n"
            f"[dim]Comparing: {provider_a} vs {provider_b}[/dim]",
            title="Deep Researcher -- Compare",
            border_style="magenta",
        ))

        if preloaded_papers is not None:
            state = PipelineState(query=query, papers=preloaded_papers)
            self._output_folder = output_folder or get_output_folder(query, self.config.output_dir)
            self.console.print(f"  [dim]Using {len(preloaded_papers)} pre-loaded papers[/dim]")
        else:
            state = PipelineState(query=query)
            self._output_folder = get_output_folder(query, self.config.output_dir)

            # Phase 1: Search (shared)
            self.console.print("\n[bold blue]Phase 1: Searching Google Scholar + Scopus...[/bold blue]")
            with self.console.status("[cyan]Searching academic databases...", spinner="dots"):
                state = self._run_search(state)

            if not state.papers:
                self.console.print("[yellow]No papers found.[/yellow]")
                return ("No papers found.", "No papers found.")

            # Phase 2: Enrich (shared)
            self.console.print(f"\n[bold blue]Phase 2: Enriching {len(state.papers)} papers...[/bold blue]")
            with self.console.status("[cyan]Fetching metadata...", spinner="dots"):
                state = self._run_enrichment(state)

        # Checkpoint
        os.makedirs(self._output_folder, exist_ok=True)
        if state.papers:
            try:
                save_checkpoint(state.papers, self._output_folder)
            except Exception:
                logger.debug("Checkpoint save failed", exc_info=True)

        # Phase 3: Synthesize with both providers in parallel
        self.console.print(f"\n[bold blue]Phase 3: Synthesizing with {provider_a} and {provider_b}...[/bold blue]")

        def _synth_with_provider(prov_name: str, prov_preset: dict) -> PipelineState:
            """Build a fresh Orchestrator with a different provider config, run synthesis."""
            from copy import deepcopy
            prov_config = deepcopy(self.config)
            prov_config.base_url = prov_preset["base_url"]
            prov_config.api_key = prov_preset.get("api_key", "")
            prov_config.model = prov_preset["default_model"]
            if prov_name == "claude":
                prov_config.provider_kind = "claude_agent"
            elif prov_name == "chatgpt":
                prov_config.provider_kind = "chatgpt_oauth"
            else:
                prov_config.provider_kind = "openai"

            prov_llm = make_llm_client(prov_config)
            # Build fresh LLM-dependent tools for this provider
            prov_orch = Orchestrator.__new__(Orchestrator)
            prov_orch.config = prov_config
            prov_orch.console = self.console
            prov_orch._cancel = self._cancel
            prov_orch._output_folder = self._output_folder
            prov_orch.last_report_paths = {}
            prov_orch._search_tool = self._search_tool
            prov_orch._scopus_tool = self._scopus_tool
            prov_orch._enrichment_tool = self._enrichment_tool
            prov_orch._clarify_tool = ClarifyTool(llm=prov_llm)
            prov_orch._categorize_tool = CategorizeTool(llm=prov_llm)
            prov_orch._synthesize_tool = SynthesisTool(llm=prov_llm)
            prov_orch._cross_analysis_tool = CrossAnalysisTool(llm=prov_llm)
            prov_orch._fallback_tool = FallbackSynthesisTool(llm=prov_llm)
            prov_orch._exec_summary_tool = ExecutiveSummaryTool(llm=prov_llm)
            return prov_orch._run_synthesis(state)

        preset_a = providers.get(provider_a, {})
        preset_b = providers.get(provider_b, {})

        state_a: PipelineState | None = None
        state_b: PipelineState | None = None
        error_a: str = ""
        error_b: str = ""

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(_synth_with_provider, provider_a, preset_a)
            future_b = pool.submit(_synth_with_provider, provider_b, preset_b)

            try:
                state_a = future_a.result(timeout=CATEGORY_SYNTHESIS_TIMEOUT * 2)
                self.console.print(f"  [green]{provider_a} synthesis complete[/green]")
            except Exception as e:
                error_a = str(e)
                self.console.print(f"  [red]{provider_a} synthesis failed: {e}[/red]")

            try:
                state_b = future_b.result(timeout=CATEGORY_SYNTHESIS_TIMEOUT * 2)
                self.console.print(f"  [green]{provider_b} synthesis complete[/green]")
            except Exception as e:
                error_b = str(e)
                self.console.print(f"  [red]{provider_b} synthesis failed: {e}[/red]")

        report_a = state_a.report if state_a else f"Synthesis failed: {error_a}"
        report_b = state_b.report if state_b else f"Synthesis failed: {error_b}"

        # Save each provider's outputs into subdirectories
        from deep_researcher.display import save_results as _save_results

        os.makedirs(self._output_folder, exist_ok=True)

        for prov_name, prov_state in [(provider_a, state_a), (provider_b, state_b)]:
            if prov_state is None:
                continue
            sub_folder = os.path.join(self._output_folder, prov_name)
            _save_results(self.console, prov_state, self.config.output_dir, sub_folder)

        # Top-level compare metadata
        import json as _json
        from datetime import datetime as _dt
        meta_path = os.path.join(self._output_folder, "metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            _json.dump({
                "query": query,
                "mode": "compare",
                "providers": [provider_a, provider_b],
                "generated": _dt.now().isoformat(),
                "total_papers": len(state.papers),
                "provider_a_success": state_a is not None,
                "provider_b_success": state_b is not None,
            }, f, indent=2)

        # Phase 4: Comparison analysis
        if state_a and state_b:
            self.console.print("\n[bold blue]Phase 4: Comparing outputs...[/bold blue]")
            comparison_text = self._run_comparison(
                query, report_a, report_b,
                provider_a, provider_b,
                len(state.papers),
                providers,
            )
            if comparison_text:
                from deep_researcher.html_compare import build_compare_html
                compare_path = os.path.join(self._output_folder, "compare.html")
                try:
                    html_doc = build_compare_html(
                        query, report_a, report_b,
                        provider_a, provider_b,
                        comparison_text,
                    )
                    with open(compare_path, "w", encoding="utf-8") as f:
                        f.write(html_doc)
                    self.console.print(f"  [green]Comparison saved:[/green] [blue]{compare_path}[/blue]")
                except Exception as e:
                    logger.warning("Failed to write compare.html: %s", e)

        self.last_report_paths = {
            "compare_folder": self._output_folder,
            "html": os.path.join(self._output_folder, "compare.html"),
        }
        return (report_a, report_b)

    def _run_comparison(
        self,
        query: str,
        report_a: str,
        report_b: str,
        provider_a: str,
        provider_b: str,
        paper_count: int,
        providers: dict[str, dict[str, str]],
    ) -> str:
        """Run the comparison analysis using the more capable provider."""
        capability_order = ["claude", "anthropic", "openai", "openrouter",
                            "chatgpt", "groq", "deepseek", "together",
                            "ollama", "lmstudio"]
        best_provider = provider_a
        for prov in capability_order:
            if prov in (provider_a, provider_b):
                best_provider = prov
                break

        from copy import deepcopy
        comp_config = deepcopy(self.config)
        best_preset = providers.get(best_provider, {})
        if best_preset:
            comp_config.base_url = best_preset.get("base_url", comp_config.base_url)
            comp_config.api_key = best_preset.get("api_key", comp_config.api_key)
            comp_config.model = best_preset.get("default_model", comp_config.model)
        if best_provider == "claude":
            comp_config.provider_kind = "claude_agent"
        elif best_provider == "chatgpt":
            comp_config.provider_kind = "chatgpt_oauth"
        else:
            comp_config.provider_kind = "openai"

        comp_llm = make_llm_client(comp_config)

        from deep_researcher.tools.comparison import ComparisonTool
        tool = ComparisonTool(llm=comp_llm)
        result = tool.safe_execute(
            query=query,
            report_a=report_a,
            report_b=report_b,
            provider_a=provider_a,
            provider_b=provider_b,
            paper_count=paper_count,
        )
        if result.text:
            self.console.print(f"  [green]Comparison analysis complete (via {best_provider})[/green]")
        else:
            self.console.print("  [yellow]Comparison analysis produced no output[/yellow]")
        return result.text

    def compare_replay(
        self,
        folder: str,
        providers: dict[str, dict[str, str]],
    ) -> tuple[str, str]:
        """Re-run dual-provider synthesis on a compare folder's papers.json.

        Loads *folder*/metadata.json (must have mode=compare) and
        *folder*/papers.json, then delegates to compare_research() with the
        pre-loaded corpus, skipping search+enrich entirely.
        """
        meta_path = os.path.join(folder, "metadata.json")
        if not os.path.exists(meta_path):
            raise FileNotFoundError(f"No metadata.json in {folder}")
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("mode") != "compare":
            raise ValueError(f"Not a compare folder: {folder}")

        prov_list = meta.get("providers", [])
        if len(prov_list) != 2:
            raise ValueError(f"Expected 2 providers in metadata, got {len(prov_list)}")

        query = meta.get("query", "replay")
        provider_a, provider_b = prov_list

        papers_path = os.path.join(folder, "papers.json")
        if not os.path.exists(papers_path):
            raise FileNotFoundError(f"No papers.json in {folder}")
        with open(papers_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        known = set(Paper.__dataclass_fields__.keys())
        rehydrated = {
            p.unique_key: p
            for d in raw if isinstance(d, dict)
            for p in [Paper(**{k: v for k, v in d.items() if k in known})]
        }

        return self.compare_research(
            query, provider_a, provider_b, providers,
            preloaded_papers=rehydrated,
            output_folder=folder,
        )

    # ------------------------------------------------------------------
    # Phase implementations (each calls tools, returns new state)
    # ------------------------------------------------------------------

    def _run_search(self, state: PipelineState) -> PipelineState:
        """Phase 1: Search all profile sources in parallel.

        Fan out across a thread pool sized to the profile's max_search_workers.
        Wall-clock = max(all sources) instead of sum(all sources).
        """
        if not self._search_tools:
            self.console.print("  [yellow]No search sources configured[/yellow]")
            return state

        workers = min(len(self._search_tools), self._profile.max_search_workers)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_name: dict[concurrent.futures.Future, str] = {}
            for tool in self._search_tools:
                future = pool.submit(
                    tool.safe_execute,
                    query=state.query,
                    cancel=self._cancel,
                )
                future_to_name[future] = tool.name

            papers: dict[str, Paper] = {}
            source_counts: dict[str, int] = {}

            for future in concurrent.futures.as_completed(future_to_name):
                tool_name = future_to_name[future]
                display_name = tool_name.replace("search_", "").replace("_", " ").title()
                try:
                    result = future.result()
                    new_count = 0
                    for paper in result.papers:
                        key = paper.unique_key
                        if key not in papers:
                            papers[key] = paper
                            new_count += 1
                        else:
                            papers[key].merge(paper)
                    source_counts[display_name] = new_count
                except Exception as e:
                    logger.debug("%s search failed: %s", tool_name, e)
                    source_counts[display_name] = 0

        # Report per-source counts
        parts = []
        for name, count in source_counts.items():
            if count > 0:
                parts.append(f"{count} from {name}")
        total = len(papers)
        if parts:
            self.console.print(f"  [green]Found {total} unique papers ({', '.join(parts)})[/green]")
        else:
            self.console.print(f"  [green]Found {total} papers[/green]")

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
        report = _assemble_report(state, self._profile, self._search_tools)
        return state.evolve(report=report)


# ------------------------------------------------------------------
# Report assembly (pure function, no side effects)
# ------------------------------------------------------------------

def _assemble_report(
    state: PipelineState,
    profile: SearchProfile,
    search_tools: list[Tool],
) -> str:
    """Assemble the final report programmatically."""
    papers = state.synthesis_papers
    categories = state.categories or {}
    sections = state.category_sections
    cross_section = state.cross_section

    years = [p.year for p in papers if p.year]
    yr_range = f"{min(years)}-{max(years)}" if years else "unknown"
    total = len(state.papers)

    # Build source list for the coverage line
    source_names = [t.name.replace("search_", "").replace("_", " ").title()
                    for t in search_tools]
    source_label = ", ".join(source_names) if source_names else "Google Scholar"

    has_doi = sum(1 for p in papers if p.doi)

    profile_note = ""
    if profile.name != "default":
        profile_note = f" Profile: {profile.name}."

    parts = [
        f"### {state.query}\n",
        f"#### Coverage",
        f"{total} papers found via {source_label}, enriched via OpenAlex. "
        f"Years {yr_range}. {has_doi} with DOIs.{profile_note}\n",
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
