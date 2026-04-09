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
    # Routes through PKCE OAuth against chatgpt.com/backend-api/codex. Reuses
    # a Codex CLI auth.json if present, else falls back to browser sign-in.
    # No API key required.
    "chatgpt": {"base_url": "https://chatgpt.com/backend-api/codex",
                "api_key": "", "default_model": "gpt-5"},
}


def _setup_elsevier(args, config: Config, console: Console) -> None:
    """Resolve the Elsevier API key and print the borrowing notice if
    we're using the bundled default. Call once, before Orchestrator."""
    from deep_researcher.elsevier_auth import (
        resolve_elsevier_key,
        print_borrowing_notice_once,
    )
    if args.no_elsevier:
        config.no_elsevier = True
        config.scopus_api_key = ""
        return

    key, is_bundled = resolve_elsevier_key(
        flag_key=args.elsevier_key,
        config_key=config.scopus_api_key or None,
    )
    config.scopus_api_key = key
    if is_bundled:
        print_borrowing_notice_once(console)


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


def _setup_chatgpt_provider(
    config: Config,
    console: Console,
    *,
    verbose: bool,
    reset_auth: bool,
) -> bool:
    """Wire up --provider chatgpt.

    Four-tier resolution:
    1. Existing Codex CLI / ChatGPT-Local auth.json
    2. Our own cached token at ~/.deep-researcher/chatgpt-auth.json
    3. PKCE browser sign-in (writes to tier 2 for next time)
    4. Fallback: OPENAI_API_KEY against api.openai.com (if tiers 1-3 fail)
    """
    from deep_researcher.auth_chatgpt import (
        CHATGPT_BACKEND_BASE_URL,
        ChatGPTAuthError,
        clear_stored_chatgpt_auth,
        resolve_chatgpt_auth,
    )
    config.provider_kind = "chatgpt_oauth"
    config.base_url = CHATGPT_BACKEND_BASE_URL
    if reset_auth:
        clear_stored_chatgpt_auth()
        console.print("[yellow]--reset-auth: cleared stored ChatGPT token.[/yellow]")
    try:
        auth = resolve_chatgpt_auth(console, verbose=verbose, allow_browser=True)
    except ChatGPTAuthError as e:
        env_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if env_key:
            console.print(
                f"[yellow]ChatGPT OAuth failed ({e}); "
                f"falling back to OPENAI_API_KEY.[/yellow]"
            )
            config.provider_kind = "openai"
            config.base_url = "https://api.openai.com/v1"
            config.api_key = env_key
            return True
        console.print(f"[red]{e}[/red]")
        return False
    config.api_key = auth.access_token
    config._chatgpt_auth_handle = auth
    return True


def _handle_auth_chatgpt_subcommand(argv: list[str]) -> None:
    """Dispatched from main() when argv[1] == 'auth-chatgpt'."""
    from deep_researcher.auth_chatgpt import (
        ChatGPTAuthError,
        _try_codex_files,
        _try_stored_token,
        clear_stored_chatgpt_auth,
        resolve_chatgpt_auth,
    )
    console = Console()
    sub = argparse.ArgumentParser(prog="deep-researcher auth-chatgpt")
    sub.add_argument("--logout", action="store_true", help="Delete stored token")
    sub.add_argument(
        "--status", action="store_true", help="Show which auth tier is active"
    )
    sub_args = sub.parse_args(argv)

    if sub_args.logout:
        clear_stored_chatgpt_auth()
        console.print("[green]Stored ChatGPT token deleted.[/green]")
        return
    if sub_args.status:
        codex = _try_codex_files()
        stored = _try_stored_token()
        if codex:
            console.print(
                f"[green]Tier 1: Codex/Local file at {codex.source_file}[/green]"
            )
            console.print(f"[dim]  expires_at: {codex.expires_at}[/dim]")
        elif stored:
            console.print(
                f"[green]Tier 2: Stored token at {stored.source_file}[/green]"
            )
            console.print(f"[dim]  expires_at: {stored.expires_at}[/dim]")
        else:
            console.print(
                "[yellow]No ChatGPT auth detected. "
                "Run: deep-researcher auth-chatgpt[/yellow]"
            )
        return

    try:
        auth = resolve_chatgpt_auth(console, verbose=True, allow_browser=True)
        console.print(f"[green]Signed in. Token at {auth.source_file}.[/green]")
    except ChatGPTAuthError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)


