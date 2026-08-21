from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, ClassVar

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from common.core.exceptions import ServiceException
from common.logger import warn
from common.utils.ranking import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingPipeline,
)
from wisepen_mcp.capabilities.core.tool_metadata import get_tool_config_value
from wisepen_mcp.domain.error_codes import McpErrorCode


class SearchMode(StrEnum):
    WEB = "web" # 普通网页
    ACADEMIC = "academic" # 学术内容


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    highlights: list[str] | None = None


@dataclass(frozen=True, slots=True)
class SearchResponse:
    results: list[SearchResult]
    answer: str | None = None

class WebSearchCandidateResult(BaseModel):
    candidate_id: str = Field(description="Result label to use when referring to this candidate.")
    title: str | None = Field(default=None, description="Page or document title reported by the search provider.")
    url: str | None = Field(default=None, description="Source URL for opening or citing the result.")
    snippet: str | None = Field(default=None, description="Provider excerpt for judging whether the source is relevant.")
    highlights: list[str] | None = Field(default=None, description="Additional excerpts that directly matched the search.")


class WebSearchToolResult(BaseModel):
    query: str = Field(description="Normalized query that was sent to the search provider.")
    mode: SearchMode = Field(description="Search scope used for this request.")
    candidates: list[WebSearchCandidateResult] = Field(description="Search evidence ordered by relevance to ranking_query; inspect each candidate's URL and excerpts before relying on it.")
    supplier_answer: str | None = Field(default=None, description=("Optional provider-generated summary. Treat it as a lead and verify it against the returned candidates."))


DEFAULT_SEARCH_RESULTS = 10
MAX_SEARCH_RESULTS = 20
TOOL_DESCRIPTION = (
    "Description:\n"
    "Search external information. search_query controls what the provider retrieves, "
    "while ranking_query describes the full information need used to reorder results. "
    "Use academic mode for literature search; providers without native academic "
    "support fall back to web search.\n"
    "Output:\n"
    "Returns relevance-ordered source candidates with URLs and excerpts. Use those "
    "candidates as evidence. supplier_answer, when present, is only a provider summary "
    "and should be checked against the sources. In the final response, every conclusion "
    "supported by a returned URL must cite it with an inline Markdown link in the form "
    "[brief description, usually the official website name](exact URL)."
)

class BaseSearchTool(ABC):
    tool_name: ClassVar[str]
    provider_name: ClassVar[str | None] = None
    description: ClassVar[str] = TOOL_DESCRIPTION
    requires_api_key: ClassVar[bool] = True

    __slots__ = ("_ranking_pipeline",)

    def __init__(self, *, ranking_pipeline: RankingPipeline) -> None:
        self._ranking_pipeline = ranking_pipeline

    def register(self, mcp: FastMCP) -> None:
        mcp.tool(name=self.tool_name, description=self.description)(self.execute)

    async def execute(
        self,
        *,
        ctx: Context,
        search_query: Annotated[str, Field(min_length=1, description="Concise keywords sent to the search provider.")],
        ranking_query: Annotated[str, Field(min_length=1, description="Complete natural-language question used to rank the returned candidates, such as 'What is the best way to learn Python programming?'")],
        mode: Annotated[SearchMode, Field(description="Use academic for literature search; unsupported providers fall back to web.")],
        max_results: Annotated[int, Field(ge=1, le=MAX_SEARCH_RESULTS, description="Maximum number of search candidates to return.")]
    ) -> WebSearchToolResult:
        search_query = search_query.strip()
        ranking_query = ranking_query.strip()
        if not search_query or not ranking_query:
            raise ServiceException(McpErrorCode.WEB_SEARCH_INVALID, "search_query and ranking_query must not be blank.")

        api_key = get_tool_config_value(ctx, "api_key")
        api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        if self.requires_api_key and not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CONFIG_MISSING,f"{self.tool_name} API key is not configured.",)

        if mode is SearchMode.ACADEMIC:
            response = await self.search_academic(query=search_query, max_results=max_results, api_key=api_key)
        else:
            response = await self.search_web(query=search_query, max_results=max_results, api_key=api_key)

        seen_urls: set[str | None] = set()
        search_results: list[SearchResult] = []
        for result in response.results:
            if result.url in seen_urls: continue
            seen_urls.add(result.url)
            search_results.append(result)
            if len(search_results) >= max_results: break

        # 分配候选项 ID
        candidates_by_id = { f"[{index}]": result for index, result in enumerate(search_results, 1)}
        if not candidates_by_id: raise ServiceException(McpErrorCode.WEB_SEARCH_EMPTY_RESULT,"The search provider returned no results.")

        # 重排序
        rank_candidates: list[RankCandidate] = []
        for candidate_id, result in candidates_by_id.items():
            ranking_text_parts: list[str] = []

            if result.title: ranking_text_parts.append(f"Title: {result.title}")
            if result.snippet: ranking_text_parts.append(f"Snippet: {result.snippet}")
            for highlight in result.highlights or (): ranking_text_parts.append(f"Highlight: {highlight}")

            rank_candidates.append(RankCandidate(
                candidate_id=candidate_id,
                text="\n".join(ranking_text_parts),
                fields={
                    "title": result.title or "",
                    "snippet": result.snippet or "",
                    "highlights": "\n".join(result.highlights or ()),
                },
            ))

        rank_request = RankRequest(
            query=RankQuery(text=ranking_query),
            candidates=tuple(rank_candidates),
            top_k=len(rank_candidates),
            candidate_limit=len(rank_candidates),
        )
        try:
            ranked = await self._ranking_pipeline.arank(rank_request)
            ranked_candidates = ranked.ranked
        except Exception as exc:  # noqa: BLE001 - 排序增强失败不能丢弃搜索证据
            # ZeroEntropy 或其它排序组件不可用时，搜索结果仍是有效的原始证据。
            warn(
                "web search ranking failed; returning provider order.",
                tool=self.tool_name,
                exc=exc,
            )
            ranked_candidates = tuple(rank_candidates)

        return WebSearchToolResult(
            query=search_query,
            mode=mode,
            candidates=[
                WebSearchCandidateResult(
                    candidate_id=item.candidate_id,
                    title=candidates_by_id[item.candidate_id].title,
                    url=candidates_by_id[item.candidate_id].url,
                    snippet=candidates_by_id[item.candidate_id].snippet,
                    highlights=candidates_by_id[item.candidate_id].highlights,
                )
                for item in ranked_candidates
            ],
            supplier_answer=response.answer,
        )

    @abstractmethod
    async def search_web(self, *, query: str, max_results: int, api_key: str | None) -> SearchResponse:
        pass

    async def search_academic(self, *, query: str, max_results: int, api_key: str | None) -> SearchResponse:
        return await self.search_web(query=query, max_results=max_results, api_key=api_key)
