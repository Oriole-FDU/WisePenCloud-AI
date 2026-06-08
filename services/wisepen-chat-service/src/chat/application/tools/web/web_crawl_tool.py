from __future__ import annotations

from typing import Any, Dict

from chat.application.tools.web.services.web_crawl import WebCrawlService
from chat.application.tools.web.services.web_crawl.models import CrawlRequest
from chat.application.tools.web.services.web_crawl.runtime.formatting import format_crawl_result
from chat.domain.interfaces.tool import BaseTool

_TOOL_DESCRIPTION = (
    "Crawls a small, bounded set of linked web pages starting from seed URLs. "
    "Use this after the user asks for information that is likely spread across a site or a few strongly related linked pages.\n\n"
    "web_crawl is not a new fetcher. It reuses web_fetch internally, applies URL security checks, robots policy, "
    "politeness, Markdown link extraction, and relevance-based frontier ranking.\n\n"
    "Use seed_urls for http:// or https:// URLs only. Do not pass content_id or file_ref values. "
    "Set objective to the specific evidence goal, not a generic phrase. Keep max_depth small; max_depth is capped at 2 "
    "and max_pages is capped at 20.\n\n"
    "HTML pages return ToolContent windows. Direct document links return file_ref handoffs for document_parse. "
    "Skipped and failed URLs are reported explicitly; do not infer content from skipped URLs."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "seed_urls": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 5,
            "uniqueItems": True,
            "description": "Initial http:// or https:// URLs to crawl from.",
        },
        "objective": {
            "type": "string",
            "minLength": 8,
            "description": "Specific evidence objective used to rank discovered links.",
        },
        "max_depth": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2,
            "default": 1,
        },
        "max_pages": {
            "type": "integer",
            "minimum": 2,
            "maximum": 20,
            "default": 8,
        },
    },
    "required": ["seed_urls", "objective"],
    "additionalProperties": False,
}


class WebCrawlTool(BaseTool):

    def __init__(self, service: WebCrawlService):
        self._service = service

    @property
    def name(self) -> str:
        return "web_crawl"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:

        session_id = context.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return "[Tool Error] session_id is required for web_crawl."
        user_id = context.get("user_id")
        if not isinstance(user_id, str) or not user_id.strip():
            return "[Tool Error] user_id is required for web_crawl."

        seed_urls = kwargs["seed_urls"]
        objective = kwargs["objective"]
        max_depth = kwargs.get("max_depth", 1)
        max_pages = kwargs.get("max_pages", 8)

        result = await self._service.crawl(
            CrawlRequest(
                user_id=user_id.strip(),
                session_id=session_id.strip(),
                seed_urls=seed_urls,
                objective=objective,
                max_depth=max_depth,
                max_pages=max_pages,
            )
        )
        return format_crawl_result(result)
