"""
tools.py
--------
Research and external tools used by the Research agent.

Design goals:
  - Extensible toolkit (web search, future: PDF, DB, SharePoint, MCP)
  - Safe to run with no external API keys (LLM-knowledge-only mode)
  - Clear, structured tool results the researcher prompt can consume
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import settings
from models import SourceInfo
from utils import get_logger, log_event, log_error, truncate_text

logger = get_logger(__name__)


# =============================================================================
# Tool result shapes
# =============================================================================


class ToolHit:
    """Single search/document hit normalized across providers."""

    def __init__(
        self,
        title: str,
        url: Optional[str] = None,
        snippet: str = "",
        source_type: str = "web",
        raw: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.title = title
        self.url = url
        self.snippet = snippet
        self.source_type = source_type
        self.raw = raw or {}

    def to_source_info(self) -> SourceInfo:
        return SourceInfo(
            title=self.title,
            url=self.url,
            source_type=self.source_type,
            snippet=truncate_text(self.snippet, 500),
            accessed_at=datetime.now(timezone.utc),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source_type": self.source_type,
        }


# =============================================================================
# Base tool interface
# =============================================================================


class BaseResearchTool(ABC):
    """Interface for any research backend."""

    name: str = "base"
    description: str = "Base research tool"

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this tool can be used with current config/credentials."""

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[ToolHit]:
        """Run a search / lookup and return normalized hits."""

    def safe_search(self, query: str, max_results: int = 5) -> List[ToolHit]:
        """search() with error isolation — failures return [] instead of raising."""
        if not self.is_available():
            logger.debug("Tool %s not available; skipping query=%r", self.name, query)
            return []
        try:
            hits = self.search(query=query, max_results=max_results)
            log_event("tool_search_completed", tool=self.name, query=query, hits=len(hits))
            return hits
        except Exception as exc:  # noqa: BLE001 — tools must not crash the workflow
            log_error("tool_search_failed", exc, tool=self.name, query=query)
            return []


# =============================================================================
# Tavily web search
# =============================================================================


class TavilySearchTool(BaseResearchTool):
    """
    Web search via Tavily API.
    https://docs.tavily.com/

    Requires: TAVILY_API_KEY and ENABLE_WEB_SEARCH=true
    """

    name = "tavily"
    description = "Tavily web search"

    def is_available(self) -> bool:
        return bool(settings.enable_web_search and settings.tavily_api_key)

    def search(self, query: str, max_results: int = 5) -> List[ToolHit]:
        # Import lazily so the project runs without tavily installed when unused
        try:
            from tavily import TavilyClient  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "tavily-python is not installed. Add it to requirements or disable web search."
            ) from exc

        client = TavilyClient(api_key=settings.tavily_api_key)
        response = client.search(
            query=query,
            max_results=max_results,
            include_answer=False,
            include_raw_content=False,
        )
        results = response.get("results") or []
        hits: List[ToolHit] = []
        for item in results:
            hits.append(
                ToolHit(
                    title=item.get("title") or "Untitled",
                    url=item.get("url"),
                    snippet=item.get("content") or item.get("snippet") or "",
                    source_type="web",
                    raw=item,
                )
            )
        return hits


# =============================================================================
# SerpAPI web search (optional alternative)
# =============================================================================


