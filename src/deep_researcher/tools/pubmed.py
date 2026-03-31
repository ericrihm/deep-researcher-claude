from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from deep_researcher.models import Paper
from deep_researcher.tools.base import Tool

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class PubMedSearchTool(Tool):
    name = "search_pubmed"
    description = (
        "Search PubMed for biomedical and life sciences literature. Covers 36M+ "
        "citations including biomedicine, health, genomics, and related fields. "
        "Use this for medical, biological, pharmaceutical, and health-related research."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query for PubMed."},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (default 10, max 20).",
            },
        },
        "required": ["query"],
    }

    def execute(self, query: str, max_results: int = 10) -> str:
        max_results = min(max_results, 20)

        try:
            search_resp = httpx.get(
                f"{EUTILS_BASE}/esearch.fcgi",
                params={"db": "pubmed", "term": query, "retmax": max_results, "retmode": "json"},
                timeout=30,
            )
            search_resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"Error searching PubMed: {e}"

        search_data = search_resp.json()
        id_list = search_data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return "No papers found on PubMed for this query."

        try:
            fetch_resp = httpx.get(
                f"{EUTILS_BASE}/efetch.fcgi",
                params={"db": "pubmed", "id": ",".join(id_list), "retmode": "xml"},
                timeout=30,
            )
            fetch_resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"Error fetching PubMed details: {e}"

        papers = _parse_pubmed_xml(fetch_resp.text)
        lines = [f"Found {len(papers)} papers on PubMed:\n"]
        for i, p in enumerate(papers, 1):
            lines.append(f"{i}. {p.to_summary()}\n")
        return "\n".join(lines)


def _parse_pubmed_xml(xml_text: str) -> list[Paper]:
    root = ET.fromstring(xml_text)
    papers = []
    for article in root.findall(".//PubmedArticle"):
        medline = article.find(".//MedlineCitation")
        if medline is None:
            continue

        article_el = medline.find(".//Article")
        if article_el is None:
            continue

        title_el = article_el.find(".//ArticleTitle")
        title = _get_text(title_el)
        if not title:
            continue

        abstract_el = article_el.find(".//Abstract/AbstractText")
        abstract = _get_text(abstract_el)

        authors = []
        for author_el in article_el.findall(".//AuthorList/Author"):
            last = _get_text(author_el.find("LastName"))
            first = _get_text(author_el.find("ForeName"))
            if last:
                name = f"{first} {last}".strip() if first else last
                authors.append(name)

        year = None
        year_el = article_el.find(".//Journal/JournalIssue/PubDate/Year")
        if year_el is not None and year_el.text:
            try:
                year = int(year_el.text)
            except ValueError:
                pass

        journal_el = article_el.find(".//Journal/Title")
        journal = _get_text(journal_el)

        pmid_el = medline.find(".//PMID")
        pmid = _get_text(pmid_el)

        doi = None
        for id_el in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
            if id_el.get("IdType") == "doi":
                doi = id_el.text
                break

        papers.append(
            Paper(
                title=title,
                authors=authors,
                year=year,
                abstract=abstract,
                doi=doi,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                source="pubmed",
                journal=journal,
                pmid=pmid,
            )
        )
    return papers


def _get_text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()
