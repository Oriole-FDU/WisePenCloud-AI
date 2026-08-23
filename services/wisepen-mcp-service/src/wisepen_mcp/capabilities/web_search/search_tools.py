from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, ClassVar

from common.core.exceptions import ServiceException
from common.logger import warn
from common.utils.ranking import RankCandidate, RankingPipeline, RankQuery, RankRequest
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

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

class WebSearchCandidate(BaseModel):
    candidate_id: str = Field(description="Result label to use when referring to this candidate.")
    title: str | None = Field(default=None, description="Page or document title reported by the search provider.")
    url: str | None = Field(default=None, description="Source URL for opening or citing the result.")
    snippet: str | None = Field(default=None, description="Provider excerpt for judging whether the source is relevant.")
    highlights: list[str] | None = Field(default=None, description="Additional excerpts that directly matched the search.")


class WebSearchToolResult(BaseModel):
    query: str = Field(description="Normalized query that was sent to the search provider.")
    mode: SearchMode = Field(description="Search scope used for this request.")
    candidates: list[WebSearchCandidate] = Field(description="Search evidence ordered by rerank relevance; inspect each candidate's URL and excerpts before relying on it.")
    supplier_answer: str | None = Field(default=None, description=("Optional provider-generated summary. Treat it as a lead and verify it against the returned candidates."))


DEFAULT_TOP_K = 10
MAX_TOP_K = 10
MAX_SEARCH_RESULTS = 50
TOOL_DESCRIPTION = (
    "Description:\n"
    "Search external information. query controls what the provider retrieves. "
    "ranking_query controls how the returned candidates are reranked; prefer a "
    "natural-language question, such as 'Why Attention Efficient?'. "
    "Use academic mode for literature search; providers without native academic "
    "support fall back to web search.\n"
    "Output:\n"
    "Returns reranked source candidates with URLs and excerpts. Use those "
    "candidates as evidence. supplier_answer, when present, is only a provider summary "
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

    __slots__ = ("_ranking_pipeline",)

    def __init__(self, *, ranking_pipeline: RankingPipeline) -> None:
        self._ranking_pipeline = ranking_pipeline

    def register(self, mcp: FastMCP) -> None:
        mcp.tool(name=self.tool_name, description=self.description)(self.execute)

    async def execute(
        self,
        *,
        ctx: Context,
        query: Annotated[str, Field(min_length=1, description="Concise keywords sent to the search provider.")],
        ranking_query: Annotated[str, Field(min_length=1, description="Full information need used for reranking. Prefer a natural-language question, such as 'Why Attention Efficient?'.")],
        mode: Annotated[SearchMode, Field(description="Use academic for literature search; unsupported providers fall back to web.")],
        top_k: Annotated[int, Field(ge=1, le=MAX_TOP_K, description="Number of reranked search candidates to return.")] = DEFAULT_TOP_K,
    ) -> WebSearchToolResult:
        query = query.strip()
        ranking_query = ranking_query.strip()
        if not query or not ranking_query:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_INVALID,
                "query and ranking_query must not be blank.",
            )

        api_key = get_tool_config_value(ctx, "api_key")
        api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        if self.requires_api_key and not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CONFIG_MISSING,f"{self.tool_name} API key is not configured.",)

        # Provider 负责较大候选集召回，top_k 只控制重排后的模型可见结果数。
        if mode is SearchMode.ACADEMIC:
            response = await self.search_academic(query=query, max_results=MAX_SEARCH_RESULTS, api_key=api_key)
        else:
            response = await self.search_web(query=query, max_results=MAX_SEARCH_RESULTS, api_key=api_key)

        seen_urls: set[str | None] = set()
        search_results: list[SearchResult] = []
        for result in response.results:
            if result.url in seen_urls: continue
            seen_urls.add(result.url)
            search_results.append(result)
            if len(search_results) >= MAX_SEARCH_RESULTS:
                break

        candidates_by_id = {f"[{index}]": result for index, result in enumerate(search_results, 1)}
        if not candidates_by_id:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_EMPTY_RESULT,
                "The search provider returned no results.",
            )

        rank_candidates = tuple(
            RankCandidate(
                candidate_id=candidate_id,
                text="\n".join(
                    part
                    for part in (
                        f"Title: {result.title}" if result.title else None,
                        f"Snippet: {result.snippet}" if result.snippet else None,
                        *(f"Highlight: {highlight}" for highlight in result.highlights or ()),
                    )
                    if part
                ),
            )
            for candidate_id, result in candidates_by_id.items()
        )
        rank_request = RankRequest(
            query=RankQuery(text=ranking_query),
            candidates=rank_candidates,
            top_k=top_k,
            candidate_limit=len(rank_candidates),
        )
        try:
            ranked_candidates = (await self._ranking_pipeline.arank(rank_request)).ranked
            ordered_candidate_ids = tuple(item.candidate_id for item in ranked_candidates)
        except Exception as exc:  # noqa: BLE001 - 重排服务失败时保留供应商证据
            warn(
                "web search reranking failed; returning provider order.",
                tool=self.tool_name,
                exc=exc,
            )
            # 重排不可用时仍遵守模型请求的 top_k，避免把内部召回数量暴露给模型。
            ordered_candidate_ids = tuple(candidates_by_id)[:top_k]

        return WebSearchToolResult(
            query=query,
            mode=mode,
            candidates=[
                WebSearchCandidate(
                    candidate_id=candidate_id,
                    title=result.title,
                    url=result.url,
                    snippet=result.snippet,
                    highlights=result.highlights,
                )
                for candidate_id in ordered_candidate_ids
                for result in (candidates_by_id[candidate_id],)
            ],
            supplier_answer=response.answer,
        )

    @abstractmethod
    async def search_web(self, *, query: str, max_results: int, api_key: str | None) -> SearchResponse:
        pass

    async def search_academic(self, *, query: str, max_results: int, api_key: str | None) -> SearchResponse:
        return await self.search_web(query=query, max_results=max_results, api_key=api_key)
