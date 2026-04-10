"""Search source profiles.

Each profile defines which search tools to activate during the search phase.
The "default" profile preserves the original Scholar + Scopus behavior.
Specialized profiles add domain-relevant sources without changing defaults.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SearchProfile:
    """Defines which search sources and prompt style a profile uses."""
    name: str
    description: str
    # Tool class names to instantiate for the search phase.
    # "scholar" and "scopus" are always-available baselines.
    # Others require the tool to be importable and (for some) an API key.
    search_sources: tuple[str, ...]
    # When set, overrides the default categorization/synthesis prompts
    # with domain-specific variants from prompts.py.
    prompt_style: str = "default"
    # Max concurrent search workers (one per source).
    max_search_workers: int = 4


# -- Profile registry -------------------------------------------------------

PROFILES: dict[str, SearchProfile] = {
    "default": SearchProfile(
        name="default",
        description="Google Scholar + Scopus (original behavior)",
        search_sources=("scholar", "scopus"),
        prompt_style="default",
        max_search_workers=2,
    ),
    "security": SearchProfile(
        name="security",
        description=(
            "Security research: Scholar + Scopus + Semantic Scholar + arXiv + "
            "DBLP + IEEE Xplore (if key). Covers USENIX Security, CCS, NDSS, "
            "IEEE S&P, and preprint servers."
        ),
        search_sources=(
            "scholar",
            "scopus",
            "semantic_scholar",
            "arxiv",
            "dblp",
            "ieee",
        ),
        prompt_style="security",
        max_search_workers=6,
    ),
    "biomedical": SearchProfile(
        name="biomedical",
        description=(
            "Biomedical research: Scholar + Scopus + PubMed. "
            "Covers NLM/MEDLINE indexed journals."
        ),
        search_sources=("scholar", "scopus", "pubmed"),
        prompt_style="default",
        max_search_workers=3,
    ),
    "comprehensive": SearchProfile(
        name="comprehensive",
        description=(
            "All available sources: Scholar + Scopus + Semantic Scholar + "
            "arXiv + DBLP + IEEE + PubMed + CORE + OpenAlex + CrossRef. "
            "Slower but maximum coverage."
        ),
        search_sources=(
            "scholar",
            "scopus",
            "semantic_scholar",
            "arxiv",
            "dblp",
            "ieee",
            "pubmed",
            "core",
            "openalex",
            "crossref",
        ),
        prompt_style="default",
        max_search_workers=8,
    ),
}


def get_profile(name: str) -> SearchProfile:
    """Look up a profile by name. Raises ValueError for unknown profiles."""
    profile = PROFILES.get(name)
    if profile is None:
        valid = ", ".join(sorted(PROFILES.keys()))
        raise ValueError(f"Unknown profile '{name}'. Valid profiles: {valid}")
    return profile


def list_profiles() -> list[SearchProfile]:
    """Return all profiles in display order."""
    return [PROFILES[k] for k in ("default", "security", "biomedical", "comprehensive")]
