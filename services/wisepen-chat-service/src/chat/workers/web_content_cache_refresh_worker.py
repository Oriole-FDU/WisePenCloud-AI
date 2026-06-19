from __future__ import annotations

from beanie import init_beanie
from pymongo import AsyncMongoClient

from arq.connections import RedisSettings

from chat.container import container
from chat.core.config.app_settings import settings
from chat.domain.entities import (
    ChatMessage,
    ChatSession,
    Model,
    ModelProviderMapping,
    Provider,
    WebContentCacheValueDocument,
    WebSearchCredential,
)


class WebContentCacheRefreshWorker:
    """网页内容缓存后台刷新 Worker。"""

    __slots__ = ("_mongo_client",)

    def __init__(self) -> None:
        self._mongo_client: AsyncMongoClient | None = None

    async def startup(self) -> None:
        self._mongo_client = AsyncMongoClient(settings.MONGODB_URL)
        await init_beanie(
            database=self._mongo_client[settings.MONGODB_DB_NAME],
            document_models=[
                ChatSession,
                ChatMessage,
                Provider,
                Model,
                ModelProviderMapping,
                WebSearchCredential,
                WebContentCacheValueDocument,
            ],
        )

    async def shutdown(self) -> None:
        if self._mongo_client is not None:
            await self._mongo_client.close()
            self._mongo_client = None
        await container.web_fetch_http_client().aclose()
        await container.web_content_cache_refresh_task_publisher().close()

    async def refresh_web_fetch_cache(self, payload: dict[str, object]) -> None:
        coordinator = container.web_fetch_coordinator()
        await coordinator.refresh_stale_url(
            url=str(payload["url"]),
            user_id=str(payload["user_id"]),
            session_id=str(payload.get("session_id") or ""),
            source_scope=str(payload.get("source_scope") or "web_public"),
        )

    async def refresh_document_parse_cache(self, payload: dict[str, object]) -> None:
        tool = container.document_parse_tool()
        await tool.refresh_stale_parse_cache(
            user_id=str(payload["user_id"]),
            session_id=str(payload["session_id"]),
            file_ref=str(payload["file_ref"]),
        )


async def startup(ctx: dict) -> None:
    worker = WebContentCacheRefreshWorker()
    await worker.startup()
    ctx["web_content_cache_refresh_worker"] = worker


async def shutdown(ctx: dict) -> None:
    worker = ctx.get("web_content_cache_refresh_worker")
    if isinstance(worker, WebContentCacheRefreshWorker):
        await worker.shutdown()


async def refresh_web_fetch_cache(ctx: dict, payload: dict[str, object]) -> None:
    worker = ctx["web_content_cache_refresh_worker"]
    if not isinstance(worker, WebContentCacheRefreshWorker):
        raise RuntimeError("网页内容缓存刷新 Worker 未初始化。")
    await worker.refresh_web_fetch_cache(payload)


async def refresh_document_parse_cache(ctx: dict, payload: dict[str, object]) -> None:
    worker = ctx["web_content_cache_refresh_worker"]
    if not isinstance(worker, WebContentCacheRefreshWorker):
        raise RuntimeError("网页内容缓存刷新 Worker 未初始化。")
    await worker.refresh_document_parse_cache(payload)


class WorkerSettings:
    functions = [
        refresh_web_fetch_cache,
        refresh_document_parse_cache,
    ]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    queue_name = "wisepen:web_content_cache:refresh"
    on_startup = startup
    on_shutdown = shutdown
