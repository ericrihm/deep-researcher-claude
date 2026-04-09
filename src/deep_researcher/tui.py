"""Interactive TUI for users who don't want to memorize CLI flags.

Entered when `deep-researcher` is run with no positional query argument.
The positional-query + --flags path is preserved untouched for power
users and scripts — this module is a fallback, not a replacement.

Design goal: minimize the gap between "I want to research X" and
"I have a report". No shell quoting, no placeholder strings that can
be mistaken for real input, no env-var shenanigans.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from deep_researcher.auth import detect_claude_oauth_credentials
from deep_researcher.config import Config
from deep_researcher.state import load_state, save_state


def _settings_table(config: Config, provider_name: str, query: str) -> Table:
    """Render current settings with inline hotkeys.

    The hotkey lives in the same row as the field it edits, so the user
    never has to glance away from a value to find its number.
    """
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column("hotkey", style="cyan", no_wrap=True, justify="right")
    t.add_column("label", style="bold", no_wrap=True)
    t.add_column("value", overflow="fold")
    short_q = (query[:70] + "…") if len(query) > 72 else (query or "[dim](not set)[/dim]")
    if config.start_year or config.end_year:
        yr = f"{config.start_year or '…'} – {config.end_year or '…'}"
    else:
        yr = "[dim]any[/dim]"
    t.add_row("[1]", "Research question", short_q)
    t.add_row("[2]", "Provider", provider_name or "[dim](default)[/dim]")
    t.add_row("[3]", "Model", config.model or "[dim](default)[/dim]")
    t.add_row("[4]", "Year range", yr)
    t.add_row("[5]", "Email", config.email or "[dim](none)[/dim]")
    t.add_row("[6]", "Output folder", config.output_dir)
    return t


def list_recent_runs(output_dir: str, limit: int = 10) -> list[dict]:
    """Return up to `limit` most recent valid output folders.

    Each dict: {'path', 'mtime', 'query', 'paper_count'}.
    Skips folders without a papers.json. Reads metadata.json for
    query + paper_count; falls back to folder slug + json length if
    metadata is missing.
    """
    if not os.path.isdir(output_dir):
        return []
    entries: list[dict] = []
    for name in os.listdir(output_dir):
        folder = os.path.join(output_dir, name)
        if not os.path.isdir(folder):
            continue
        papers_path = os.path.join(folder, "papers.json")
        if not os.path.exists(papers_path):
            continue
        try:
            mtime = os.path.getmtime(folder)
        except OSError:
            continue
        query = ""
        paper_count = 0
        meta_path = os.path.join(folder, "metadata.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                query = (meta.get("query") or "").strip()
                paper_count = int(meta.get("total_papers") or 0)
            except Exception:
                pass
        if not paper_count:
            try:
                with open(papers_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    paper_count = len(data)
            except Exception:
                pass
        if not query:
            # Derive from folder name; strip timestamp prefix if present
            base = name
            parts = base.split("-", 4)
            if len(parts) >= 5 and parts[0].isdigit():
                base = parts[-1]
            query = base.replace("-", " ") or "(unknown)"
        entries.append({
            "path": folder,
            "mtime": mtime,
            "query": query,
            "paper_count": paper_count,
        })
    entries.sort(key=lambda e: -e["mtime"])
    return entries[:limit]


def _replay_submenu(console: Console, output_dir: str) -> Optional[str]:
    """Render the recent-runs list and return the chosen folder path, or None."""
    from datetime import datetime
    runs = list_recent_runs(output_dir, limit=10)
    if not runs:
        console.print(
            f"\n[yellow]No past runs found in {output_dir}.[/yellow] "
            f"[dim]Press Enter to go back.[/dim]"
        )
        try:
            Prompt.ask("  >", default="", show_default=False)
        except (KeyboardInterrupt, EOFError):
            pass
        return None

    console.print("\n[bold]Recent runs[/bold] [dim](newest first)[/dim]")
    for i, r in enumerate(runs, 1):
        ts = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M")
        q = r["query"]
        if len(q) > 48:
            q = q[:47] + "…"
        console.print(
            f"  [cyan]{i:>2}[/cyan]  [dim]{ts}[/dim]  "
            f"{q}  [dim]({r['paper_count']} papers)[/dim]"
        )
    console.print("  [dim]Enter a number, or b to go back.[/dim]")
    try:
        raw = Prompt.ask("  >", default="b", show_default=False).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return None
    if raw in ("", "b", "back"):
        return None
    try:
        idx = int(raw)
    except ValueError:
        console.print("[yellow]Not a number — going back.[/yellow]")
        return None
    if not (1 <= idx <= len(runs)):
        console.print("[yellow]Out of range — going back.[/yellow]")
        return None
    return runs[idx - 1]["path"]


def _compare_submenu(
    console: Console,
    providers: dict,
    current_provider: str,
) -> tuple[str, str] | None:
    """Ask the user to pick two providers for comparison. Returns (a, b) or None."""
    keys = [k for k in providers.keys() if k not in ("chatgpt",)]
    console.print("\n[bold]Pick two providers to compare[/bold]")
    for i, k in enumerate(keys, 1):
        console.print(f"  [cyan]{i}[/cyan]. {k}")
    console.print("  [dim]Enter two numbers separated by space, or b to go back.[/dim]")
    try:
        raw = Prompt.ask("  >", default="b", show_default=False).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return None
    if raw in ("", "b", "back"):
        return None
    parts = raw.split()
    if len(parts) != 2:
        console.print("[yellow]Enter exactly two numbers.[/yellow]")
        return None
    try:
        idx_a, idx_b = int(parts[0]), int(parts[1])
    except ValueError:
        console.print("[yellow]Not valid numbers.[/yellow]")
        return None
    if not (1 <= idx_a <= len(keys)) or not (1 <= idx_b <= len(keys)):
        console.print("[yellow]Out of range.[/yellow]")
        return None
    if idx_a == idx_b:
        console.print("[yellow]Pick two different providers.[/yellow]")
        return None
    return (keys[idx_a - 1], keys[idx_b - 1])


def _ask_question(console: Console, current: str) -> str:
    console.print(
        "\n[bold]What would you like to research?[/bold]  "
        "[dim](a sentence or short paragraph works best)[/dim]"
    )
    if current:
        console.print(f"[dim]Press Enter to reuse your last query:[/dim] [cyan]{current}[/cyan]")
    answer = Prompt.ask("  >", default=current or None, show_default=False)
    return (answer or "").strip()


def _pick_provider(console: Console, providers: dict, current: str) -> str:
    console.print("\n[bold]Choose a provider[/bold]")
    keys = list(providers.keys())
    for i, k in enumerate(keys, 1):
        marker = "[green]✓[/green] " if k == current else "  "
        console.print(f"  {marker}[cyan]{i}[/cyan]. {k}")
    console.print("  [dim]Enter to keep current[/dim]")
    raw = Prompt.ask("  >", default="", show_default=False).strip()
    if not raw:
        return current
    try:
        idx = int(raw)
        if 1 <= idx <= len(keys):
            return keys[idx - 1]
    except ValueError:
        if raw in providers:
            return raw
    console.print("[yellow]Not a valid choice — keeping current.[/yellow]")
    return current


def _ask_year_range(console: Console, start: Optional[int], end: Optional[int]) -> tuple[Optional[int], Optional[int]]:
    console.print(
        "\n[bold]Year range[/bold] [dim](press Enter to skip either bound)[/dim]"
    )
    s_raw = Prompt.ask(
        "  Start year",
        default=str(start) if start else "",
        show_default=bool(start),
    ).strip()
    e_raw = Prompt.ask(
        "  End year",
        default=str(end) if end else "",
        show_default=bool(end),
    ).strip()
    new_s = int(s_raw) if s_raw.isdigit() else None
    new_e = int(e_raw) if e_raw.isdigit() else None
    return new_s, new_e


def _ask_model(console: Console, current: str) -> str:
    console.print("\n[bold]Model[/bold] [dim](press Enter to keep current)[/dim]")
    return Prompt.ask("  >", default=current, show_default=True).strip() or current


def _ask_email(console: Console, current: str) -> str:
    console.print(
        "\n[bold]Email[/bold] [dim](optional — used for polite API access to OpenAlex/CrossRef/Unpaywall)[/dim]"
    )
    return Prompt.ask("  >", default=current, show_default=bool(current)).strip()


def _ask_output_dir(console: Console, current: str) -> str:
    console.print("\n[bold]Output folder[/bold]")
    return Prompt.ask("  >", default=current, show_default=True).strip() or current


def run(console: Console, providers: dict) -> Optional[tuple]:
    """Run the interactive TUI.

    Returns (query, config, provider_name) when the user chooses to start
    research, or None if they quit.
    """
    state = load_state()

    # Seed defaults from saved state
    query: str = state.get("last_query", "") or ""
    provider_name: str = state.get("last_provider", "") or ""

    # First-time novices who followed our README and ran `claude login`
    # should not have to pick from a list of 9 providers. If we see
    # OAuth credentials on disk and no prior choice, default to claude.
    if not provider_name and detect_claude_oauth_credentials():
        provider_name = "claude"

    # Build a Config pre-filled from state. Config.__post_init__ handles
    # env/config.json fallbacks for anything we don't explicitly set.
    config = Config()
    if provider_name and provider_name in providers:
        preset = providers[provider_name]
        if preset["base_url"]:
            config.base_url = preset["base_url"]
        if preset["api_key"]:
            config.api_key = preset["api_key"]
        config.model = preset["default_model"]
        if provider_name == "claude":
            config.provider_kind = "claude_agent"
        if provider_name in ("ollama", "lmstudio"):
            config.timeout = 300
    if state.get("last_model"):
        config.model = state["last_model"]
    if state.get("last_start_year") is not None:
        config.start_year = state["last_start_year"]
    if state.get("last_end_year") is not None:
        config.end_year = state["last_end_year"]
    if state.get("last_email"):
        config.email = state["last_email"]
    if state.get("last_output_dir"):
        config.output_dir = state["last_output_dir"]

    console.print(Panel(
        "[bold]Deep Researcher[/bold]\n"
        "Type a research question and I'll search Google Scholar, read ~100 papers, "
        "and write you a literature review.\n"
        "[dim]Tip: power users can run `deep-researcher \"your question\" --provider claude` directly.[/dim]",
        border_style="blue",
    ))

    # Ask the query up front — this is what novices expect: type, get
    # asked, answer, go. The settings menu comes after so they can
    # review / tweak before committing.
    #
    # Catch Ctrl-C / EOF at every prompt so a novice bailing out of
    # the TUI sees a clean "Bye." instead of a Rich traceback dumped
    # into their PowerShell window.
    try:
        query = _ask_question(console, query)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Bye.[/dim]")
        return None

    while True:
        console.print()
        console.print(Panel(
            _settings_table(config, provider_name, query),
            title="Current settings",
            border_style="cyan",
        ))
        console.print(
            "  [green bold]Enter[/green bold] to start  •  "
            "[cyan]1-6[/cyan] edit a field  •  "
            "[cyan]c[/cyan] compare  •  "
            "[cyan]r[/cyan] replay past run  •  "
            "[red]q[/red] quit"
        )
        try:
            choice = Prompt.ask("  >", default="s", show_default=False).strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Bye.[/dim]")
            return None

        if choice == "1":
            try:
                query = _ask_question(console, query)
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Bye.[/dim]")
                return None
        elif choice == "2":
            new_provider = _pick_provider(console, providers, provider_name)
            if new_provider and new_provider != provider_name:
                provider_name = new_provider
                preset = providers[provider_name]
                if preset["base_url"]:
                    config.base_url = preset["base_url"]
                if preset["api_key"]:
                    config.api_key = preset["api_key"]
                config.model = preset["default_model"]
                config.provider_kind = "claude_agent" if provider_name == "claude" else "openai"
                if provider_name in ("ollama", "lmstudio"):
                    config.timeout = 300
        elif choice == "3":
            config.model = _ask_model(console, config.model)
        elif choice == "4":
            config.start_year, config.end_year = _ask_year_range(
                console, config.start_year, config.end_year
            )
        elif choice == "5":
            config.email = _ask_email(console, config.email)
        elif choice == "6":
            config.output_dir = _ask_output_dir(console, config.output_dir)
        elif choice == "c":
            if not query:
                console.print("[yellow]Please set a research question first (option 1).[/yellow]")
                continue
            compare_result = _compare_submenu(console, providers, provider_name)
            if compare_result is not None:
                prov_a, prov_b = compare_result
                save_state(
                    last_query=query,
                    last_provider=provider_name,
                    last_model=config.model,
                    last_start_year=config.start_year,
                    last_end_year=config.end_year,
                    last_email=config.email,
                    last_output_dir=config.output_dir,
                )
                return ("__compare__", query, config, provider_name, prov_a, prov_b)
        elif choice == "r":
            picked = _replay_submenu(console, config.output_dir)
            if picked is not None:
                save_state(
                    last_query=query,
                    last_provider=provider_name,
                    last_model=config.model,
                    last_start_year=config.start_year,
                    last_end_year=config.end_year,
                    last_email=config.email,
                    last_output_dir=config.output_dir,
                )
                return ("__replay__", picked, config, provider_name)
        elif choice in ("q", "quit", "exit"):
            console.print("[dim]Bye.[/dim]")
            return None
        elif choice in ("s", "start", ""):
            if not query:
                console.print("[yellow]Please set a research question first (option 1).[/yellow]")
                continue
            # Cloud providers need an API key; let the user know now
            # instead of after confirmation.
            if provider_name and provider_name not in ("ollama", "lmstudio", "claude"):
                if not config.api_key or config.api_key in ("ollama", "lm-studio", ""):
                    console.print(
                        f"[red]Provider '{provider_name}' needs an API key.[/red] "
                        f"Set OPENAI_API_KEY in your environment, or edit "
                        f"~/.deep-researcher/config.json, then restart."
                    )
                    continue
            console.print()
            console.print(Panel(
                f"[bold]{query}[/bold]\n\n"
                f"Provider: {provider_name or '(default)'}  •  Model: {config.model}\n"
                f"[dim]This may take several minutes and use API credits.[/dim]",
                title="Ready to start",
                border_style="green",
            ))
            if not Confirm.ask("Start research now?", default=True):
                continue
            # Persist for next run
            save_state(
                last_query=query,
                last_provider=provider_name,
                last_model=config.model,
                last_start_year=config.start_year,
                last_end_year=config.end_year,
                last_email=config.email,
                last_output_dir=config.output_dir,
            )
            return query, config, provider_name
        else:
            console.print("[yellow]Pick one of the numbered options, or s / q.[/yellow]")