def main() -> None:
    # Intercept `deep-researcher auth-chatgpt ...` before argparse runs,
    # since it has its own subparser and argument set.
    if len(sys.argv) >= 2 and sys.argv[1] == "auth-chatgpt":
        _handle_auth_chatgpt_subcommand(sys.argv[2:])
        return

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
    parser.add_argument(
        "--replay",
        metavar="FOLDER",
        help="Re-run synthesis on an existing output folder without re-searching",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("PROVIDER_A", "PROVIDER_B"),
        help="Compare two providers on the same corpus (e.g., --compare claude groq)",
    )
    parser.add_argument("--provider", choices=list(PROVIDERS.keys()), help="LLM provider (auto-configures base URL and model)")
    parser.add_argument("--model", default=None, help="LLM model name")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible API base URL")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--output", default=None, help="Output directory (default: ./output)")
    parser.add_argument("--email", default=None, help="Email for polite API access to OpenAlex/CrossRef/Unpaywall")
    parser.add_argument("--start-year", type=int, default=None, help="Filter papers published on or after this year")
    parser.add_argument("--end-year", type=int, default=None, help="Filter papers published on or before this year")
    parser.add_argument("--elsevier-key", default=None,
                        help="Elsevier API key for Scopus search "
                             "(overrides ELSEVIER_API_KEY and config.json)")
    parser.add_argument("--no-elsevier", action="store_true",
                        help="Skip the Elsevier/Scopus search pass")
    parser.add_argument("--interactive", action="store_true", help="Ask clarifying questions before researching")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open the HTML report in a browser when done")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--reset-auth", action="store_true", help="Forget any stored auth state for the selected provider and re-onboard on next run")
    parser.add_argument("--show-advisory", action="store_true", help="Re-show the Claude OAuth policy advisory (normally printed once)")
    parser.add_argument("--version", action="version", version=f"deep-researcher {__version__}")
    args = parser.parse_args()

    if args.replay and args.query:
        parser.error("--replay and a query are mutually exclusive")

    if args.compare and args.replay:
        parser.error("--compare and --replay are mutually exclusive")
    if args.compare and not args.query:
        parser.error("--compare requires a query argument")
    if args.compare:
        for prov in args.compare:
            if prov not in PROVIDERS:
                parser.error(f"Unknown provider '{prov}'. Choose from: {', '.join(PROVIDERS.keys())}")
        for prov in args.compare:
            if prov in ("claude", "chatgpt") and prov != args.provider:
                parser.error(
                    f"--compare with '{prov}' requires --provider {prov} "
                    f"(OAuth providers need explicit auth setup)"
                )

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
    if args.replay and not args.query:
        config = Config()
        if args.provider:
            preset = PROVIDERS[args.provider]
            config.base_url = preset["base_url"]
            config.api_key = preset["api_key"]
            config.model = preset["default_model"]
            if args.provider in ("ollama", "lmstudio"):
                config.timeout = 300
            if args.provider == "claude":
                ok = _setup_claude_provider(
                    config, console,
                    verbose=args.verbose,
                    show_advisory=args.show_advisory,
                    reset_auth=args.reset_auth,
                )
                if not ok:
                    sys.exit(1)
            elif args.provider == "chatgpt":
                ok = _setup_chatgpt_provider(
                    config, console,
                    verbose=args.verbose,
                    reset_auth=args.reset_auth,
                )
                if not ok:
                    sys.exit(1)
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
        _setup_elsevier(args, config, console)
        _run_replay(console, config, args.replay, open_html=not args.no_open)
        return

    if not args.query:
        from deep_researcher import tui
        result = tui.run(console, PROVIDERS)
        if result is None:
            sys.exit(0)
        if len(result) == 4 and result[0] == "__replay__":
            _, folder, config, provider_name = result
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
            elif provider_name == "chatgpt":
                ok = _setup_chatgpt_provider(
                    config, console,
                    verbose=args.verbose,
                    reset_auth=args.reset_auth,
                )
                if not ok:
                    sys.exit(1)
            _setup_elsevier(args, config, console)
            _run_replay(console, config, folder, open_html=not args.no_open)
            return
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
        elif provider_name == "chatgpt":
            ok = _setup_chatgpt_provider(
                config, console,
                verbose=args.verbose,
                reset_auth=args.reset_auth,
            )
            if not ok:
                sys.exit(1)
        _setup_elsevier(args, config, console)
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
        elif args.provider == "chatgpt":
            ok = _setup_chatgpt_provider(
                config, console,
                verbose=args.verbose,
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

    _setup_elsevier(args, config, console)

    # Check for missing API key on cloud providers
    # ("claude" and "chatgpt" use OAuth; no API key needed upfront.)
    if not config.api_key or config.api_key in ("ollama", "lm-studio"):
        if args.provider and args.provider not in ("ollama", "lmstudio", "claude", "chatgpt"):
            console.print(f"[red]Error: --provider {args.provider} requires an API key.[/red]")
            console.print(f"Set it with: --api-key YOUR_KEY")
            console.print(f"Or export it — bash/zsh: [cyan]export OPENAI_API_KEY=YOUR_KEY[/cyan]  "
                          f"PowerShell: [cyan]$env:OPENAI_API_KEY = \"YOUR_KEY\"[/cyan]")
            sys.exit(1)

    if args.compare:
        _run_compare(console, config, args.query, args.compare[0], args.compare[1],
                     open_html=not args.no_open)
        return

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


def _run_replay(console: Console, config: Config, folder: str, *, open_html: bool = True) -> None:
    """Replay synthesis on an existing output folder."""
    if config.provider_kind == "claude_agent":
        console.print(f"[dim]Using {config.model} (subscription auth).[/dim]")
    else:
        console.print(f"[dim]Model: {config.model} @ {config.base_url}[/dim]")

    orchestrator = Orchestrator(config)

    def _on_interrupt(signum, frame):
        orchestrator.cancel()

    prev_handler = signal.signal(signal.SIGINT, _on_interrupt)
    try:
        report = orchestrator.replay(folder)
        if report:
            console.print("\n")
            try:
                from rich.markdown import Markdown
                console.print(Markdown(report))
            except Exception:
                console.print(report)
            html_path = orchestrator.last_report_paths.get("html")
            if html_path and open_html and not html_path.startswith("(failed"):
                import webbrowser
                try:
                    webbrowser.open("file://" + os.path.abspath(html_path))
                except Exception as e:
                    console.print(f"[yellow]Could not auto-open HTML report: {e}[/yellow]")
    except FileNotFoundError as e:
        console.print(f"[red]Replay failed:[/red] {e}")
        sys.exit(1)
    except ValueError as e:
        console.print(f"[red]Replay failed:[/red] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Replay interrupted.[/yellow]")
        sys.exit(1)
    finally:
        signal.signal(signal.SIGINT, prev_handler)


def _run_compare(
    console: Console,
    config: Config,
    query: str,
    provider_a: str,
    provider_b: str,
    *,
    open_html: bool = True,
) -> None:
    """Run dual-provider comparison."""
    console.print(f"[dim]Comparing: {provider_a} vs {provider_b}[/dim]")

    orchestrator = Orchestrator(config)

    def _on_interrupt(signum, frame):
        orchestrator.cancel()

    prev_handler = signal.signal(signal.SIGINT, _on_interrupt)
    try:
        report_a, report_b = orchestrator.compare_research(
            query, provider_a, provider_b, PROVIDERS,
        )
        if report_a or report_b:
            console.print("\n[green]Comparison complete.[/green]")
        html_path = orchestrator.last_report_paths.get("html")
        if html_path and open_html and os.path.exists(html_path):
            import webbrowser
            try:
                webbrowser.open("file://" + os.path.abspath(html_path))
            except Exception as e:
                console.print(f"[yellow]Could not auto-open compare.html: {e}[/yellow]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Comparison interrupted.[/yellow]")
        sys.exit(1)
    finally:
        signal.signal(signal.SIGINT, prev_handler)


if __name__ == "__main__":
    main()
