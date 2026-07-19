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
from chat.application.tools.core.output.tool_return import ToolReturn

from ..services.errors import (
    WebSearchCustomApiKeyInvalid,
    WebSearchCustomApiKeyMissing,
    WebSearchEmptyResult,
    WebSearchError,
    WebSearchInternalError,
    WebSearchNetworkError,
)
from ..services.pipeline import (
    SearchPipeline,
    SearchPipelineResult,
    VisibleWebSearchCandidate,
)
from ..services.providers.core.models import SearchMode, SearchProviderName
from ..services.sources import SearchSourceFactory

DEFAULT_SEARCH_RESULTS = 10
MAX_SEARCH_RESULTS = 20
WEB_SEARCH_TIMEOUT_SECONDS = 300.0

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Required. A concise, search-engine-friendly query.",
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
    "Search external web information and return ranked candidates. "
    "Use academic mode for literature search; sources without native academic "
    "support fall back to web search. Call once with one clear query and fetch "
    "selected URLs when evidence is required."
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
                cache_chunked=False,
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
    ) -> ToolReturn:
        query = kwargs["query"].strip()
        mode = SearchMode(str(kwargs.get("mode") or SearchMode.WEB.value))
        requested_results = kwargs.get("max_results") or DEFAULT_SEARCH_RESULTS
        max_results = max(1, min(requested_results, MAX_SEARCH_RESULTS))

        try:
            api_key = (
                str((config or {}).get("api_key") or "").strip()
                if self._provider is not None
                else None
            )
            source = self._source_factory.build(
                provider=self._provider,
                api_key=api_key,
            )
            result = await self._search_pipeline.search(
                query=query,
                max_results=max_results,
                source=source,
                mode=mode,
            )
        except WebSearchCustomApiKeyMissing as exc:
            raise self._execution_error(
                "api_key_missing",
                exc,
                retryable=False,
            ) from exc
        except WebSearchCustomApiKeyInvalid as exc:
            raise self._execution_error(
                "api_key_invalid",
                exc,
                retryable=False,
            ) from exc
        except WebSearchNetworkError as exc:
            raise self._execution_error(
                "network_error",
                exc,
                retryable=True,
            ) from exc
        except WebSearchEmptyResult as exc:
            raise self._execution_error(
                "empty_result",
                exc,
                retryable=True,
            ) from exc
        except WebSearchInternalError as exc:
            raise self._execution_error(
                "unavailable",
                exc,
                retryable=False,
            ) from exc
        except WebSearchError as exc:
            raise self._execution_error(
                "failed",
                exc,
                retryable=False,
            ) from exc

        return _build_tool_return(result=result, mode=mode)

    def _execution_error(
        self,
        reason: str,
        exc: Exception,
        *,
        retryable: bool,
    ) -> ToolExecutionError:
        return ToolExecutionError(
            reason=f"{self._tool_name}_{reason}",
            detail_reason=str(exc),
            retryable=retryable,
        )


def _build_tool_return(
    *,
    result: SearchPipelineResult,
    mode: SearchMode,
) -> ToolReturn:
    answers = tuple(
        dict.fromkeys(
            response.answer
            for response in result.search_result.responses
            if response.answer
        )
    )
    visible_result: dict[str, object] = {
        "query": result.search_result.query,
        "mode": mode.value,
        "candidates": tuple(
            VisibleWebSearchCandidate(
                url=candidate.url,
                title=candidate.title,
                overview=candidate.overview,
                highlights=candidate.highlights,
            )
            for candidate in result.candidates
        ),
        "recommended_ids": result.recommended_ids,
    }
    if answers:
        visible_result["supplier_answers"] = answers

    return ToolReturn(visible_result=visible_result)
