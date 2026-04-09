"""Display and output helpers.

Separated from orchestrator so orchestration logic
has no presentation concerns (Principle 7).
"""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from deep_researcher.models import PipelineState
from deep_researcher.report import save_report


def print_summary(console: Console, state: PipelineState) -> None:
    console.print(f"\n[green]Research complete.[/green]")
    table = Table(title="Research Summary", show_header=False, border_style="green")
    table.add_row("Papers found", str(len(state.papers)))

    has_doi = sum(1 for p in state.papers.values() if p.doi)
    table.add_row("With DOIs", f"{has_doi}/{len(state.papers)}")

    has_abstract = sum(1 for p in state.papers.values() if p.abstract and len(p.abstract) > 200)
    table.add_row("Full abstracts", f"{has_abstract}/{len(state.papers)}")

    years = [p.year for p in state.papers.values() if p.year]
    if years:
        table.add_row("Year range", f"{min(years)}-{max(years)}")

    oa_count = sum(1 for p in state.papers.values() if p.open_access_url)
    if oa_count:
        table.add_row("Open access", f"{oa_count}/{len(state.papers)}")

    console.print(table)


def save_results(
    console: Console,
    state: PipelineState,
    output_dir: str,
    folder: str | None = None,
) -> dict[str, str] | None:
    if not state.report.strip():
        return None
    try:
        paths = save_report(
            state.query, state.report, state.papers,
            output_dir, folder=folder,
            synthesis_papers=state.synthesis_papers or None,
        )
        console.print(f"\n[green bold]Files saved:[/green bold]")
        for label, path in paths.items():
            console.print(f"  {label}: [blue]{path}[/blue]")
        return paths
    except Exception as e:
        console.print(f"[red]Error saving report: {e}[/red]")
        return None
