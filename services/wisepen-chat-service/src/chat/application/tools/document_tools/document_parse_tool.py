from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from common.logger import warn

from chat.application.tools.common.batching import batched
from chat.application.tools.common.tool_run_file_store import ToolRunFileStore
from chat.application.tools.common.tool_run_file_store.errors import (
    InvalidToolFileRefError,
    ToolFileNotFoundError,
    ToolFileUnreadableError,
)
from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.tool_return import (
    SuggestedAction,
    SuggestedActionPriority,
    ToolReturn,
)
from chat.application.tools.document_tools.document_parse.models import DocumentParseRequest
from chat.application.tools.document_tools.document_parse.service import DocumentParseService
from chat.application.tools.web_tools.web_content_cache.models import (
    WebContentCacheEntry,
    WebContentCacheMode,
    WebContentCacheValue,
)
from chat.application.tools.web_tools.web_content_cache.repository import (
    WebContentCacheRepository,
)
from chat.application.tools.web_tools.web_fetch.fetchers.base import BaseFetcher, RawFetchOutput
from chat.application.tools.web_tools.web_fetch.errors import WebFetchError
from chat.application.tools.web_tools.web_fetch.utils import filename_from_url
from chat.application.tools.web_tools.web_content_cache.refresh_queue import (
    DOCUMENT_PARSE_REFRESH_JOB,
    WebContentCacheRefreshJob,
    WebContentCacheRefreshTaskPublisher,
)
from chat.application.tools.web_tools.web_content_cache.cache_ttl import compute_ttl
from chat.application.tools.tool_settings import tool_settings

MAX_DOCUMENT_PARSE_FILE_REFS = 64
SERVICE_BATCH_SIZE = tool_settings.DOCUMENT_PARSE_MAX_FILE_REFS
DOCUMENT_PARSE_CONCURRENCY = tool_settings.DOCUMENT_PARSE_CONCURRENCY
_DOCUMENT_PARSE_CACHE_PARSER_VERSION = "document_parse:v1"
_REFRESH_LOCK_TTL_SECONDS = tool_settings.DOCUMENT_PARSE_REFRESH_LOCK_TTL_SECONDS


@dataclass(frozen=True, slots=True)
class _ParsedCacheHit:
    markdown: str
    cache_mode: WebContentCacheMode
    stale: bool


@dataclass(frozen=True, slots=True)
class DocumentParseToolItem:
    file_ref: str  # 调用方传入的 tfile_* 引用
    status: str  # success 或 failed
    file_name: str | None = None  # 解析出的展示文件名
    content_ref: int | None = None  # 对应 ToolReturn.cacheable_texts 的索引
    source_scope: str | None = None  # 通过 ToolRunFileStore metadata 识别出的来源范围
    reason: str | None = None  # 单项失败原因，供模型判断下一步


