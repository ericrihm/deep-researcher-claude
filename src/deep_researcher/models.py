from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


@dataclass
class Paper:
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    doi: str | None = None
    url: str | None = None
    source: str = ""
    citation_count: int | None = None
    journal: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    open_access_url: str | None = None

    @property
    def unique_key(self) -> str:
        if self.doi:
            return f"doi:{self.doi.lower()}"
        normalized = re.sub(r"\s+", " ", self.title.lower().strip())
        return f"title:{hashlib.md5(normalized.encode()).hexdigest()}"

    def to_summary(self) -> str:
        parts = [f"**{self.title}**"]
        if self.authors:
            author_str = self.authors[0]
            if len(self.authors) > 1:
                author_str += " et al."
            parts.append(f"Authors: {author_str}")
        if self.year:
            parts.append(f"Year: {self.year}")
        if self.journal:
            parts.append(f"Journal: {self.journal}")
        if self.citation_count is not None:
            parts.append(f"Citations: {self.citation_count}")
        if self.doi:
            parts.append(f"DOI: {self.doi}")
        if self.abstract:
            abstract = self.abstract[:300]
            if len(self.abstract) > 300:
                abstract += "..."
            parts.append(f"Abstract: {abstract}")
        if self.open_access_url:
            parts.append(f"Open Access: {self.open_access_url}")
        return "\n".join(parts)

    def to_bibtex(self) -> str:
        if self.authors:
            first_author_last = self.authors[0].split()[-1].lower()
        else:
            first_author_last = "unknown"
        year = self.year or "nd"
        title_word = re.sub(r"[^a-z]", "", self.title.split()[0].lower()) if self.title else "untitled"
        key = f"{first_author_last}{year}{title_word}"

        lines = [f"@article{{{key},"]
        lines.append(f'  title = {{{self.title}}},')
        if self.authors:
            lines.append(f'  author = {{{" and ".join(self.authors)}}},')
        if self.year:
            lines.append(f"  year = {{{self.year}}},")
        if self.journal:
            lines.append(f'  journal = {{{self.journal}}},')
        if self.doi:
            lines.append(f'  doi = {{{self.doi}}},')
        if self.url:
            lines.append(f'  url = {{{self.url}}},')
        lines.append("}")
        return "\n".join(lines)
