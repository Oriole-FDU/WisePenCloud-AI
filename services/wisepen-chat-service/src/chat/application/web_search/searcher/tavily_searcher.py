from __future__ import annotations

from tavily import AsyncTavilyClient

from chat.application.web_search.models import (
    SearchResponse,
    TavilySearchRequest,
    map_tavily_response,
)

__all__ = [
    "TavilySearcher",
]


class TavilySearcher:
    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 15.0,
    ) -> None:
        api_key = api_key.strip()

        if not api_key:
            raise ValueError("api_key 不能为空")

        self._client = AsyncTavilyClient(api_key=api_key)
        self._timeout = timeout

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        request = TavilySearchRequest(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        payload = request.to_payload()
        payload["timeout"] = self._timeout

        safe_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"api_key"}
        }

        try:
            raw_response = await self._client.search(**payload)
        except Exception as e:
            raise RuntimeError(
                "Tavily search failed: "
                f"payload={safe_payload}, "
                f"error={type(e).__name__}: {e}"
            ) from e

        return map_tavily_response(raw_response)