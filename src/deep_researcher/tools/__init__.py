from __future__ import annotations

from deep_researcher.config import Config
from deep_researcher.tools.arxiv_search import ArxivSearchTool
from deep_researcher.tools.base import ToolRegistry
from deep_researcher.tools.core_search import CoreSearchTool
from deep_researcher.tools.crossref import CrossrefSearchTool
from deep_researcher.tools.open_access import OpenAccessTool
from deep_researcher.tools.openalex import OpenAlexSearchTool
from deep_researcher.tools.paper_details import PaperDetailsTool
from deep_researcher.tools.pubmed import PubMedSearchTool
from deep_researcher.tools.semantic_scholar import GetCitationsTool, SemanticScholarSearchTool


def build_tool_registry(config: Config) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ArxivSearchTool())
    registry.register(SemanticScholarSearchTool())
    registry.register(OpenAlexSearchTool(email=config.email))
    registry.register(CrossrefSearchTool(email=config.email))
    registry.register(PubMedSearchTool())
    registry.register(CoreSearchTool(api_key=config.core_api_key))
    registry.register(PaperDetailsTool())
    registry.register(GetCitationsTool())
    registry.register(OpenAccessTool(email=config.email))
    return registry
