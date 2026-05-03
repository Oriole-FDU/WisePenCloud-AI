from typing import Any, Dict, Optional
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from chat.application.web_fetch.fetch_coordinator import FetchCoordinator


class WebFetchTool(BaseTool):
    """网页浏览抓取工具"""

    def __init__(self):
        self._fetcher = FetchCoordinator(settings.STEEL_BASE_URL)

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetches and extracts textual content from a given URL, returning clean Markdown. "
            "Supports both web pages and direct document links (PDF, DOCX, XLSX, PPTX) — "
            "content type is auto-detected, no need to specify format.\n\n"
            "**Fetch strategy:** Three-stage automatic fallback: "
            "1) lightweight static HTTP request; 2) headless browser (Steel) for JS-heavy pages; "
            "3) local headless browser as final fallback. "
            "Document URLs are handled directly via static fetch — no browser stage needed.\n\n"
            "**When to use:** Call this tool when a user provides a URL (web page or document) "
            "and asks you to read, summarize, analyze, or answer questions about its content.\n\n"
            "**force_browser:** Set to true ONLY when: "
            "a) default mode returned incomplete content or a bot-check page; "
            "b) the target is a known dynamic, JavaScript-heavy website. "
            "Do NOT use force_browser for document URLs (PDF, DOCX, etc.) — it provides no benefit.\n\n"
            "**Note:** Returned content may be very long and is automatically truncated. "
            "Focus on extracting the information relevant to the user's request."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch. Accepts web pages (HTML) and direct document links (PDF, DOCX, XLSX, PPTX). Must start with http:// or https://.",
                },
                "force_browser": {
                    "type": "boolean",
                    "description": (
                        "Force browser mode. Defaults to false. "
                        "false: tries a fast static fetch first, then falls back to a headless browser. Best for most situations. "
                        "true: skips the static fetch and uses a headless browser immediately. Useful for dynamic, JavaScript-heavy pages "
                        "or when a static fetch has already failed."
                    ),
                    "default": False,
                },
            },
            "required": ["url"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        """执行网页浏览并返回 Markdown 内容或错误消息"""
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        url: str = kwargs.get("url", "")
        force_browser: bool = kwargs.get("force_browser", False)

        if not url:
            return "[Tool Error] Missing required url parameter"

        md_result = await self._fetcher.fetch(url, force_browser=force_browser)

        if md_result is None:
            return "[Tool Result] Failed to fetch web page content (all fetch methods exhausted)"

        if len(md_result) > settings.TOOL_RESULT_MAX_CHARS:
            md_result = md_result[:settings.TOOL_RESULT_MAX_CHARS] + "\n\n...(Content truncated due to length)"

        return md_result
