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
                "HTML pages are cleaned to Markdown. Direct PDF URLs use fast native text-layer "
                "extraction only. Use document_link_extract for exact PDF parsing or DOCX, XLSX, "
                "and PPTX links. Do not pass a search query, site name, or relative URL."
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
                    "content. Use this when exact HTML page URLs are known, including several "
                    "unrelated pages. Direct PDF URLs are supported as a convenience but use only "
                    "fast native text-layer extraction; this tool does not provide exact PDF "
                    "parsing and does not accept other binary documents. Use document_link_extract "
                    "for exact PDF, DOCX, XLSX, or PPTX extraction, and web_crawl to discover linked "
                    "HTML pages. Invalid or unsupported URLs are omitted. Each returned item "
                    "identifies its source URL, and cached content keeps source_url metadata for "
                    "follow-up content reads."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.MEDIUM,
                timeout_seconds=300.0,
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
        del context, config
        results = await self._fetch_coordinator.fetch(
            [str(url).strip() for url in kwargs["urls"]],
        )
        visible_result = {
            "items": tuple(
                {"source_url": result.source_url}
                for result in results
            )
        }
        return ToolReturn(
            visible_result=visible_result,
            cacheable_texts=tuple(
                CacheableText(
                    text=result.text,
                    is_md=result.is_md,
                    metadata={"source_url": result.source_url},
                )
                for result in results
            ),
        )
