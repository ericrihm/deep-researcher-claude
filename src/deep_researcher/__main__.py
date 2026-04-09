from __future__ import annotations

import argparse
import os
import signal
import sys

from rich.console import Console

from deep_researcher import __version__
from deep_researcher.orchestrator import Orchestrator
from deep_researcher.config import Config

# Provider presets — saves users from looking up base URLs
PROVIDERS: dict[str, dict[str, str]] = {
    "ollama": {"base_url": "http://localhost:11434/v1", "api_key": "ollama", "default_model": "qwen3.5:9b"},
    "lmstudio": {"base_url": "http://localhost:1234/v1", "api_key": "lm-studio", "default_model": "default"},
    "openai": {"base_url": "https://api.openai.com/v1", "api_key": "", "default_model": "gpt-5.4-mini"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "api_key": "", "default_model": "claude-sonnet-4-6"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "api_key": "", "default_model": "qwen/qwen3-32b"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": "", "default_model": "deepseek-chat"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key": "", "default_model": "anthropic/claude-sonnet-4-6"},
    "together": {"base_url": "https://api.together.xyz/v1", "api_key": "", "default_model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct"},
    # Routes through claude_agent_sdk -> bundled `claude` CLI -> OAuth credentials
    # from `claude login`. No API key required. base_url/api_key are unused for
    # this provider; the entries exist so the preset-application loop in main()
    # doesn't need a special case.
    "claude": {"base_url": "", "api_key": "", "default_model": "claude-sonnet-4-5"},
}


def _setup_claude_provider(
    config: Config,
    console: Console,
    *,
    verbose: bool,
    show_advisory: bool,
    reset_auth: bool,
) -> bool:
    """Wire up --provider claude. Returns True on success, False to exit.

    Shared between the CLI flag path and the TUI path so that every
    entry point gets the same auth detection, env-scrub notice, and
    advisory treatment.
    """
    from deep_researcher.auth import (
        claude_cli_installed,
        detect_claude_code_session,
        detect_claude_oauth_credentials,
        print_oauth_advisory,
    )
    config.provider_kind = "claude_agent"

    # Warn if ANTHROPIC_API_KEY is set — it will be scrubbed per-call in
    # llm_claude._scrub_anthropic_env, but the researcher should know
    # their env var is being ignored on purpose rather than wonder why
    # billing isn't flowing through it.
    if os.environ.get("ANTHROPIC_API_KEY"):
        console.print(
            "[dim]Ignoring ANTHROPIC_API_KEY in this session — "
            "using OAuth from your `claude login` session.[/dim]"
        )

    in_session = detect_claude_code_session()
    has_oauth = detect_claude_oauth_credentials()
    has_cli = claude_cli_installed()
    if in_session:
        if verbose:
            console.print(
                "[dim]Detected Claude Code CLI session — using the active "
                "subscription auth.[/dim]"
            )
    elif has_oauth:
        if verbose:
            console.print(
                "[dim]Using auth from your local `claude login` session.[/dim]"
            )
    else:
        console.print(
            "[red]No Claude credentials found.[/red] "
            "For the smoothest experience, run this inside the Claude Code CLI "
            "([cyan]claude[/cyan]) — install: https://docs.claude.com/en/docs/claude-code/quickstart"
        )
        console.print(
            "Then run [cyan]claude login[/cyan] once. After that, "
            "[cyan]deep-researcher --provider claude[/cyan] will work from any terminal."
        )
        if not has_cli:
            console.print(
                "[yellow]Hint: `claude` is not on PATH. The bundled CLI inside "
                "claude_agent_sdk will still work, but you must have logged in once.[/yellow]"
            )
        return False

    print_oauth_advisory(console, force=show_advisory)
    if reset_auth:
        from deep_researcher.state import clear_state_keys
        clear_state_keys("advisory_seen")
        console.print(
            "[yellow]--reset-auth: cleared local state. This provider's auth lives in "
            "your `claude` CLI session — run `claude logout` and then `claude login` "
            "to reset the credentials themselves.[/yellow]"
        )
    return True


