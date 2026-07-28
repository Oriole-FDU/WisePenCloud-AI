from __future__ import annotations

from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.output.tool_return import CacheableText, ToolReturn
from .web_fetch import FetchCoordinator

MAX_URLS = 64

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "urls": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": MAX_URLS,
            "description": (
                "One or more complete, publicly reachable http:// or https:// URLs. "
                "Use a direct PDF URL when you need PDF content; ordinary HTML pages are "
                "cleaned to Markdown. Do not pass a search query, site name, or relative URL."
            ),
        },
    },
    "required": ["urls"],
    "additionalProperties": False,
}


class WebFetchTool:
    __slots__ = ("_definition", "_fetch_coordinator")

    def __init__(
        self,
        *,
        fetch_coordinator: FetchCoordinator,
    ) -> None:
        self._fetch_coordinator = fetch_coordinator
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="web_fetch",
                description=(
                    "Fetch one or more specific public HTTP(S) URLs and return their readable "
                    "content. Use this when the exact page or PDF URL is already known, including "
                    "several unrelated URLs. HTML and direct PDFs are returned as Markdown. Use "
                    "web_crawl when you need to "
                    "discover and read multiple linked HTML pages starting from one site URL. URLs "
                    "must be complete public http(s) URLs; invalid or unsupported URLs are omitted "
                    "from the returned items. Each returned item identifies its source URL and the "
                    "corresponding cached content index for follow-up content reads."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.MEDIUM,
                timeout_seconds=300.0,
                required_context_keys=("user_id",),
            ),
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
        del config
        results = await self._fetch_coordinator.fetch(
            [str(url).strip() for url in kwargs["urls"]],
            user_id=str(context["user_id"]),
        )
        visible_result = {
            "items": tuple(
                {
                    "source_url": result.source_url,
                    "content_index": index,
                }
                for index, result in enumerate(results)
            )
        }
        return ToolReturn(
            visible_result=visible_result,
            cacheable_texts=tuple(
                CacheableText(text=result.text, is_md=result.is_md)
                for result in results
            ),
        )