class SerpApiSearchTool(BaseResearchTool):
    """
    Google results via SerpAPI.
    https://serpapi.com/

    Requires: SERPAPI_API_KEY and ENABLE_WEB_SEARCH=true
    """

    name = "serpapi"
    description = "SerpAPI Google search"

    def is_available(self) -> bool:
        return bool(settings.enable_web_search and settings.serpapi_api_key)

    def search(self, query: str, max_results: int = 5) -> List[ToolHit]:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError("requests is required for SerpAPI search") from exc

        endpoint = "https://serpapi.com/search.json"
        params = {
            "q": query,
            "api_key": settings.serpapi_api_key,
            "num": max_results,
            "engine": "google",
        }
        resp = requests.get(endpoint, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        organic = data.get("organic_results") or []
        hits: List[ToolHit] = []
        for item in organic[:max_results]:
            hits.append(
                ToolHit(
                    title=item.get("title") or "Untitled",
                    url=item.get("link"),
                    snippet=item.get("snippet") or "",
                    source_type="web",
                    raw=item,
                )
            )
        return hits


# =============================================================================
# Placeholder / future tools (stubs keep architecture extensible)
# =============================================================================


class DocumentSearchTool(BaseResearchTool):
    """
    Future: search local PDFs / company docs / vector store.
    Not active yet — always unavailable until implemented.
    """

    name = "documents"
    description = "Internal document search (not configured)"

    def is_available(self) -> bool:
        return False

    def search(self, query: str, max_results: int = 5) -> List[ToolHit]:
        return []


class DatabaseQueryTool(BaseResearchTool):
    """Future: SQL / warehouse lookups."""

    name = "database"
    description = "Database query tool (not configured)"

    def is_available(self) -> bool:
        return False

    def search(self, query: str, max_results: int = 5) -> List[ToolHit]:
        return []


# =============================================================================
# Toolkit facade used by the Research agent
# =============================================================================


class ResearchToolkit:
    """
    Aggregates all research tools and exposes a simple API:

        toolkit = ResearchToolkit()
        text = toolkit.gather_for_questions(["What is ...?", "How does ...?"])
    """

    def __init__(self, tools: Optional[List[BaseResearchTool]] = None) -> None:
        if tools is not None:
            self.tools = tools
        else:
            # Order = preference when multiple are available
            self.tools = [
                TavilySearchTool(),
                SerpApiSearchTool(),
                DocumentSearchTool(),
                DatabaseQueryTool(),
            ]

    def available_tools(self) -> List[BaseResearchTool]:
        return [t for t in self.tools if t.is_available()]

    def any_available(self) -> bool:
        return len(self.available_tools()) > 0

    def search_all(self, query: str, max_results: int = 5) -> List[ToolHit]:
        """
        Query all available tools and merge hits.
        Deduplicate by URL when possible.
        """
        merged: List[ToolHit] = []
        seen_urls: set[str] = set()

        available = self.available_tools()
        if not available:
            logger.info("No external research tools available; using LLM knowledge only")
            return []

        for tool in available:
            for hit in tool.safe_search(query=query, max_results=max_results):
                url_key = (hit.url or "").strip().lower()
                if url_key and url_key in seen_urls:
                    continue
                if url_key:
                    seen_urls.add(url_key)
                merged.append(hit)

        return merged

    def gather_for_questions(
        self,
        questions: List[str],
        max_results_per_question: int = 4,
    ) -> str:
        """
        Run research for each question and return a single text block
        suitable for injection into the researcher user prompt.
        """
        if not questions:
            return "None"

        if not self.any_available():
            return (
                "None — no external search tools configured. "
                "Use reliable general knowledge and set source_type to "
                '"llm_knowledge".'
            )

        sections: List[str] = []
        for idx, question in enumerate(questions, start=1):
            hits = self.search_all(question, max_results=max_results_per_question)
            if not hits:
                sections.append(
                    f"Q{idx}. {question}\n  Results: (no hits)"
                )
                continue

            lines = [f"Q{idx}. {question}", "  Results:"]
            for h_i, hit in enumerate(hits, start=1):
                lines.append(
                    f"  [{h_i}] {hit.title}\n"
                    f"      url: {hit.url or 'n/a'}\n"
                    f"      snippet: {truncate_text(hit.snippet, 400)}"
                )
            sections.append("\n".join(lines))

        payload = "\n\n".join(sections)
        log_event(
            "research_toolkit_gather_completed",
            questions=len(questions),
            chars=len(payload),
            tools=[t.name for t in self.available_tools()],
        )
        return payload

    def gather_as_json(
        self,
        questions: List[str],
        max_results_per_question: int = 4,
    ) -> str:
        """Same as gather_for_questions but JSON-serialized (optional use)."""
        if not self.any_available():
            return json.dumps({"available": False, "results": []})

        results = []
        for question in questions:
            hits = self.search_all(question, max_results=max_results_per_question)
            results.append(
                {
                    "question": question,
                    "hits": [h.to_dict() for h in hits],
                }
            )
        return json.dumps({"available": True, "results": results}, indent=2)

    def hits_to_sources(self, hits: List[ToolHit]) -> List[SourceInfo]:
        return [h.to_source_info() for h in hits]


# =============================================================================
# Module-level convenience
# =============================================================================

# Shared default toolkit (stateless; safe to reuse)
default_toolkit = ResearchToolkit()


def get_research_toolkit() -> ResearchToolkit:
    """Factory hook for DI / testing."""
    return ResearchToolkit()