def main() -> None:
    # Windows encoding handling — two layers, with important caveats.
    #
    # Layer 1 (always safe): reconfigure Python's own stdout/stderr to
    # emit UTF-8 with replacement on errors. This only affects our
    # process's writer and prevents UnicodeEncodeError when Rich prints
    # LLM output containing em-dashes, arrows, etc. Harmless to
    # anything else.
    #
    # Layer 2 (CAREFUL): flipping the Windows console code page to
    # 65001 via SetConsoleOutputCP fixes mojibake for bare-PowerShell
    # users, but it mutates SHARED console state. If we're running as
    # a child of another interactive tool (e.g. Claude Code CLI, which
    # is a Node.js process that cached the console code page at its
    # own startup and set up its output encoder accordingly), flipping
    # the code page mid-session desyncs that parent's encoder and
    # garbles all of its subsequent UI output. We detected this in
    # the wild: running `deep-researcher` from inside `claude` broke
    # Claude Code CLI's spinner and status line for the rest of the
    # session.
    #
    # Mitigation: skip layer 2 entirely when we detect we're inside a
    # Claude Code CLI session (CLAUDECODE=1, same signal we already
    # use for auth). For standalone runs, save the original code page
    # and restore it on exit so we leave the console the way we
    # found it.
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

        if os.environ.get("CLAUDECODE") != "1":
            try:
                import atexit
                import ctypes
                kernel32 = ctypes.windll.kernel32
                original_output_cp = kernel32.GetConsoleOutputCP()
                original_input_cp = kernel32.GetConsoleCP()
                if original_output_cp != 65001:
                    kernel32.SetConsoleOutputCP(65001)
                if original_input_cp != 65001:
                    kernel32.SetConsoleCP(65001)

                def _restore_console_cp() -> None:
                    try:
                        if original_output_cp != 65001:
                            kernel32.SetConsoleOutputCP(original_output_cp)
                        if original_input_cp != 65001:
                            kernel32.SetConsoleCP(original_input_cp)
                    except Exception:
                        pass

                atexit.register(_restore_console_cp)
            except Exception:
                pass

    parser = argparse.ArgumentParser(
        prog="deep-researcher",
        description="An agentic academic research assistant that searches multiple databases and produces literature reviews.",
    )
    parser.add_argument("query", nargs="?", help="Research question to investigate")
    parser.add_argument("--provider", choices=list(PROVIDERS.keys()), help="LLM provider (auto-configures base URL and model)")
    parser.add_argument("--model", default=None, help="LLM model name")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible API base URL")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--output", default=None, help="Output directory (default: ./output)")
    parser.add_argument("--email", default=None, help="Email for polite API access to OpenAlex/CrossRef/Unpaywall")
    parser.add_argument("--start-year", type=int, default=None, help="Filter papers published on or after this year")
    parser.add_argument("--end-year", type=int, default=None, help="Filter papers published on or before this year")
    parser.add_argument("--interactive", action="store_true", help="Ask clarifying questions before researching")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open the HTML report in a browser when done")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--reset-auth", action="store_true", help="Forget any stored auth state for the selected provider and re-onboard on next run")
    parser.add_argument("--show-advisory", action="store_true", help="Re-show the Claude OAuth policy advisory (normally printed once)")
    parser.add_argument("--version", action="version", version=f"deep-researcher {__version__}")
    args = parser.parse_args()

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.WARNING)  # Keep third-party libs quiet
        logging.getLogger("deep_researcher").setLevel(logging.DEBUG)
        logging.getLogger("deep_researcher").addHandler(
            logging.StreamHandler()
        )
        logging.getLogger("deep_researcher").handlers[-1].setFormatter(
            logging.Formatter("%(name)s %(levelname)s: %(message)s")
        )

    console = Console()

    # --------------------------------------------------------------
    # No-args path: drop into the interactive TUI so researchers who
    # don't want to memorize flags (or quote their query correctly on
    # PowerShell) have a path that Just Works.
    # --------------------------------------------------------------
    if not args.query:
        from deep_researcher import tui
        result = tui.run(console, PROVIDERS)
        if result is None:
            sys.exit(0)
        query, config, provider_name = result
        if provider_name == "claude":
            ok = _setup_claude_provider(
                config,
                console,
                verbose=args.verbose,
                show_advisory=args.show_advisory,
                reset_auth=args.reset_auth,
            )
            if not ok:
                sys.exit(1)
        _run_pipeline(console, config, query, open_html=not args.no_open)
        return

    # --------------------------------------------------------------
    # Flag-based path (power users / scripts): preserved unchanged.
    # --------------------------------------------------------------
    config = Config()
    if args.provider:
        preset = PROVIDERS[args.provider]
        config.base_url = preset["base_url"]
        config.api_key = preset["api_key"]
        config.model = preset["default_model"]
        # Local models need longer timeouts for synthesis (large prompt + long response)
        if args.provider in ("ollama", "lmstudio"):
            config.timeout = 300
        if args.provider == "claude":
            ok = _setup_claude_provider(
                config,
                console,
                verbose=args.verbose,
                show_advisory=args.show_advisory,
                reset_auth=args.reset_auth,
            )
            if not ok:
                sys.exit(1)

    # Explicit args override provider preset
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key
    if args.output:
        config.output_dir = args.output
    if args.email:
        config.email = args.email
    if args.start_year is not None:
        config.start_year = args.start_year
    if args.end_year is not None:
        config.end_year = args.end_year
    if args.interactive:
        config.interactive = True

    # Check for missing API key on cloud providers
    # ("claude" routes through the SDK and uses OAuth; no API key needed.)
    if not config.api_key or config.api_key in ("ollama", "lm-studio"):
        if args.provider and args.provider not in ("ollama", "lmstudio", "claude"):
            console.print(f"[red]Error: --provider {args.provider} requires an API key.[/red]")
            console.print(f"Set it with: --api-key YOUR_KEY")
            console.print(f"Or export it — bash/zsh: [cyan]export OPENAI_API_KEY=YOUR_KEY[/cyan]  "
                          f"PowerShell: [cyan]$env:OPENAI_API_KEY = \"YOUR_KEY\"[/cyan]")
            sys.exit(1)

    _run_pipeline(console, config, args.query, open_html=not args.no_open)


