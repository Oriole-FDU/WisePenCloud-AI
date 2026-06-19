from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256

from common.logger import info, warn

from .cache_ttl import compute_ttl
from .models import (
    WebContentCacheEntry,
    WebContentCacheMode,
    WebContentCacheValue,
)
from .refresh_queue import (
    WEB_FETCH_REFRESH_JOB,
    WebContentCacheRefreshJob,
    WebContentCacheRefreshTaskPublisher,
)
from .repository import WebContentCacheRepository

WEB_PUBLIC_SOURCE_SCOPE = "web_public"
WEB_CUSTOM_SOURCE_SCOPE = "web_custom"
DEFAULT_REFRESH_LOCK_TTL_SECONDS = 300


@dataclass(frozen=True, slots=True)
class CachedMarkdownPage:
    """URL 缓存命中的 HTML/Markdown 页面。"""

    source_url: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    title: str | None
    markdown: str
    raw_html: str | None
    cache_mode: WebContentCacheMode
    stale: bool


@dataclass(frozen=True, slots=True)
class HtmlCacheWrite:
    """写入 URL HTML 缓存所需的最小数据。"""

    url: str
    user_id: str
    source_scope: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    raw_html: str | None
    markdown: str
    title: str | None
    headers: dict[str, str]
    fetcher: str | None
    cleaner: str | None
    producer: str
    extra_metadata: dict[str, object] | None = None


