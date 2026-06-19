from __future__ import annotations

from typing import Protocol

from .models import WebSearchCandidateMapping


class WebSearchCandidateRepository(Protocol):
    """候选 URL 映射仓储协议。

    Redis 实现只保存短期路由状态，不承担 web_fetch 公共缓存职责。
    """

    async def set_mapping(
        self,
        mapping: WebSearchCandidateMapping,
        *,
        ttl_seconds: int,
    ) -> None:
        ...

    async def get_mapping(
        self,
        *,
        user_id: str,
        search_ref: str,
    ) -> WebSearchCandidateMapping | None:
        ...

    async def delete_mapping(
        self,
        *,
        user_id: str,
        search_ref: str,
    ) -> None:
        ...
