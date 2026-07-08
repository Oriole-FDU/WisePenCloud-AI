from __future__ import annotations

from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.tool_return import ToolReturn
from chat.application.tools.search_tools.web_search.core.errors import (
    WebSearchEmptyResult,
    WebSearchError,
    WebSearchInternalError,
    WebSearchNetworkError,
)
from chat.application.tools.search_tools.web_search.runtime_context_resolver import (
    WebSearchRuntimeContextResolver,
)
from chat.application.tools.search_tools.web_search.factories.platform_source_factory import (
    WebSearchPlatformSourceFactory,
)
from chat.application.tools.search_tools.web_search.pipeline.candidates_builder import build_candidates
from chat.application.tools.search_tools.web_search.providers.models import SearchMode
from chat.application.tools.search_tools.web_search.result_builder import build_search_tool_return
from chat.application.tools.search_tools.web_search.service import SearchService
from chat.application.tools.search_tools.web_search.tool_utils import select_recommended_ids

DEFAULT_SEARCH_RESULTS = 10
MAX_SEARCH_RESULTS = 20
MAX_RECOMMENDED_CANDIDATES = 5
FALLBACK_CANDIDATES_COUNT = 3
WEB_SEARCH_TOOL_TIMEOUT_SECONDS = 300.0

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Required. A concise search query for the platform search source.",
        },
        "mode": {
            "type": "string",
            "enum": [SearchMode.WEB.value, SearchMode.ACADEMIC.value],
            "default": SearchMode.WEB.value,
            "description": "Use academic only when the active platform member provider supports it.",
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_SEARCH_RESULTS,
            "default": DEFAULT_SEARCH_RESULTS,
            "description": "Maximum candidate results per search request.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


class PlatformSearchTool:
    """平台搜索工具：按平台默认/会员配置路由。"""

    __slots__ = (
        "_definition",
        "_platform_source_factory",
        "_runtime_context_resolver",
        "_service",
    )

    def __init__(
            self,
            *,
            service: SearchService,
            platform_source_factory: WebSearchPlatformSourceFactory,
            runtime_context_resolver: WebSearchRuntimeContextResolver,
    ) -> None:
        self._service = service
        self._platform_source_factory = platform_source_factory
        self._runtime_context_resolver = runtime_context_resolver
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="platform_search",
                description=(
                    "Search with the platform default or platform member search source and return ranked candidates.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - Use this as the general search tool when no specific provider is requested.\n"
                    "  - Set mode='academic' only for paper/literature requests; it requires an academic-capable platform member provider.\n"
                    "OUTPUT RULES:\n"
                    "  - Fetch selected candidate URLs with web_fetch before relying on results as evidence.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                timeout_seconds=WEB_SEARCH_TOOL_TIMEOUT_SECONDS,
                cache_chunked=False,
                required_context_keys=("user_id",),
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolReturn:
        query = kwargs["query"].strip()
        mode = SearchMode(str(kwargs.get("mode") or SearchMode.WEB.value))
        max_results = max(1, min(kwargs.get("max_results") or DEFAULT_SEARCH_RESULTS, MAX_SEARCH_RESULTS))

        search_config = await self._runtime_context_resolver.resolve_platform(
            user_id=str(context["user_id"]),
        )
        if (
                mode == SearchMode.ACADEMIC
                and (
                    search_config.provider is None
                    or not search_config.provider.supports_academic_mode
                )
        ):
            raise ToolExecutionError(
                reason="platform_search_academic_unavailable",
                detail_reason="platform academic search requires an academic-capable platform member provider.",
                retryable=False,
            )

        try:
            source = self._platform_source_factory.build(search_config)
            result = await self._service.search(
                query=query,
                max_results=max_results,
                source=source,
                mode=mode,
            )
            candidates = build_candidates(result.responses)
            if not candidates:
                raise WebSearchEmptyResult(provider=search_config.provider, reason="搜索没有返回结果")
        except WebSearchInternalError as exc:
            raise ToolExecutionError(
                reason="platform_search_unavailable",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
        except WebSearchNetworkError as exc:
            raise ToolExecutionError(
                reason="platform_search_network_error",
                detail_reason=str(exc),
                retryable=True,
            ) from exc
        except WebSearchEmptyResult as exc:
            raise ToolExecutionError(
                reason="platform_search_empty_result",
                detail_reason=str(exc),
                retryable=True,
            ) from exc
        except WebSearchError as exc:
            raise ToolExecutionError(
                reason="platform_search_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

        recommended_ids = await select_recommended_ids(
            search_query=query,
            candidates=candidates,
            max_recommended_candidates=MAX_RECOMMENDED_CANDIDATES,
            fallback_candidates_count=FALLBACK_CANDIDATES_COUNT,
        )
        return build_search_tool_return(
            result,
            candidates=candidates,
            responses=result.responses,
            tool_name="platform_search",
            mode=mode.value,
            recommended_ids=recommended_ids,
        )
