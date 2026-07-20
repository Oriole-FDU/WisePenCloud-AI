from __future__ import annotations

from typing import Any

from chat.application.tools.core import (
    ToolConfigSpec,
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from ..services.models import (
    SearchMode,
    SearchProviderName,
)
from ..services.pipeline import SearchPipeline
from ..services.providers.base import (
    SearchProviderCredentialError,
    SearchProviderError,
    SearchProviderNetworkError,
)
from ..services.sources import SearchSourceFactory

DEFAULT_SEARCH_RESULTS = 10
MAX_SEARCH_RESULTS = 20
WEB_SEARCH_TIMEOUT_SECONDS = 300.0

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "object",
            "properties": {
                "search_query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Concise keywords for the search provider.",
                },
                "ranking_query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "A complete natural-language question describing the information to rank by.",
                },
            },
            "required": ["search_query", "ranking_query"],
            "additionalProperties": False,
        },
        "mode": {
            "type": "string",
            "enum": [SearchMode.WEB.value, SearchMode.ACADEMIC.value],
            "default": SearchMode.WEB.value,
            "description": (
                "Use academic for literature search; unsupported sources "
                "fall back to web."
            ),
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
TOOL_DESCRIPTION = (
    "Search external web information and return candidates ordered by relevance. "
    "Provide concise provider keywords in search_query and a complete natural-"
    "language question in ranking_query. Use academic mode for literature search; "
    "sources without native academic support fall back to web search."
)
API_KEY_CONFIG_SPEC = ToolConfigSpec(
    schema={
        "type": "object",
        "properties": {
            "api_key": {
                "type": "string",
                "title": "API Key",
                "description": "API key for the configured search source.",
                "writeOnly": True,
            },
        },
        "additionalProperties": False,
    },
    required_keys=("api_key",),
    secret_keys=("api_key",),
)


class BaseWebSearchTool:
    """Web Search 工具共享的执行门面。"""

    def __init__(
        self,
        *,
        tool_name: str,
        provider: SearchProviderName | None,
        search_pipeline: SearchPipeline,
        source_factory: SearchSourceFactory,
    ) -> None:
        self._tool_name = tool_name
        self._provider = provider
        self._search_pipeline = search_pipeline
        self._source_factory = source_factory
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name=tool_name,
                description=TOOL_DESCRIPTION,
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                timeout_seconds=WEB_SEARCH_TIMEOUT_SECONDS,
            ),
            config_spec=API_KEY_CONFIG_SPEC if provider is not None else None,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, object]:
        query_payload = kwargs["query"]
        search_query = query_payload["search_query"].strip()
        ranking_query = query_payload["ranking_query"].strip()
        mode = SearchMode(str(kwargs.get("mode") or SearchMode.WEB.value))
        requested_results = kwargs.get("max_results") or DEFAULT_SEARCH_RESULTS
        max_results = max(1, min(requested_results, MAX_SEARCH_RESULTS))
        api_key = (
            str((config or {}).get("api_key") or "").strip()
            if self._provider is not None
            else None
        )
        if self._provider is not None and not api_key:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_api_key_missing",
                detail_reason=f"{self._provider} 缺少工具配置中的 API key",
                retryable=False,
            )

        try:
            source = self._source_factory.build(
                provider=self._provider,
                api_key=api_key,
            )
            result = await self._search_pipeline.search(
                search_query=search_query,
                ranking_query=ranking_query,
                max_results=max_results,
                source=source,
                mode=mode,
            )

        except SearchProviderCredentialError as exc:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_api_key_invalid",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

        except SearchProviderNetworkError as exc:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_network_error",
                detail_reason=str(exc),
                retryable=True,
            ) from exc

        except SearchProviderError as exc:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

        except Exception as exc:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_unavailable",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

        if not result.candidates:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_empty_result",
                detail_reason="搜索没有返回结果",
                retryable=True,
            )

        return {
            "query": result.search_query,
            "mode": mode.value,
            "candidates": result.candidates,
            "supplier_answer": result.response.answer if result.response else None,
        }
