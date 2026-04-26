import time
import asyncio
from typing import List, Dict, Literal, Optional, Any, Tuple

from tavily import TavilyClient
from common.logger import log_error

from chat.domain.interfaces.tool import BaseTool
from chat.core.config.app_settings import settings
from chat.api.schemas.web_search import WebSearchResponse, SearchResult, ImageResult


class WebSearchTool(BaseTool):
    """联网搜索工具"""

    def __init__(self, ttl: int = 3600):
        self._ttl: int = ttl
        self._cache: Dict[str, Tuple[WebSearchResponse, float]] = {}
        self._client: TavilyClient = TavilyClient(api_key=settings.TAVILY_API_KEY)

    @property
    def name(self) -> str:
        return "web_search"
    
    @property
    def description(self) -> str:
        return (
            "Searches the web using the Tavily API. "
            "Use this tool when you need to find current information, look up facts, or retrieve sources "
            "that are beyond your knowledge cutoff. "
            "Returns a concise set of relevant results, each containing a title, URL, and a text snippet. "
            "An AI-generated answer snippet can optionally be included."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query string.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (1-10). Default 5.",
                    "default": 5,
                },
                "search_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "description": (
                        "Search depth. "
                        "'basic' offers balanced relevance and latency for general-purpose searches. "
                        "'advanced' provides the highest relevance with increased latency, "
                        "ideal for detailed, high-precision queries."
                    ),
                    "default": "basic",
                },
                "include_answer": {
                    "type": "boolean",
                    "description": "Include an AI-generated answer snippet. Default false.",
                    "default": False,
                },
                "include_images": {
                    "type": "boolean",
                    "description": "Include relevant images in the results. Use when the user asks for pictures, photos, or visual information.",
                    "default": False,
                },
                "include_image_descriptions": {
                    "type": "boolean",
                    "description": "Include descriptions of images. Only used when include_images is true.",
                    "default": False,
                },
            },
            "required": ["query"],
        }

    @property
    def is_ephemeral_output(self) -> bool:
        """Web search 结果应该保存到对话历史中，所以返回 False"""
        return False

    @property
    def reserved(self) -> bool:
        """Web search 是普通业务工具，用户可以自由使用"""
        return False

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        """执行联网搜索"""
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        query: str = kwargs["query"]
        max_results: int = min(kwargs.get("max_results", 5), 10)
        search_depth: Literal["basic", "advanced"] = kwargs.get("search_depth", "basic")
        include_answer: bool = kwargs.get("include_answer", False)
        include_images: bool = kwargs.get("include_images", False)
        include_image_descriptions: bool = kwargs.get("include_image_descriptions", False)

        cache_key = (
            f"{session_id}:search:"
            f"q={query}:"
            f"n={max_results}:"
            f"depth={search_depth}:"
            f"ans={include_answer}:"
            f"img={include_images}:"
            f"desc={include_image_descriptions}"
        )

        if cache_key in self._cache:
            response, expired_at = self._cache[cache_key]
            if time.time() < expired_at:
                return self._to_llm_context(response)
        
        try:
            response = await self._tavily_search(
                query=query,
                max_results=max_results,
                search_depth=search_depth,
                include_answer=include_answer,
                include_images=include_images,
                include_image_descriptions=include_image_descriptions,
            )
            self._cache[cache_key] = (response, time.time() + self._ttl)

            if not response.results:
                return "[Tool Result] No results found for the query."

            return self._to_llm_context(response)
            
        except Exception as e:
            log_error("联网搜索", e, session_id=session_id, query=query)
            return "[Tool Error] An error occurred while searching the web."
        

    async def _tavily_search(
                self,
                query: str,
                max_results: int,
                search_depth: Literal["basic", "advanced"],
                include_answer: bool,
                include_images: bool,
                include_image_descriptions: bool,
            ) -> WebSearchResponse:
        """执行 Tavily 搜索"""
    
        raw_response = await asyncio.to_thread(
            self._client.search,
            query=query,
            max_results=max_results,
            search_depth=search_depth,
            include_answer=include_answer,
            include_images=include_images,
            include_image_descriptions=include_image_descriptions,
        )
        
        return self._parse_response(raw_response)

    def _parse_response(self, raw_response: Dict[str, Any]) -> WebSearchResponse:
        """解析 Tavily 搜索响应"""
        results: List[SearchResult] = []
        for raw_result in raw_response.get("results", []):
            results.append(
                SearchResult(
                    title=raw_result.get("title", ""),
                    url=raw_result.get("url", ""),
                    snippet=raw_result.get("content", ""),
                    images=[ImageResult(
                        url=raw_image.get("url", ""), 
                        desc=raw_image.get("description")
                    ) for raw_image in raw_result.get("images", [])]
                )
            )
        
        return WebSearchResponse(
            query=raw_response.get("query", ""),
            results=results,
            answer=raw_response.get("answer"),
        )
        
    def _to_llm_context(self, response: WebSearchResponse) -> str:
        """转为 LLM 友好的紧凑格式"""
        lines = [f"Web search results for '{response.query}':"]
        
        if response.answer:
            lines.append(f"\nAnswer: {response.answer}")
        
        for i, result in enumerate(response.results, 1):
            title = result.title.strip() or result.url or "(no title)"
            snippet = result.snippet.strip()
            
            lines.append(f"\n{i}. {title}")
            lines.append(f"   URL: {result.url}")
            lines.append(f"   Snippet: {snippet}")
            
            if result.images:
                lines.append(f"   Images: {len(result.images)} available")
        
        raw = "\n".join(lines)
        
        if len(raw) > settings.TOOL_RESULT_MAX_CHARS:
            raw = raw[:settings.TOOL_RESULT_MAX_CHARS] + "\n...[truncated]"
        
        return raw