def _run_pipeline(console: Console, config: Config, query: str, *, open_html: bool = True) -> None:
    """Shared pipeline runner for both the flag path and the TUI path."""
    if config.provider_kind == "claude_agent":
        console.print(f"[dim]Using {config.model} (subscription auth).[/dim]")
    else:
        console.print(f"[dim]Model: {config.model} @ {config.base_url}[/dim]")
    if config.start_year is not None or config.end_year is not None:
        yr_range = f"{config.start_year if config.start_year is not None else '...'}-{config.end_year if config.end_year is not None else '...'}"
        console.print(f"[dim]Settings: years={yr_range}[/dim]")

    orchestrator = Orchestrator(config)
    if config.interactive:
        query = orchestrator.clarify(query)

    def _on_interrupt(signum, frame):
        orchestrator.cancel()

    prev_handler = signal.signal(signal.SIGINT, _on_interrupt)
    try:
        report = orchestrator.research(query)
        if report:
            console.print("\n")
            try:
                from rich.markdown import Markdown
                console.print(Markdown(report))
            except Exception:
                console.print(report)
            # Auto-open the HTML report in the default browser
            html_path = orchestrator.last_report_paths.get("html")
            if html_path and open_html and not html_path.startswith("(failed"):
                import webbrowser
                try:
                    webbrowser.open("file://" + os.path.abspath(html_path))
                except Exception as e:
                    console.print(f"[yellow]Could not auto-open HTML report: {e}[/yellow]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Research interrupted.[/yellow]")
        sys.exit(1)
    finally:
        signal.signal(signal.SIGINT, prev_handler)


if __name__ == "__main__":
    main()
