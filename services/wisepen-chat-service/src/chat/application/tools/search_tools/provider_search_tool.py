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
    WebSearchCustomApiKeyInvalid,
    WebSearchCustomApiKeyMissing,
    WebSearchEmptyResult,
    WebSearchError,
    WebSearchNetworkError,
)
from chat.application.tools.search_tools.web_search.core.sources import CustomSearchSource
from chat.application.tools.search_tools.web_search.factories.integration_searcher_factory import (
    IntegrationSearcherFactory,
)
from chat.application.tools.search_tools.web_search.pipeline.candidates_builder import build_candidates
from chat.application.tools.search_tools.web_search.providers.models import SearchMode, SearchProviderName
from chat.application.tools.search_tools.web_search.result_builder import build_search_tool_return
from chat.application.tools.search_tools.web_search.service import SearchService
from chat.application.tools.search_tools.web_search.tool_utils import select_recommended_ids
from chat.core.persistence.mongo.web_search_credential_repository import MongoWebSearchCredentialRepository
from common.core.exceptions import ServiceException

DEFAULT_SEARCH_RESULTS = 10
MAX_SEARCH_RESULTS = 20
MAX_RECOMMENDED_CANDIDATES = 5
FALLBACK_CANDIDATES_COUNT = 3
WEB_SEARCH_TOOL_TIMEOUT_SECONDS = 300.0


class ProviderSearchTool:
    """自定义供应商搜索工具。每个实例固定一个 provider。"""

    __slots__ = (
        "_credential_repository",
        "_definition",
        "_integration_searcher_factory",
        "_provider",
        "_service",
        "_tool_name",
    )

    def __init__(
            self,
            *,
            tool_name: str,
            provider: SearchProviderName,
            service: SearchService,
            integration_searcher_factory: IntegrationSearcherFactory,
            credential_repository: MongoWebSearchCredentialRepository,
    ) -> None:
        self._tool_name = tool_name
        self._provider = provider
        self._service = service
        self._integration_searcher_factory = integration_searcher_factory
        self._credential_repository = credential_repository
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name=tool_name,
                description=_tool_description(provider),
                parameters_schema=ToolParametersSchema(_parameters_schema(provider)),
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
        if mode == SearchMode.ACADEMIC and not self._provider.supports_academic_mode:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_academic_unavailable",
                detail_reason=f"{self._provider.value} does not support academic search.",
                retryable=False,
            )

        try:
            source_id = f"custom:{self._provider.value}"
            api_key = await self._credential_repository.get_custom_api_key(
                user_id=str(context["user_id"]),
                provider=self._provider,
            )
            source = CustomSearchSource(
                provider=self._provider,
                source_id=source_id,
                searcher=self._integration_searcher_factory.build(
                    provider=self._provider,
                    api_key=api_key,
                    source_id=source_id,
                ),
                api_key=api_key,
            )
            result = await self._service.search(
                query=query,
                max_results=max_results,
                source=source,
                mode=mode,
            )
            candidates = build_candidates(result.responses)
            if not candidates:
                raise WebSearchEmptyResult(provider=self._provider, reason="搜索没有返回结果")
        except ServiceException as exc:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_api_key_missing",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
        except WebSearchCustomApiKeyMissing as exc:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_api_key_missing",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
        except WebSearchCustomApiKeyInvalid as exc:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_api_key_invalid",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
        except WebSearchNetworkError as exc:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_network_error",
                detail_reason=str(exc),
                retryable=True,
            ) from exc
        except WebSearchEmptyResult as exc:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_empty_result",
                detail_reason=str(exc),
                retryable=True,
            ) from exc
        except WebSearchError as exc:
            raise ToolExecutionError(
                reason=f"{self._tool_name}_failed",
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
            tool_name=self._tool_name,
            mode=mode.value,
            recommended_ids=recommended_ids,
        )


def _parameters_schema(provider: SearchProviderName) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Required. A concise, search-engine-friendly query for this provider.",
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_SEARCH_RESULTS,
            "default": DEFAULT_SEARCH_RESULTS,
            "description": "Maximum candidate results per search request.",
        },
    }
    if provider.supports_academic_mode:
        properties["mode"] = {
            "type": "string",
            "enum": [SearchMode.WEB.value, SearchMode.ACADEMIC.value],
            "default": SearchMode.WEB.value,
            "description": "Use academic for paper/literature search; otherwise use web.",
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["query"],
        "additionalProperties": False,
    }


def _tool_description(provider: SearchProviderName) -> str:
    academic_rule = ""
    if provider.supports_academic_mode:
        academic_rule = "  - Set mode='academic' when the user explicitly asks for papers, citations, or literature.\n"
    return (
        f"Search with the user's configured {provider.value} API key and return ranked candidates.\n"
        "\n"
        "WHEN TO TRIGGER:\n"
        "  - Use this tool when the user needs external information and this provider is suitable or requested.\n"
        f"{academic_rule}"
        "EXECUTION RULES:\n"
        "  - Execute exactly one explicit query per tool call.\n"
        "  - If results need evidence, fetch selected candidate URLs with web_fetch.\n"
    )
