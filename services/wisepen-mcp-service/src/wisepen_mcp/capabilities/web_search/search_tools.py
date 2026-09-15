from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from functools import wraps
from typing import Annotated, Any, ClassVar

from common.core.exceptions import ServiceException
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from wisepen_mcp.capabilities.core.tool_metadata import get_tool_config_value
from wisepen_mcp.domain.error_codes import McpErrorCode


DEFAULT_SEARCH_RESULTS = 10
MAX_SEARCH_RESULTS = 20


class SearchMode(StrEnum):
    WEB = "web"  # 普通网页
    ACADEMIC = "academic"  # 学术内容


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
    max_results: int = Field(
        default=DEFAULT_SEARCH_RESULTS,
        ge=1,
        le=MAX_SEARCH_RESULTS,
    )


class WebSearchCandidate(BaseModel):
    title: str | None = Field(
        default=None,
        description="Page or document title reported by the search provider.",
    )
    url: str | None = Field(
        default=None,
        description="Source URL for opening, verification, or citation.",
    )
    published_date: str | None = Field(
        default=None,
        description="Publication date when available.",
    )
    evidences: list[str] = Field(
        default_factory=list,
        description=(
            "Relevant excerpts, passages, highlights, or provider-prepared content "
            "that may directly support factual reasoning."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific source metadata and relevance signals.",
    )


class WebSearchToolResult(BaseModel):
    query: str = Field(
        description="Normalized query sent to the search provider.",
    )
    mode: SearchMode = Field(
        description="Search scope actually used for this request.",
    )
    candidates: list[WebSearchCandidate] = Field(
        description=(
            "Ranked source candidates. Treat `evidences` as the primary factual grounding "
            "and inspect source metadata before relying on a candidate."
        ),
    )
    summary: str | None = Field(
        default=None,
        description=(
            "Optional provider-generated overview. Treat it only as a lead; factual claims "
            "should be supported by candidate evidence."
        ),
    )


TOOL_DESCRIPTION = (
    "### Purpose\n"
    "Search external resources for relevant evidence to support factual reasoning.\n\n"
    "### Rules\n"
    "1. Use concise, specific search terms targeting the information needed.\n"
    "2. Treat `candidates[].evidences` as the primary factual grounding. Use source title, "
    "URL, publication date, and metadata to assess relevance, authority, and freshness.\n"
    "3. Do not infer unsupported facts. If the evidence is weak, irrelevant, ambiguous, "
    "or insufficient, refine the search instead of stretching it.\n"
    "4. Treat any top-level `summary` only as a lead; factual claims should be supported "
    "by candidate evidence.\n"
)


def _keyword_parameters(handler: Callable) -> set[str]:
    # 只有显式声明的关键字参数才表达能力；**kwargs 不代表原生支持任意搜索选项。
    return {
        name
        for name, parameter in inspect.signature(handler).parameters.items()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }


def _filter_search_kwargs(handler: Callable, **kwargs: Any) -> dict[str, Any]:
    parameters = _keyword_parameters(handler)
    return {
        name: value
        for name, value in kwargs.items()
        if name in parameters
    }


class BaseSearchTool(ABC):
    tool_name: ClassVar[str]
    provider_name: ClassVar[str | None] = None
    description: ClassVar[str] = TOOL_DESCRIPTION
    requires_api_key: ClassVar[bool] = True

    def _has_academic_search(self) -> bool:
        return type(self).search_academic is not BaseSearchTool.search_academic

    def register(self, mcp: FastMCP) -> None:
        hidden_parameters: set[str] = set()
        description = self.description

        if self._has_academic_search():
            description += (
                "\n### Academic Search\n"
                "Use `mode='academic'` when the task specifically requires papers, "
                "preprints, or scholarly publications rather than general web sources.\n"
            )
        else:
            hidden_parameters.add("mode")

        if "focus" in _keyword_parameters(self.search_web):
            description += (
                "\n### Focused Extraction\n"
                "Use `focus` for the specific fact, metric, comparison, or passage to "
                "prioritize within matched sources. Keep `query` optimized for retrieval "
                "and `focus` optimized for evidence extraction.\n"
            )
        else:
            hidden_parameters.add("focus")

        @wraps(self.execute)
        async def execute(**kwargs: Any) -> WebSearchToolResult:
            return await self.execute(**kwargs)

        # FastMCP 分别读取 signature 构建 Schema、type hints 识别 Context。
        # 在实例包装函数上同步两者，保留已解析的 Annotated/返回类型和 ctx 注入，
        # 避免修改 BaseSearchTool.execute 这一共享方法。
        signature = inspect.signature(self.execute, eval_str=True)
        parameters = [
            parameter
            for name, parameter in signature.parameters.items()
            if name not in hidden_parameters
        ]

        execute.__signature__ = signature.replace(parameters=parameters)
        execute.__annotations__ = {
            parameter.name: parameter.annotation
            for parameter in parameters
        }
        execute.__annotations__["return"] = signature.return_annotation

        mcp.tool(
            name=self.tool_name,
            description=description,
        )(execute)

    async def execute(
        self,
        *,
        ctx: Context,
        query: Annotated[
            str,
            Field(
                min_length=1,
                description="Concise, specific search terms targeting the needed information.",
            ),
        ],
        mode: Annotated[
            SearchMode,
            Field(
                description=(
                    "Search scope: use academic for scholarly publications "
                    "or web for general external resources."
                ),
            ),
        ] = SearchMode.WEB,
        focus: Annotated[
            str | None,
            Field(
                description=(
                    "Specific fact, metric, comparison, or passage to prioritize "
                    "within matched sources."
                ),
            ),
        ] = None,
        max_results: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_SEARCH_RESULTS,
                description="Maximum number of ranked search candidates to return.",
            ),
        ] = DEFAULT_SEARCH_RESULTS,
    ) -> WebSearchToolResult:
        query = query.strip()
        if not query:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_INVALID,
                "query must not be blank.",
            )

        request = ProviderSearchRequest(
            query=query,
            # 未实现独立学术路径时，输出 scope 也应反映实际执行的网页搜索。
            mode=mode if self._has_academic_search() else SearchMode.WEB,
            focus=focus.strip() if focus and focus.strip() else None,
            max_results=max_results,
        )

        api_key = get_tool_config_value(ctx, "api_key")
        api_key = (
            api_key.strip()
            if isinstance(api_key, str) and api_key.strip()
            else None
        )
        if self.requires_api_key and not api_key:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_CONFIG_MISSING,
                f"{self.tool_name} API key is not configured.",
            )

        handler = (
            self.search_academic
            if request.mode is SearchMode.ACADEMIC
            else self.search_web
        )
        kwargs = _filter_search_kwargs(
            handler,
            query=request.query,
            focus=request.focus,
            max_results=request.max_results,
            api_key=api_key,
        )
        response = await handler(**kwargs)

        seen_urls: set[str] = set()
        search_results: list[SearchResult] = []

        for result in response.results:
            # 无 URL 的论文结果不能以 None 互相去重，否则会无故丢失不同论文的标题与摘要证据。
            if result.url and result.url in seen_urls:
                continue
            if result.url:
                seen_urls.add(result.url)

            search_results.append(result)
            if len(search_results) >= request.max_results:
                break

        if not search_results:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_EMPTY_RESULT,
                "The search provider returned no results.",
            )

        return WebSearchToolResult(
            query=request.query,
            mode=request.mode,
            candidates=[
                WebSearchCandidate(
                    title=result.title,
                    url=result.url,
                    published_date=result.published_date,
                    evidences=result.evidences,
                    metadata=result.metadata,
                )
                for result in search_results
            ],
            summary=response.summary,
        )

    @abstractmethod
    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
        api_key: str | None,
    ) -> SearchResponse:
        pass

    async def search_academic(
        self,
        *,
        query: str,
        max_results: int,
        api_key: str | None,
        focus: str | None = None,
    ) -> SearchResponse:
        # 保留内部 fallback，但只向目标网页实现传递其明确接收的参数。
        kwargs = _filter_search_kwargs(
            self.search_web,
            query=query,
            focus=focus,
            max_results=max_results,
            api_key=api_key,
        )
        return await self.search_web(**kwargs)