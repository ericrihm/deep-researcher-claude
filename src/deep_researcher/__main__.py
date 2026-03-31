from __future__ import annotations

import argparse
import sys

from rich.console import Console

from deep_researcher import __version__
from deep_researcher.agent import ResearchAgent
from deep_researcher.config import Config


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="deep-researcher",
        description="An agentic academic research assistant that searches multiple databases and produces literature reviews.",
    )
    parser.add_argument("query", nargs="?", help="Research question to investigate")
    parser.add_argument("--model", default=None, help="LLM model name (default: llama3.1)")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible API base URL (default: http://localhost:11434/v1)")
    parser.add_argument("--api-key", default=None, help="API key (default: 'ollama')")
    parser.add_argument("--max-iterations", type=int, default=None, help="Maximum research iterations (default: 20)")
    parser.add_argument("--output", default=None, help="Output directory (default: ./output)")
    parser.add_argument("--email", default=None, help="Email for polite API access to OpenAlex/CrossRef/Unpaywall")
    parser.add_argument("--version", action="version", version=f"deep-researcher {__version__}")
    args = parser.parse_args()

    console = Console()

    if not args.query:
        console.print("[bold]Deep Researcher[/bold] — Academic Literature Review Agent\n")
        console.print("Usage: deep-researcher \"your research question here\"\n")
        console.print("Examples:")
        console.print('  deep-researcher "transformer models for structural health monitoring"')
        console.print('  deep-researcher "machine learning in drug discovery" --model gpt-4o')
        console.print('  deep-researcher "CRISPR gene editing efficiency" --email you@university.edu')
        console.print("\nRun deep-researcher --help for all options.")
        sys.exit(0)

    config = Config()
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key
    if args.max_iterations:
        config.max_iterations = args.max_iterations
    if args.output:
        config.output_dir = args.output
    if args.email:
        config.email = args.email

    console.print(f"[dim]Model: {config.model} @ {config.base_url}[/dim]")

    agent = ResearchAgent(config)
    try:
        report = agent.research(args.query)
        if report:
            console.print("\n")
            from rich.markdown import Markdown
            console.print(Markdown(report))
    except KeyboardInterrupt:
        console.print("\n[yellow]Research interrupted by user.[/yellow]")
        sys.exit(1)


if __name__ == "__main__":
    main()