class WebContentCacheService:
    """URL 内容缓存门面，封装 HTML markdown 读写与 stale refresh 调度。"""

    __slots__ = ("_repository", "_refresh_task_publisher")

    def __init__(
        self,
        *,
        repository: WebContentCacheRepository | None,
        refresh_task_publisher: WebContentCacheRefreshTaskPublisher | None = None,
    ) -> None:
        self._repository = repository
        self._refresh_task_publisher = refresh_task_publisher

    async def read_markdown_page(
        self,
        *,
        url: str,
        user_id: str,
        session_id: str,
        source_scope: str,
        refresh_job_prefix: str,
        refresh_task_name: str = WEB_FETCH_REFRESH_JOB,
        refresh_lock_ttl_seconds: int = DEFAULT_REFRESH_LOCK_TTL_SECONDS,
    ) -> CachedMarkdownPage | None:
        """读取 URL markdown 缓存，stale 命中时返回旧内容并安排后台刷新。"""
        repository = self._repository
        if repository is None:
            return None

        try:
            now = datetime.now(timezone.utc)
            for mode in (WebContentCacheMode.PRIVATE, WebContentCacheMode.PUBLIC):
                entry = await repository.get_entry(
                    user_id=user_id,
                    url=url,
                    cache_mode=mode,
                )
                if entry is None:
                    continue

                value = await repository.get_value(doc_id=entry.mongo_doc_id)
                if value is None or not value.markdown:
                    continue

                hard_expire_at = _ensure_aware(entry.hard_expire_at)
                if now > hard_expire_at:
                    continue

                title = value.metadata.get("title")
                stale = now > _ensure_aware(entry.soft_expire_at)
                if stale:
                    refresh_source_scope = (
                        WEB_CUSTOM_SOURCE_SCOPE
                        if entry.cache_mode == WebContentCacheMode.PRIVATE
                        else WEB_PUBLIC_SOURCE_SCOPE
                    )
                    await self.schedule_stale_refresh(
                        url=url,
                        user_id=user_id,
                        session_id=session_id,
                        source_scope=refresh_source_scope,
                        cache_mode=entry.cache_mode,
                        refresh_job_prefix=refresh_job_prefix,
                        refresh_task_name=refresh_task_name,
                        refresh_lock_ttl_seconds=refresh_lock_ttl_seconds,
                    )

                info(
                    "URL markdown 缓存命中",
                    url=url,
                    cache_mode=entry.cache_mode.value,
                    doc_id=entry.mongo_doc_id,
                    stale=stale,
                    producer=refresh_job_prefix,
                )
                return CachedMarkdownPage(
                    source_url=url,
                    final_url=value.final_url,
                    status_code=value.status_code,
                    content_type=value.content_type,
                    title=title if isinstance(title, str) else None,
                    markdown=value.markdown,
                    raw_html=value.raw_html,
                    cache_mode=entry.cache_mode,
                    stale=stale,
                )
        except Exception as exc:
            warn("URL markdown 缓存读取失败，降级为实时获取", url=url, e=exc)

        return None

    async def write_html_markdown(self, write: HtmlCacheWrite) -> str | None:
        """写入 HTML 清洗结果缓存；失败返回 None，不影响调用方结果。"""
        repository = self._repository
        if repository is None or not write.markdown:
            return None

        try:
            now = datetime.now(timezone.utc)
            mode = _cache_mode_for_source_scope(write.source_scope)
            ttl = compute_ttl(
                headers=write.headers,
                now=now,
                is_shared_cache=(mode == WebContentCacheMode.PUBLIC),
                status_code=write.status_code or 200,
            )
            if ttl.no_store:
                info("URL HTML 缓存被 no-store 指令跳过", url=write.url)
                return None

            canonical_url = write.url.strip()
            content_hash_payload = f"{write.raw_html or ''}\n---markdown---\n{write.markdown}"
            metadata = {
                "title": write.title,
                "source_scope": write.source_scope,
                "source_url": write.url,
                "fetcher": write.fetcher,
                "cleaner": write.cleaner,
                "producer": write.producer,
                "cache_control": write.headers.get("cache-control"),
                **(write.extra_metadata or {}),
            }
            value = WebContentCacheValue(
                id=None,
                user_id=write.user_id,
                canonical_url=canonical_url,
                final_url=write.final_url,
                cache_mode=mode,
                status_code=write.status_code,
                content_type=write.content_type,
                raw_html=write.raw_html,
                markdown=write.markdown,
                content_hash=sha256(content_hash_payload.encode("utf-8")).hexdigest(),
                fetched_at=now,
                metadata=metadata,
            )
            doc_id = await repository.save_value(value)
            await repository.set_entry(
                WebContentCacheEntry(
                    user_id=write.user_id,
                    url_hash=sha256(canonical_url.encode("utf-8")).hexdigest(),
                    canonical_url=canonical_url,
                    mongo_doc_id=doc_id,
                    cache_mode=mode,
                    soft_expire_at=ttl.soft_expire_at,
                    hard_expire_at=ttl.hard_expire_at,
                    etag=write.headers.get("etag"),
                    last_modified=write.headers.get("last-modified"),
                )
            )
            info(
                "URL HTML 缓存已写入",
                url=write.url,
                cache_mode=mode.value,
                doc_id=doc_id,
                producer=write.producer,
            )
            return doc_id
        except Exception as exc:
            warn("URL HTML 缓存写入失败", url=write.url, e=exc)
            return None

    async def schedule_stale_refresh(
        self,
        *,
        url: str,
        user_id: str,
        session_id: str,
        source_scope: str,
        cache_mode: WebContentCacheMode,
        refresh_job_prefix: str,
        refresh_task_name: str = WEB_FETCH_REFRESH_JOB,
        refresh_lock_ttl_seconds: int = DEFAULT_REFRESH_LOCK_TTL_SECONDS,
    ) -> None:
        repository = self._repository
        if repository is None:
            return

        try:
            lock_owner = "public" if cache_mode == WebContentCacheMode.PUBLIC else user_id
            lock_key = f"{refresh_job_prefix}:{cache_mode.value}:{lock_owner}:{url}"
            if not await repository.try_acquire_refresh_lock(
                key=lock_key,
                ttl_seconds=refresh_lock_ttl_seconds,
            ):
                return
        except Exception as exc:
            warn("URL 缓存 stale 刷新锁获取失败", url=url, e=exc)
            return

        if self._refresh_task_publisher is None:
            return

        url_hash = sha256(url.encode("utf-8")).hexdigest()
        job_id = (
            f"{refresh_job_prefix}:{cache_mode.value}:"
            f"{'public' if cache_mode == WebContentCacheMode.PUBLIC else user_id}:"
            f"{url_hash}"
        )
        try:
            await self._refresh_task_publisher.enqueue(
                WebContentCacheRefreshJob(
                    name=refresh_task_name,
                    job_id=job_id,
                    payload={
                        "url": url,
                        "user_id": user_id,
                        "session_id": session_id,
                        "source_scope": source_scope,
                        "cache_mode": cache_mode.value,
                    },
                )
            )
        except Exception as exc:
            warn("URL 缓存 stale 刷新任务入队失败", url=url, e=exc)


def _cache_mode_for_source_scope(source_scope: str) -> WebContentCacheMode:
    if source_scope == WEB_CUSTOM_SOURCE_SCOPE:
        return WebContentCacheMode.PRIVATE
    return WebContentCacheMode.PUBLIC


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
