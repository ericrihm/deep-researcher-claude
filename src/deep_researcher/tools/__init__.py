from __future__ import annotations

from deep_researcher.config import Config
from deep_researcher.tools.arxiv_search import ArxivSearchTool
from deep_researcher.tools.base import ToolRegistry
from deep_researcher.tools.core_search import CoreSearchTool
from deep_researcher.tools.crossref import CrossrefSearchTool
from deep_researcher.tools.ieee_xplore import IEEEXploreSearchTool
from deep_researcher.tools.scopus import ScopusSearchTool
from deep_researcher.tools.open_access import OpenAccessTool
from deep_researcher.tools.openalex import OpenAlexSearchTool
from deep_researcher.tools.paper_details import PaperDetailsTool
from deep_researcher.tools.pubmed import PubMedSearchTool
from deep_researcher.tools.semantic_scholar import GetCitationsTool, SemanticScholarSearchTool


def build_tool_registry(config: Config) -> ToolRegistry:
    registry = ToolRegistry()
    tools = [
        ArxivSearchTool(),
        SemanticScholarSearchTool(),
        OpenAlexSearchTool(email=config.email),
        CrossrefSearchTool(email=config.email),
        PubMedSearchTool(),
        CoreSearchTool(api_key=config.core_api_key),
        ScopusSearchTool(api_key=config.scopus_api_key),
        IEEEXploreSearchTool(api_key=config.ieee_api_key),
        PaperDetailsTool(),
        GetCitationsTool(),
        OpenAccessTool(email=config.email),
    ]
    for tool in tools:
        tool.set_year_range(config.start_year, config.end_year)
        registry.register(tool)
    return registry
