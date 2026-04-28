from typing import Any, Dict, Optional, Literal

from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from chat.application.browse_url.smart_fetcher import SmartFetcher


class BrowseUrlTool(BaseTool):
    """网页浏览抓取工具"""

    def __init__(self):
        self._fetcher = SmartFetcher(settings.STEEL_BASE_URL)

    @property
    def name(self) -> str:
        return "browse_url"

    @property
    def description(self) -> str:
        return (
            "Fetches and extracts the textual content of a given URL, returning clean Markdown. "
            "It uses a three-stage automatic fallback strategy to reliably retrieve content from most websites: "
            "1) a fast, lightweight static HTTP request; 2) a headless browser (Steel) for JavaScript-heavy pages; "
            "3) a local headless browser as a final fallback.\n\n"
            "**When to use this tool:** You should call this tool when a user provides a specific URL and asks you to "
            "read, summarize, analyze, or answer questions based on its content.\n\n"
            "**Choosing a mode:**"
            "- 'auto' (default): Use this for most cases. It starts with the fast static fetch and automatically "
            "falls back if the page requires it.\n"
            "- 'browser': Use this when you have already tried 'auto' but suspect the content was incomplete, "
            "or when a user explicitly asks to view a dynamic, JavaScript-based website.\n\n"
            "**Important:** The content returned may be very long and is automatically truncated. "
            "Focus on extracting the relevant information needed to complete the user's request. "
            "If the page content is too long, consider summarizing it or reading it in sections if the tool supports offset parameters."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The complete URL of the web page to fetch. Must start with http:// or https://.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "browser"],
                    "description": (
                        "Fetch mode. Defaults to 'auto'. "
                        "'auto': tries a fast static fetch first, then falls back to a headless browser. Best for most situations. "
                        "'browser': skips the static fetch and uses a headless browser immediately. Useful for dynamic, JavaScript-heavy pages "
                        "or when a static fetch has already failed."
                    ),
                    "default": "auto",
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
        mode: Literal["auto", "browser"] = kwargs.get("mode", "auto")

        if not url:
            return "[Tool Error] Missing required url parameter"

        # 调用抓取调度器
        try:
            md_result = await self._fetcher.fetch(url, mode=mode)
        except Exception as e:
            return f"[Tool Error] Web page fetch failed: {e}"

        if md_result is None:
            return "[Tool Result] Failed to fetch web page content (all fetch methods exhausted)"

        # 限制返回长度，防止 token 溢出
        if len(md_result) > settings.TOOL_RESULT_MAX_CHARS:
            md_result = md_result[:settings.TOOL_RESULT_MAX_CHARS] + "\n\n...(Content truncated due to length)"

        return md_result
