"""Parsing utilities and corpus-building helpers for the ResearchAgent."""

import re
import logging

from deep_researcher.constants import (
    ABSTRACT_MAX_CHARS,
    ABSTRACT_MIN_CUT,
    CHARS_PER_TOKEN,
    MIN_CATEGORIZATION_COVERAGE,
)
from deep_researcher.models import Paper

logger = logging.getLogger("deep_researcher")


def parse_categories(text: str, paper_count: int) -> dict[str, list[int]]:
    """Parse LLM category output into {name: [0-based indices]}."""
    categories: dict[str, list[int]] = {}
    current_cat = None

    for line in text.split("\n"):
        cleaned = re.sub(r"[*_`#>]", "", line).strip()
        cleaned = re.sub(r"^[-+]\s*", "", cleaned)
        if not cleaned:
            continue

        cat_match = re.match(r"(?:CATEGORY|Category)\s*:\s*(.+)", cleaned, re.IGNORECASE)
        if cat_match:
            current_cat = cat_match.group(1).strip()
            continue

        papers_match = re.match(r"(?:PAPERS|Papers)\s*:\s*(.+)", cleaned, re.IGNORECASE)
        if papers_match and current_cat:
            nums = re.findall(r"\d+", papers_match.group(1))
            indices = [int(n) - 1 for n in nums if 0 < int(n) <= paper_count]
            if indices:
                categories[current_cat] = indices
            current_cat = None

    assigned = set()
    for indices in categories.values():
        assigned.update(indices)
    if len(assigned) < paper_count * MIN_CATEGORIZATION_COVERAGE:
        logger.warning("Categorization covered only %d/%d papers", len(assigned), paper_count)

    return categories


def parse_merged_categories(
    text: str, original: dict[str, list[int]]
) -> dict[str, list[int]] | None:
    """Parse LLM merge output into consolidated categories."""
    merged: dict[str, list[int]] = {}
    current_final = None

    for line in text.split("\n"):
        cleaned = re.sub(r"[*_`#>]", "", line).strip()
        cleaned = re.sub(r"^[-+]\s*", "", cleaned)
        if not cleaned:
            continue

        final_match = re.match(r"(?:FINAL)\s*:\s*(.+)", cleaned, re.IGNORECASE)
        if final_match:
            current_final = final_match.group(1).strip()
            continue

        merge_match = re.match(r"(?:MERGE)\s*:\s*(.+)", cleaned, re.IGNORECASE)
        if merge_match and current_final:
            old_names = [n.strip() for n in merge_match.group(1).split(",")]
            indices: list[int] = []
            for old_name in old_names:
                if old_name in original:
                    indices.extend(original[old_name])
                else:
                    for orig_name in original:
                        if old_name.lower() in orig_name.lower() or orig_name.lower() in old_name.lower():
                            indices.extend(original[orig_name])
                            break
            if indices:
                merged[current_final] = indices
            current_final = None

    if not merged:
        return None
    total_papers = sum(len(v) for v in merged.values())
    orig_total = sum(len(v) for v in original.values())
    if total_papers < orig_total * 0.5:
        logger.warning("Category merge lost too many papers (%d/%d)", total_papers, orig_total)
        return None
    return merged


def build_tiered_corpus(indexed_papers: list, token_budget: int = 15000) -> str:
    """Build a token-budgeted corpus with progressive compression.

    indexed_papers: list of (global_index, Paper) tuples.
    Uses global indices for [N] references so they match the final reference list.
    """
    if not indexed_papers:
        return ""

    # Sort by citations within the category
    sorted_pairs = sorted(indexed_papers, key=lambda x: (-(x[1].citation_count or 0), -(x[1].year or 0)))

    lines = []
    tokens_used = 0
    level1_budget = int(token_budget * 0.6)
    level2_budget = int(token_budget * 0.9)

    for processed, (idx, p) in enumerate(sorted_pairs):
        ref_num = idx + 1  # 0-based index -> 1-based reference number

        full_entry = paper_full_entry(ref_num, p)
        full_tokens = len(full_entry) // CHARS_PER_TOKEN
        if tokens_used + full_tokens < level1_budget:
            lines.append(full_entry)
            tokens_used += full_tokens
            continue

        short_entry = paper_short_entry(ref_num, p)
        short_tokens = len(short_entry) // CHARS_PER_TOKEN
        if tokens_used + short_tokens < level2_budget:
            lines.append(short_entry)
            tokens_used += short_tokens
            continue

        remaining = len(sorted_pairs) - processed
        if remaining > 0:
            lines.append(f"\n(+ {remaining} additional papers in this category, sorted by citation count)")
        break

    return "\n".join(lines)


def paper_full_entry(idx: int, p: Paper) -> str:
    """Full paper entry with abstract for tier-1 papers."""
    parts = []
    author = p.authors[0] if p.authors else "Unknown"
    if len(p.authors) > 1:
        author += " et al."
    parts.append(f"[{idx}] {p.title}")
    meta = [author]
    if p.year:
        meta.append(str(p.year))
    if p.journal:
        meta.append(p.journal)
    if p.citation_count is not None:
        meta.append(f"{p.citation_count} citations")
    if p.doi:
        meta.append(f"DOI: {p.doi}")
    parts.append(f"   {' | '.join(meta)}")
    if p.abstract:
        abstract = p.abstract[:ABSTRACT_MAX_CHARS]
        if len(p.abstract) > ABSTRACT_MAX_CHARS:
            cut = abstract.rfind(". ")
            abstract = abstract[:cut + 1] if cut > ABSTRACT_MIN_CUT else abstract + "..."
        parts.append(f"   Abstract: {abstract}")
    return "\n".join(parts)


def paper_short_entry(idx: int, p: Paper) -> str:
    """One-line compressed entry for tier-2 papers."""
    author = p.authors[0].split()[-1] if p.authors else "Unknown"
    year = p.year or "n.d."
    cites = f", {p.citation_count} cites" if p.citation_count else ""
    return f"[{idx}] {p.title} ({author}, {year}{cites})"


def titles_match(title_a: str, title_b: str) -> bool:
    """Check if two paper titles refer to the same work.

    Uses word overlap ratio to catch fuzzy matches (different punctuation,
    truncation, etc.) while rejecting completely different papers that share
    a few common words like "large language models".
    """
    words_a = set(re.findall(r"[a-z0-9]+", title_a.lower()))
    words_b = set(re.findall(r"[a-z0-9]+", title_b.lower()))
    if not words_a or not words_b:
        return False
    # Remove very common words that cause false matches
    stopwords = {"a", "an", "the", "of", "in", "for", "and", "on", "to", "with", "based", "using"}
    words_a -= stopwords
    words_b -= stopwords
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b)
    smaller = min(len(words_a), len(words_b))
    return overlap / smaller >= 0.5
