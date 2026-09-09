from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any, ClassVar

from common.core.exceptions import ServiceException
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from wisepen_mcp.capabilities.core.tool_metadata import get_tool_config_value
from wisepen_mcp.domain.error_codes import McpErrorCode


class SearchMode(StrEnum):
    WEB = "web" # 普通网页
    ACADEMIC = "academic" # 学术内容


DEFAULT_SEARCH_RESULTS = 10
MAX_SEARCH_RESULTS = 20


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str | None = None
    url: str | None = None
    published_date: str | None = None
    evidences: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchResponse:
    results: list[SearchResult]
    summary: str | None = None


class ProviderSearchRequest(BaseModel):
    """Provider 无关的搜索意图；不暴露供应商成本或深度旋钮。"""

    query: str = Field(min_length=1)
    mode: SearchMode = SearchMode.WEB
    focus: str | None = None
    max_results: int = Field(default=10, ge=1, le=MAX_SEARCH_RESULTS)

class WebSearchCandidate(BaseModel):
    candidate_id: str = Field(description="Result label to use when referring to this candidate.")
    title: str | None = Field(default=None, description="Page or document title reported by the search provider.")
    url: str | None = Field(default=None, description="Source URL for opening or citing the result.")
    published_date: str | None = None
    evidences: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebSearchToolResult(BaseModel):
    query: str = Field(description="Normalized query that was sent to the search provider.")
    mode: SearchMode = Field(description="Search scope used for this request.")
    candidates: list[WebSearchCandidate] = Field(description="Search evidence in provider order; inspect each candidate's URL and excerpts before relying on it.")
    summary: str | None = Field(default=None, description="Optional provider summary; treat it only as a lead.")


TOOL_DESCRIPTION = (
    "Description:\n"
    "Search external information. query controls what the provider retrieves. "
    "Use academic mode for literature search; providers without native academic "
    "support fall back to web search.\n"
    "Output:\n"
    "Returns source candidates with URLs and excerpts in provider order. Use those "
    "candidates as evidence. summary, when present, is only a provider lead "
    "and should be checked against the sources. In the final response, "
    "Every claim supported by a returned URL must be cited immediately after the relevant statement "
    "with an inline Markdown link in the form"
    "[brief description, usually the official website name](exact URL)"
)

class BaseSearchTool(ABC):
    tool_name: ClassVar[str]
    provider_name: ClassVar[str | None] = None
    description: ClassVar[str] = TOOL_DESCRIPTION
    requires_api_key: ClassVar[bool] = True

    def register(self, mcp: FastMCP) -> None:
        mcp.tool(name=self.tool_name, description=self.description)(self.execute)

    async def execute(
        self,
        *,
        ctx: Context,
        query: Annotated[str, Field(min_length=1, description="Concise keywords sent to the search provider.")],
        mode: Annotated[SearchMode, Field(description="Use academic for literature search; unsupported providers fall back to web.")],
        focus: Annotated[str | None, Field(description="Specific fact or passage to extract from matched pages.")] = None,
        max_results: Annotated[int, Field(ge=1, le=MAX_SEARCH_RESULTS, description="Maximum number of search candidates to return.")] = DEFAULT_SEARCH_RESULTS,
    ) -> WebSearchToolResult:
        query = query.strip()
        if not query:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_INVALID,
                "query must not be blank.",
            )

        api_key = get_tool_config_value(ctx, "api_key")
        api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        if self.requires_api_key and not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CONFIG_MISSING,f"{self.tool_name} API key is not configured.",)

        if mode is SearchMode.ACADEMIC:
            response = await self.search_academic(query=query, focus=focus, max_results=max_results, api_key=api_key)
        else:
            response = await self.search_web(query=query, focus=focus, max_results=max_results, api_key=api_key)

        seen_urls: set[str | None] = set()
        search_results: list[SearchResult] = []
        for result in response.results:
            if result.url in seen_urls: continue
            seen_urls.add(result.url)
            search_results.append(result)
            if len(search_results) >= max_results:
                break

        candidates_by_id = {f"[{index}]": result for index, result in enumerate(search_results, 1)}
        if not candidates_by_id:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_EMPTY_RESULT,
                "The search provider returned no results.",
            )

        return WebSearchToolResult(
            query=query,
            mode=mode,
            candidates=[
                WebSearchCandidate(
                    candidate_id=candidate_id,
                    title=result.title,
                    url=result.url,
                    published_date=result.published_date,
                    evidences=result.evidences,
                    metadata=result.metadata,
                )
                for candidate_id in candidates_by_id
                for result in (candidates_by_id[candidate_id],)
            ],
            summary=response.summary,
        )

    @abstractmethod
    async def search_web(self, *, query: str, focus: str | None, max_results: int, api_key: str | None) -> SearchResponse:
        pass

    async def search_academic(self, *, query: str, focus: str | None, max_results: int, api_key: str | None) -> SearchResponse:
        return await self.search_web(query=query, focus=focus, max_results=max_results, api_key=api_key)