class DocumentParseTool:
    """批量解析 tfile_* 文档引用的工具入口。"""

    __slots__ = (
        "_content_cache_repository",
        "_definition",
        "_direct_fetcher",
        "_file_store",
        "_parse_service",
        "_refresh_task_publisher",
    )

    def __init__(
        self,
        *,
        file_store: ToolRunFileStore,
        parse_service: DocumentParseService,
        content_cache_repository: WebContentCacheRepository | None = None,
        refresh_task_publisher: WebContentCacheRefreshTaskPublisher | None = None,
        direct_fetcher: BaseFetcher | None = None,
    ) -> None:
        self._file_store = file_store
        self._parse_service = parse_service
        self._content_cache_repository = content_cache_repository
        self._refresh_task_publisher = refresh_task_publisher
        self._direct_fetcher = direct_fetcher
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="document_parse",
                description=(
                    "Parse temporary document files or direct file URLs into Markdown.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when previous tools returned tfile_* references (e.g. from web_fetch, web_crawl, or uploads) and you need their textual content.\n"
                    "  - MUST trigger directly when the user provides obvious file URLs (PDF, image, Office, spreadsheet, or similar non-HTML files) and asks for their content.\n"
                    "  - SHOULD trigger when the user asks to read, summarize, or answer questions about an attached document.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need to read normal HTML pages — use web_fetch or web_crawl instead.\n"
                    "  - You already have content_ids from a previous parse — use tool_content_read or tool_content_sequential_read instead.\n"
                    "  - You only have a non-file web page URL; mode='from_direct_urls' is only for direct file URLs.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - mode='from_web_fetch' => provide file_refs with tfile_* values returned by web_fetch or another previous tool.\n"
                    "  - mode='from_direct_urls' => provide direct_urls with full http(s) file URLs.\n"
                    "  - file_refs and direct_urls are mutually exclusive; never provide both.\n"
                    "  - Pass all selected files in one array; the tool auto-batches large sets and parses files concurrently within each batch.\n"
                    "  - Do not wrap obvious direct file URLs through web_fetch first; use mode='from_direct_urls' directly.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - Returns one item per input file with status success or failed.\n"
                    "  - Each successfully parsed file produces a cacheable content unit; failed files return a reason code.\n"
                    "  - Use the suggested tool_content_read action to locate answer-relevant windows in the parsed Markdown."
                ),
                parameters_schema=ToolParametersSchema(
                    {
                        "type": "object",
                        "properties": {
                            "mode": {
                                "type": "string",
                                "enum": ["from_web_fetch", "from_direct_urls"],
                                "description": (
                                    "Required. Use from_web_fetch for tfile_* file_refs; "
                                    "use from_direct_urls for obvious direct file URLs."
                                ),
                            },
                            "file_refs": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                "minItems": 1,
                                "maxItems": MAX_DOCUMENT_PARSE_FILE_REFS,
                                "description": (
                                    "Required when mode='from_web_fetch'. tfile_* references produced by previous tools. "
                                    "Large sets are automatically split into internal batches."
                                ),
                            },
                            "direct_urls": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                "minItems": 1,
                                "maxItems": MAX_DOCUMENT_PARSE_FILE_REFS,
                                "description": (
                                    "Required when mode='from_direct_urls'. Full http(s) direct file URLs. "
                                    "Large sets are automatically split into internal batches. "
                                    "Use this for obvious non-HTML file links instead of calling web_fetch first."
                                ),
                            },
                        },
                        "required": ["mode"],
                        "additionalProperties": False,
                    }
                ),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("user_id", "session_id"),
                timeout_seconds=tool_settings.DOCUMENT_PARSE_TOOL_TIMEOUT_SECONDS,
                cache_chunked=True,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        """返回工具元定义。"""
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolReturn:
        """批量解析文件引用，单项失败不影响其它文件。"""
        user_id = str(context["user_id"])
        session_id = str(context["session_id"])
        mode = str(kwargs["mode"])
        file_refs = tuple(str(value) for value in kwargs.get("file_refs", ()))
        direct_urls = tuple(str(value) for value in kwargs.get("direct_urls", ()))

        match mode:
            case "from_web_fetch":
                if not file_refs:
                    raise ToolExecutionError(
                        reason="missing_file_refs",
                        detail_reason="file_refs is required when mode='from_web_fetch'.",
                        retryable=False,
                    )
                if direct_urls:
                    raise ToolExecutionError(
                        reason="mixed_document_parse_inputs",
                        detail_reason="direct_urls must not be provided when mode='from_web_fetch'.",
                        retryable=False,
                    )
                item_results = await self._parse_file_ref_batches(
                    user_id=user_id,
                    session_id=session_id,
                    file_refs=file_refs,
                )
            case "from_direct_urls":
                if not direct_urls:
                    raise ToolExecutionError(
                        reason="missing_direct_urls",
                        detail_reason="direct_urls is required when mode='from_direct_urls'.",
                        retryable=False,
                    )
                if file_refs:
                    raise ToolExecutionError(
                        reason="mixed_document_parse_inputs",
                        detail_reason="file_refs must not be provided when mode='from_direct_urls'.",
                        retryable=False,
                    )
                item_results = await self._parse_direct_url_batches(
                    user_id=user_id,
                    session_id=session_id,
                    direct_urls=direct_urls,
                )
            case _:
                raise ToolExecutionError(
                    reason="invalid_mode",
                    detail_reason="mode must be 'from_web_fetch' or 'from_direct_urls'.",
                    retryable=False,
                )

        cacheable_texts: list[str] = []
        items: list[DocumentParseToolItem] = []
        for item, markdown in item_results:
            if markdown:
                item = DocumentParseToolItem(
                    file_ref=item.file_ref,
                    status=item.status,
                    file_name=item.file_name,
                    content_ref=len(cacheable_texts),
                    source_scope=item.source_scope,
                )
                cacheable_texts.append(markdown)
            items.append(item)

        return ToolReturn(
            tag="document_parse_result",
            visible_result={
                "items": tuple(items),
                "suggested_action": SuggestedAction(
                    tool_name="tool_content_read",
                    mode="ranked_expand",
                    reason="Search the parsed Markdown content for answer-relevant windows.",
                    priority=SuggestedActionPriority.HIGH,
                ),
            },
            cacheable_texts=tuple(cacheable_texts),
        )

    async def _parse_file_ref_batches(
        self,
        *,
        user_id: str,
        session_id: str,
        file_refs: tuple[str, ...],
    ) -> list[tuple[DocumentParseToolItem, str | None]]:
        semaphore = asyncio.Semaphore(DOCUMENT_PARSE_CONCURRENCY)
        results: list[tuple[DocumentParseToolItem, str | None]] = []
        for batch_file_refs in batched(file_refs, batch_size=max(1, int(SERVICE_BATCH_SIZE))):
            parse_inputs = [
                self._parse_one(
                    semaphore=semaphore,
                    user_id=user_id,
                    session_id=session_id,
                    file_ref=file_ref,
                )
                for file_ref in batch_file_refs
            ]
            results.extend(await asyncio.gather(*parse_inputs, return_exceptions=False))
        return results

    async def _parse_direct_url_batches(
        self,
        *,
        user_id: str,
        session_id: str,
        direct_urls: tuple[str, ...],
    ) -> list[tuple[DocumentParseToolItem, str | None]]:
        semaphore = asyncio.Semaphore(DOCUMENT_PARSE_CONCURRENCY)
        results: list[tuple[DocumentParseToolItem, str | None]] = []
        for batch_direct_urls in batched(direct_urls, batch_size=max(1, int(SERVICE_BATCH_SIZE))):
            parse_inputs = [
                self._parse_direct_url(
                    semaphore=semaphore,
                    user_id=user_id,
                    session_id=session_id,
                    direct_url=direct_url,
                )
                for direct_url in batch_direct_urls
            ]
            results.extend(await asyncio.gather(*parse_inputs, return_exceptions=False))
        return results

    async def _parse_one(
        self,
        *,
        semaphore: asyncio.Semaphore,
        user_id: str,
        session_id: str,
        file_ref: str,
    ) -> tuple[DocumentParseToolItem, str | None]:
        """解析单个文件引用；异常转换为单项失败。"""
        async with semaphore:
            try:
                resolved = await self._file_store.resolve_ref(
                    user_id=user_id,
                    session_id=session_id,
                    ref_id=file_ref,
                )
                source_scope = _source_scope_from_metadata(resolved.metadata)
                source_kind = _string_metadata(resolved.metadata, "source_kind")
                cache_hit = await self._read_parsed_web_cache(
                    user_id=user_id,
                    metadata=resolved.metadata,
                )
                if cache_hit is not None:
                    if cache_hit.stale:
                        await self._schedule_stale_parse_refresh(
                            user_id=user_id,
                            session_id=session_id,
                            file_ref=file_ref,
                            metadata=resolved.metadata,
                            cache_mode=cache_hit.cache_mode,
                        )
                    return (
                        DocumentParseToolItem(
                            file_ref=file_ref,
                            status="success",
                            file_name=resolved.filename,
                            source_scope=source_scope,
                        ),
                        cache_hit.markdown,
                    )

                result = await self._parse_service.parse(
                    DocumentParseRequest(
                        file_path=resolved.path,
                        original_filename=resolved.filename,
                        mime_type=resolved.content_type,
                        source_scope=source_scope,
                        source_kind=source_kind,
                    )
                )
                markdown = result.markdown.strip()
                if markdown:
                    await self._write_parsed_web_cache(
                        user_id=user_id,
                        metadata=resolved.metadata,
                        content_type=resolved.content_type,
                        markdown=markdown,
                    )
                return (
                    DocumentParseToolItem(
                        file_ref=file_ref,
                        status="success",
                        file_name=resolved.filename,
                        source_scope=source_scope,
                    ),
                    markdown or None,
                )
            except Exception as e:
                return (
                    DocumentParseToolItem(
                        file_ref=file_ref,
                        status="failed",
                        source_scope=None,
                        reason=_failure_reason(e),
                    ),
                    None,
                )

    async def _parse_direct_url(
        self,
        *,
        semaphore: asyncio.Semaphore,
        user_id: str,
        session_id: str,
        direct_url: str,
    ) -> tuple[DocumentParseToolItem, str | None]:
        """下载明显文件直链并复用 tfile 解析链路。"""
        if self._direct_fetcher is None:
            return (
                DocumentParseToolItem(
                    file_ref=direct_url,
                    status="failed",
                    reason="direct_url_fetch_unavailable",
                ),
                None,
            )

        url = direct_url.strip()
        if not url.startswith(("http://", "https://")):
            return (
                DocumentParseToolItem(
                    file_ref=direct_url,
                    status="failed",
                    reason="invalid_direct_url",
                ),
                None,
            )

        raw: RawFetchOutput | None = None
        try:
            metadata = _direct_url_metadata(url=url, final_url=url, content_type=None)
            cache_hit = await self._read_parsed_web_cache(
                user_id=user_id,
                metadata=metadata,
            )
            if cache_hit is not None:
                return (
                    DocumentParseToolItem(
                        file_ref=direct_url,
                        status="success",
                        file_name=filename_from_url(url),
                        source_scope="web_public",
                    ),
                    cache_hit.markdown,
                )

            raw = await self._direct_fetcher.fetch(url)
            if raw.file_path is None:
                return (
                    DocumentParseToolItem(
                        file_ref=direct_url,
                        status="failed",
                        reason="direct_url_not_file",
                    ),
                    None,
                )

            cache_doc_id = await self._write_direct_url_cache_stub(
                user_id=user_id,
                raw=raw,
            )
            metadata = _direct_url_metadata(
                url=raw.source_url,
                final_url=raw.final_url or raw.source_url,
                content_type=raw.content_type,
                cache_doc_id=cache_doc_id,
            )
            record = await self._file_store.publish_file(
                user_id=user_id,
                session_id=session_id,
                producer="document_parse",
                path=raw.file_path,
                filename=filename_from_url(raw.final_url or raw.source_url)
                or f"download.{raw.file_label or 'bin'}",
                content_type=raw.content_type,
                ref_prefix="web_public",
                metadata=metadata,
            )
            return await self._parse_one(
                semaphore=semaphore,
                user_id=user_id,
                session_id=session_id,
                file_ref=record.ref_id,
            )
        except WebFetchError as e:
            return (
                DocumentParseToolItem(
                    file_ref=direct_url,
                    status="failed",
                    reason=f"direct_url_fetch_failed:{e.reason}",
                ),
                None,
            )
        except Exception:
            return (
                DocumentParseToolItem(
                    file_ref=direct_url,
                    status="failed",
                    reason="direct_url_parse_failed",
                ),
                None,
            )
        finally:
            if raw is not None and raw.file_path is not None:
                with contextlib.suppress(OSError):
                    Path(raw.file_path).unlink(missing_ok=True)

    async def _write_direct_url_cache_stub(
        self,
        *,
        user_id: str,
        raw: RawFetchOutput,
    ) -> str | None:
        """为直链解析预创建 URL 缓存文档，后续解析结果回填 markdown。"""
        repository = self._content_cache_repository
        if repository is None:
            return None

        try:
            now = datetime.now(timezone.utc)
            ttl = compute_ttl(
                headers=raw.headers,
                now=now,
                is_shared_cache=True,
                status_code=raw.status_code or 200,
            )
            if ttl.no_store:
                return None

            canonical_url = raw.source_url.strip()
            value = WebContentCacheValue(
                id=None,
                user_id=user_id,
                canonical_url=canonical_url,
                final_url=raw.final_url,
                cache_mode=WebContentCacheMode.PUBLIC,
                status_code=raw.status_code,
                content_type=raw.content_type,
                raw_html=None,
                markdown=None,
                fetched_at=now,
                metadata={
                    "source_kind": "web_fetch",
                    "source_scope": "web_public",
                    "source_url": raw.source_url,
                    "final_url": raw.final_url,
                    "fetcher": raw.fetcher,
                    "file_label": raw.file_label,
                    "cache_control": raw.headers.get("cache-control"),
                },
            )
            doc_id = await repository.save_value(value)
            await repository.set_entry(
                WebContentCacheEntry(
                    user_id=user_id,
                    url_hash=sha256(canonical_url.encode("utf-8")).hexdigest(),
                    canonical_url=canonical_url,
                    mongo_doc_id=doc_id,
                    cache_mode=WebContentCacheMode.PUBLIC,
                    soft_expire_at=ttl.soft_expire_at,
                    hard_expire_at=ttl.hard_expire_at,
                    etag=raw.headers.get("etag"),
                    last_modified=raw.headers.get("last-modified"),
                )
            )
            return doc_id
        except Exception:
            warn(
                "文档解析直链缓存占位写入失败",
                source_url=raw.source_url,
                audit_message="文档解析直链缓存占位写入失败，不影响本次解析结果返回。",
            )
            return None

    async def _read_parsed_web_cache(
        self,
        *,
        user_id: str,
        metadata: dict[str, object],
    ) -> _ParsedCacheHit | None:
        repository = self._content_cache_repository
        if repository is None:
            return None

        source_kind = _string_metadata(metadata, "source_kind")
        source_scope = _source_scope_from_metadata(metadata)
        source_url = _string_metadata(metadata, "source_url")
        if source_kind != "web_fetch" or source_scope is None or source_url is None:
            return None

        try:
            # document_parse 的 file_ref 已带来源域，读取时不能在 public/private 间串域回退。
            cache_mode = (
                WebContentCacheMode.PRIVATE
                if source_scope == "web_custom"
                else WebContentCacheMode.PUBLIC
            )
            entry = await repository.get_entry(
                user_id=user_id,
                url=source_url,
                cache_mode=cache_mode,
            )
            if entry is None:
                return None

            now = datetime.now(timezone.utc)
            hard_expire_at = entry.hard_expire_at
            if hard_expire_at.tzinfo is None:
                hard_expire_at = hard_expire_at.replace(tzinfo=timezone.utc)
            if now > hard_expire_at:
                return None

            value = await repository.get_value(doc_id=entry.mongo_doc_id)
            if (
                value is None
                or not value.markdown
                or value.metadata.get("parser_version") != _DOCUMENT_PARSE_CACHE_PARSER_VERSION
            ):
                return None

            soft_expire_at = entry.soft_expire_at
            if soft_expire_at.tzinfo is None:
                soft_expire_at = soft_expire_at.replace(tzinfo=timezone.utc)

            return _ParsedCacheHit(
                markdown=value.markdown,
                cache_mode=entry.cache_mode,
                stale=now > soft_expire_at,
            )
        except Exception:
            warn(
                "文档解析缓存读取失败",
                source_url=source_url,
                source_scope=source_scope,
                audit_message="文档解析缓存读取失败，已降级为重新解析源文件。",
            )
            return None

        return None

    async def _schedule_stale_parse_refresh(
        self,
        *,
        user_id: str,
        session_id: str,
        file_ref: str,
        metadata: dict[str, object],
        cache_mode: WebContentCacheMode,
    ) -> None:
        repository = self._content_cache_repository
        source_url = _string_metadata(metadata, "source_url")
        if repository is None or source_url is None:
            return

        try:
            lock_owner = "public" if cache_mode == WebContentCacheMode.PUBLIC else user_id
            lock_key = (
                f"document_parse:{cache_mode.value}:{lock_owner}:"
                f"{source_url}:{_DOCUMENT_PARSE_CACHE_PARSER_VERSION}"
            )
            if not await repository.try_acquire_refresh_lock(
                key=lock_key,
                ttl_seconds=_REFRESH_LOCK_TTL_SECONDS,
            ):
                return
        except Exception:
            warn(
                "文档解析刷新锁获取失败",
                source_url=source_url,
                cache_mode=cache_mode,
                audit_message="文档解析旧缓存刷新锁获取失败，保留旧缓存返回并跳过刷新。",
            )
            return

        source_url_hash = sha256(source_url.encode("utf-8")).hexdigest()
        lock_owner = "public" if cache_mode == WebContentCacheMode.PUBLIC else user_id
        job_id = (
            f"document_parse:{cache_mode.value}:{lock_owner}:"
            f"{source_url_hash}:{_DOCUMENT_PARSE_CACHE_PARSER_VERSION}"
        )
        if self._refresh_task_publisher is not None:
            try:
                await self._refresh_task_publisher.enqueue(
                    WebContentCacheRefreshJob(
                        name=DOCUMENT_PARSE_REFRESH_JOB,
                        job_id=job_id,
                        payload={
                            "user_id": user_id,
                            "session_id": session_id,
                            "file_ref": file_ref,
                            "cache_mode": cache_mode.value,
                        },
                    )
                )
                return
            except Exception:
                warn(
                    "文档解析刷新任务入队失败，降级为本进程后台任务",
                    file_ref=file_ref,
                    source_url=source_url,
                    cache_mode=cache_mode,
                    audit_message="文档解析 stale 缓存刷新任务入队失败，已尝试使用本进程后台任务兜底。",
                )

        asyncio.create_task(
            self.refresh_stale_parse_cache(
                user_id=user_id,
                session_id=session_id,
                file_ref=file_ref,
            )
        )

    async def refresh_stale_parse_cache(
        self,
        *,
        user_id: str,
        session_id: str,
        file_ref: str,
    ) -> None:
        try:
            resolved = await self._file_store.resolve_ref(
                user_id=user_id,
                session_id=session_id,
                ref_id=file_ref,
            )
            result = await self._parse_service.parse(
                DocumentParseRequest(
                    file_path=resolved.path,
                    original_filename=resolved.filename,
                    mime_type=resolved.content_type,
                    source_scope=_source_scope_from_metadata(resolved.metadata),
                    source_kind=_string_metadata(resolved.metadata, "source_kind"),
                )
            )
            markdown = result.markdown.strip()
            if markdown:
                await self._write_parsed_web_cache(
                    user_id=user_id,
                    metadata=resolved.metadata,
                    content_type=resolved.content_type,
                    markdown=markdown,
                )
        except Exception:
            warn(
                "文档解析 stale 后台刷新失败",
                file_ref=file_ref,
                audit_message="文档解析后台刷新失败，已保留调用方收到的旧缓存结果。",
            )
            return

    async def _write_parsed_web_cache(
        self,
        *,
        user_id: str,
        metadata: dict[str, object],
        content_type: str | None,
        markdown: str,
    ) -> None:
        repository = self._content_cache_repository
        if repository is None:
            return

        source_kind = _string_metadata(metadata, "source_kind")
        source_scope = _source_scope_from_metadata(metadata)
        source_url = _string_metadata(metadata, "source_url")
        if source_kind != "web_fetch" or source_scope is None or source_url is None:
            return

        try:
            now = datetime.now(timezone.utc)
            # source_scope 是 public/private 写入隔离的唯一来源，不从 file_ref 文本反推。
            mode = (
                WebContentCacheMode.PRIVATE
                if source_scope == "web_custom"
                else WebContentCacheMode.PUBLIC
            )
            doc_id = _string_metadata(metadata, "source_cache_doc_id")
            existing = await repository.get_value(doc_id=doc_id) if doc_id else None
            # 从已有缓存条目的 metadata 中提取 cache-control，用于智能 TTL 计算
            cache_control_header = None
            if existing is not None and isinstance(existing.metadata, dict):
                cache_control_header = existing.metadata.get("cache_control")
            ttl = compute_ttl(
                headers={"cache-control": cache_control_header} if cache_control_header else {},
                now=now,
                is_shared_cache=(mode == WebContentCacheMode.PUBLIC),
                status_code=existing.status_code if existing is not None else 200,
            )
            if ttl.no_store:
                return
            raw_html = existing.raw_html if existing is not None else None
            content_hash_payload = f"{raw_html or ''}\n---markdown---\n{markdown}"
            final_url = _string_metadata(metadata, "final_url")
            value = WebContentCacheValue(
                id=doc_id if existing is not None else None,
                user_id=user_id,
                canonical_url=existing.canonical_url if existing is not None else source_url.strip(),
                final_url=existing.final_url if existing is not None else final_url,
                cache_mode=mode,
                status_code=existing.status_code if existing is not None else None,
                content_type=existing.content_type if existing is not None else content_type,
                raw_html=raw_html,
                markdown=markdown,
                content_hash=sha256(content_hash_payload.encode("utf-8")).hexdigest(),
                fetched_at=existing.fetched_at if existing is not None else now,
                metadata={
                    **(existing.metadata if existing is not None else {}),
                    "source_kind": source_kind,
                    "source_scope": source_scope,
                    "source_url": source_url,
                    "final_url": final_url,
                    "content_type": content_type,
                    "parser": "document_parse",
                    "parser_version": _DOCUMENT_PARSE_CACHE_PARSER_VERSION,
                },
            )
            saved_doc_id = await repository.save_value(value)
            await repository.set_entry(
                WebContentCacheEntry(
                    user_id=user_id,
                    url_hash=sha256(value.canonical_url.encode("utf-8")).hexdigest(),
                    canonical_url=value.canonical_url,
                    mongo_doc_id=saved_doc_id,
                    cache_mode=mode,
                    soft_expire_at=ttl.soft_expire_at,
                    hard_expire_at=ttl.hard_expire_at,
                )
            )
        except Exception:
            warn(
                "文档解析缓存写入失败",
                source_url=source_url,
                source_scope=source_scope,
                audit_message="文档解析结果写入网页内容缓存失败，不影响本次解析结果返回。",
            )
            return


def _failure_reason(error: Exception) -> str:
    if isinstance(error, InvalidToolFileRefError):
        return "invalid_file_ref"
    if isinstance(error, ToolFileNotFoundError):
        return "file_ref_unavailable"
    if isinstance(error, ToolFileUnreadableError):
        return "file_unreadable"
    return "parse_failed"


def _source_scope_from_metadata(metadata: dict[str, object]) -> str | None:
    value = metadata.get("source_scope")
    return str(value) if isinstance(value, str) and value else None


def _string_metadata(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return str(value) if isinstance(value, str) and value else None


def _direct_url_metadata(
    *,
    url: str,
    final_url: str | None,
    content_type: str | None,
    cache_doc_id: str | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "source_kind": "web_fetch",
        "source_scope": "web_public",
        "source_url": url,
        "final_url": final_url,
        "content_type": content_type,
    }
    if cache_doc_id:
        metadata["source_cache_doc_id"] = cache_doc_id
    return metadata
