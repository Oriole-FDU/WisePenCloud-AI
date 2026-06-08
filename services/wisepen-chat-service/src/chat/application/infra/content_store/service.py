import hashlib
import uuid
from enum import StrEnum
from typing import Any, Dict, Optional

from chat.application.infra.content_store.chunking import (
    create_content_chunks,
    find_chunk_by_offset,
)
from chat.application.infra.content_store.models import (
    ContentReceipt,
    ContentWindow,
    StoredContent,
)
from common.logger import log_event


class ContentWindowError(StrEnum):
    OFFSET_OUT_OF_RANGE = "offset_out_of_range"
    CHUNK_NOT_FOUND = "chunk_not_found"
    CHUNK_INDEX_OUT_OF_RANGE = "chunk_index_out_of_range"
    EMPTY_CONTENT = "empty_content"


class ContentCacheError(StrEnum):
    CONTENT_TOO_LARGE = "content_too_large"
    EMPTY_CONTENT = "empty_content"


class ContentStore:
    """
    通用基础设施层的内容存储门面控制器。

    - _repository: 由容器注入的内容仓储，可落地到 Redis 等后端
    - _default_chunk_size: 系统默认单分块最大字符跨度
    - _max_item_chars: 单条资产允许存入的字符长度上限
    - _normalize_text: 是否在入库前自动裁剪头尾空白符
    """

    def __init__(
            self,
            *,
            repository: Any,
            default_chunk_size: int,
            max_item_chars: int,
            normalize_text: bool = True,
    ) -> None:
        """初始化 ContentStore。

        Args:
            repository: 由容器注入的内容仓储实例。
            default_chunk_size: 系统默认单分块最大字符跨度。
            max_item_chars: 单条内容允许存入的最大字符数。
            normalize_text: 是否在入库前自动裁剪头尾空白符，默认为 True。
        """
        self._repository = repository
        self._default_chunk_size = max(1, default_chunk_size)
        self._max_item_chars = max(1, max_item_chars)
        self._normalize_text = normalize_text

    def _store_content(
            self,
            *,
            scope_id: str,
            producer: str,
            source: str,
            text: str,
            content_type: str,
            metadata: Optional[Dict[str, Any]],
            chunk_size: Optional[int],
    ) -> Optional[StoredContent]:
        """物理写入仓库，返回 StoredContent 对象。

        对文本进行归一化处理后，将其分块并存入缓存仓库。外部公开方法通过此方法避免二次查询。

        Args:
            scope_id: 作用域 ID，用于隔离不同会话的内容。
            producer: 产生内容的来源标识。
            source: 内容来源标签。
            text: 要存储的内容文本。
            content_type: 内容的 MIME 类型。
            metadata: 可选的键值对元数据。
            chunk_size: 分块的最大字符数，不指定则使用默认值。

        Returns:
            成功时返回 StoredContent 对象，文本为空或超出最大字符数限制时返回 None。
        """
        if self._normalize_text:
            text = text.strip()

        # 空文本或超长文本不进入缓存；调用方会按未缓存路径降级。
        if not text or len(text) > self._max_item_chars:
            return None

        safe_metadata: Dict[str, Any] = dict(metadata) if metadata else {}
        safe_metadata["content_hash"] = hashlib.sha256(text.encode()).hexdigest()

        chunks = create_content_chunks(
            text,
            max(1, chunk_size if chunk_size is not None else self._default_chunk_size),
            content_type=content_type,
        )
        stored = StoredContent(
            content_id=f"cnt_{uuid.uuid4().hex[:16]}",
            scope_id=scope_id,
            producer=producer,
            source=source,
            content_type=content_type,
            text=text,
            chunks=chunks,
            metadata=safe_metadata,
        )
        self._repository.put(stored)
        return stored

    def put_content(
            self,
            *,
            scope_id: str,
            producer: str,
            source: str,
            text: str,
            content_type: str = "text/markdown",
            metadata: Optional[Dict[str, Any]] = None,
            chunk_size: Optional[int] = None,
    ) -> Optional[str]:
        """缓存内容文本并返回内容 ID。

        将文本内容归一化、分块后存入缓存仓库，并返回可用于后续读取的内容 ID。

        Args:
            scope_id: 作用域 ID。
            producer: 产生内容的来源标识。
            source: 内容来源标签。
            text: 要缓存的内容文本。
            content_type: 内容的 MIME 类型，默认为 text/markdown。
            metadata: 可选的键值对元数据。
            chunk_size: 分块的最大字符数，不指定则使用默认值。

        Returns:
            缓存成功时返回内容 ID，失败时返回 None。
        """
        stored = self._store_content(
            scope_id=scope_id, producer=producer, source=source, text=text,
            content_type=content_type, metadata=metadata, chunk_size=chunk_size,
        )
        return stored.content_id if stored else None

    def put_stored_content(self, stored: StoredContent) -> None:
        """重写已存在的 StoredContent，用于补充协议 metadata。"""
        self._repository.put(stored)

    def get_content(self, *, content_id: str, scope_id: str) -> Optional[StoredContent]:
        """从缓存中获取已存储的内容。

        根据内容 ID 和所属作用域从缓存仓库中检索已存储的内容。

        Args:
            content_id: 内容 ID。
            scope_id: 作用域 ID，用于验证内容所属的作用域。

        Returns:
            找到内容时返回 StoredContent 对象，未找到或作用域不匹配时返回 None。
        """
        item = self._repository.get(content_id)

        # 内容不存在、TTL 过期或 scope 不匹配时视为不可读取，避免跨作用域读取缓存内容。
        if item is None or item.scope_id != scope_id:
            return None
        return item

    def put_content_receipt(
            self,
            *,
            scope_id: str,
            producer: str,
            source: str,
            text: str,
            content_type: str = "application/json",
            metadata: Optional[Dict[str, Any]] = None,
            chunk_size: Optional[int] = None,
    ) -> Optional[ContentReceipt]:
        """缓存内容文本并返回结构化回执。

        将文本内容归一化、分块后存入缓存仓库，并返回包含内容 ID、原始长度、分块数等详情的回执对象。

        Args:
            scope_id: 作用域 ID。
            producer: 产生内容的来源标识。
            source: 内容来源标签。
            text: 要缓存的内容文本。
            content_type: 内容的 MIME 类型，默认为 application/json。
            metadata: 可选的键值对元数据。
            chunk_size: 分块的最大字符数，不指定则使用默认值。

        Returns:
            缓存成功时返回 ContentReceipt 回执对象，失败时返回 None。
        """
        stored = self._store_content(
            scope_id=scope_id, producer=producer, source=source, text=text,
            content_type=content_type, metadata=metadata, chunk_size=chunk_size,
        )
        if stored is None:
            return None
        return ContentReceipt(
            content_id=stored.content_id,
            producer=stored.producer,
            source=stored.source,
            content_type=stored.content_type,
            original_length=len(stored.text),
            chunk_count=len(stored.chunks),
            cached=True,
            metadata=dict(stored.metadata),
        )

    def read_chunk_window_by_offset(
            self,
            *,
            content_id: str,
            scope_id: str,
            offset: int = 0,
            limit: Optional[int] = None,
    ) -> Optional[ContentWindow]:
        """从缓存中读取指定偏移量和长度的内容窗口。

        根据内容 ID 和作用域从缓存中检索内容，并按指定的字符偏移量和长度返回内容片段。
        如果偏移量超出范围，返回带有 error 标记的空窗口。

        Args:
            content_id: 内容 ID。
            scope_id: 作用域 ID。
            offset: 读取起始位置的字符偏移量，默认为 0。
            limit: 返回内容的最大字符数，不指定则使用默认分块大小。

        Returns:
            成功时返回 ContentWindow 窗口对象，内容不存在或已过期时返回 None。
        """
        item = self.get_content(content_id=content_id, scope_id=scope_id)
        if item is None:
            return None

        original_length = len(item.text)
        offset = max(0, offset)
        effective_limit = max(1, limit if limit is not None else self._default_chunk_size)

        # offset 超出正文范围属于读取参数错误；内容仍存在，因此返回 error window 而不是 None。
        if offset >= original_length:
            return ContentWindow(
                content_id=content_id, producer=item.producer, source=item.source,
                content_type=item.content_type, original_length=original_length,
                chunk_count=len(item.chunks), offset=offset,
                returned_length=0, truncated=False, next_offset=None,
                text="", error=ContentWindowError.OFFSET_OUT_OF_RANGE.value,
            )

        chunks = (
            item.chunks if effective_limit == self._default_chunk_size
            else create_content_chunks(item.text, effective_limit, content_type=item.content_type)
        )
        chunk_count = len(chunks)
        chunk = find_chunk_by_offset(chunks, offset)

        # offset 合法但无法定位 chunk，说明缓存分块与正文不一致，返回 error window 保护读取链路。
        if chunk is None:
            return ContentWindow(
                content_id=content_id, producer=item.producer, source=item.source,
                content_type=item.content_type, original_length=original_length,
                chunk_count=chunk_count, offset=offset,
                returned_length=0, truncated=False, next_offset=None,
                text="", error=ContentWindowError.CHUNK_NOT_FOUND.value,
            )

        truncated = chunk.index < chunk_count - 1
        next_offset = chunk.end_offset if truncated else None
        window_text = item.text[chunk.start_offset:chunk.end_offset]
        returned_length = len(window_text)

        log_event(
            "分段读取进行中",
            content_id=content_id,
            chunk_id=chunk.index,
            chunk_count=chunk_count,
            offset=chunk.start_offset,
            returned_length=returned_length,
            truncated=truncated,
            next_offset=next_offset,
        )

        return ContentWindow(
            content_id=content_id,
            producer=item.producer,
            source=item.source,
            content_type=item.content_type,
            original_length=original_length,
            chunk_index=chunk.index,
            chunk_count=chunk_count,
            offset=chunk.start_offset,
            returned_length=returned_length,
            truncated=truncated,
            next_offset=next_offset,
            text=window_text,
        )

    def put_and_read_window(
            self,
            *,
            scope_id: str,
            producer: str,
            source: str,
            text: str,
            content_type: str = "text/markdown",
            metadata: Optional[Dict[str, Any]] = None,
            offset: int = 0,
            limit: Optional[int] = None,
    ) -> ContentWindow:
        """缓存内容并立即读取指定的内容窗口。

        先缓存文本内容，然后根据偏移量和限制长度读取对应的内容窗口。
        如果内容因过大等原因未能缓存，会返回一个包含截断内容的未缓存窗口，并附上缓存失败的警告。

        常用场景：工具产出长文本后需要缓存全文，同时立即返回首个可读窗口；首个窗口承担内容预览与后续读取导航。

        Args:
            scope_id: 作用域 ID。
            producer: 产生内容的来源标识。
            source: 内容来源标签。
            text: 要缓存的内容文本。
            content_type: 内容的 MIME 类型，默认为 text/markdown。
            metadata: 可选的键值对元数据。
            offset: 读取起始位置的字符偏移量，默认为 0。
            limit: 返回内容的最大字符数，不指定则使用默认分块大小。

        Returns:
            包含请求内容片段的 ContentWindow 对象。内容未被缓存时返回未缓存窗口并附带警告。
        """
        content_id = self.put_content(
            scope_id=scope_id,
            producer=producer,
            source=source,
            text=text,
            content_type=content_type,
            metadata=metadata,
        )
        if content_id:
            window = self.read_chunk_window_by_offset(
                content_id=content_id, scope_id=scope_id, offset=offset, limit=limit,
            )
            if window is not None:
                return window

        # 缓存失败不应阻断当前响应；直接基于原文返回一个未缓存窗口。
        return _create_uncached_window(
            text=text,
            producer=producer,
            source=source,
            content_type=content_type,
            offset=offset,
            limit=limit if limit is not None else self._default_chunk_size,
            cache_error=ContentCacheError.CONTENT_TOO_LARGE.value,
            normalize_text=self._normalize_text,
        )

    def read_chunk_window_by_index(
            self,
            *,
            content_id: str,
            scope_id: str,
            chunk_index: int,
            before_chunks: int = 0,
            after_chunks: int = 0,
    ) -> Optional[ContentWindow]:
        """以指定分块为中心读取内容窗口。

        根据分块索引定位缓存内容，并可选地包含前后相邻的分块，将合并后的内容返回。
        如果分块索引超出范围，返回带有 error 标记的错误窗口。

        Args:
            content_id: 内容 ID。
            scope_id: 作用域 ID。
            chunk_index: 目标分块的索引。
            before_chunks: 目标分块之前额外包含的分块数，默认为 0。
            after_chunks: 目标分块之后额外包含的分块数，默认为 0。

        Returns:
            成功时返回合并后的 ContentWindow 对象，内容不存在时返回 None。
        """
        item = self.get_content(content_id=content_id, scope_id=scope_id)
        if item is None:
            return None

        chunks = item.chunks
        chunk_count = len(chunks)
        text_length = len(item.text)

        # chunk_index 越界属于读取参数错误；内容仍存在，因此返回 error window。
        if chunk_index < 0 or chunk_index >= chunk_count:
            return ContentWindow(
                content_id=content_id,
                producer=item.producer,
                source=item.source,
                content_type=item.content_type,
                original_length=text_length,
                chunk_index=chunk_index,
                chunk_count=chunk_count,
                error=ContentWindowError.CHUNK_INDEX_OUT_OF_RANGE.value,
            )

        start_chunk = max(0, chunk_index - max(0, before_chunks))
        end_chunk = min(chunk_count - 1, chunk_index + max(0, after_chunks))

        start_offset = chunks[start_chunk].start_offset
        end_offset = chunks[end_chunk].end_offset
        window_text = item.text[start_offset:end_offset]
        truncated = end_chunk < chunk_count - 1

        return ContentWindow(
            content_id=content_id,
            producer=item.producer,
            source=item.source,
            content_type=item.content_type,
            original_length=text_length,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
            offset=start_offset,
            returned_length=len(window_text),
            truncated=truncated,
            next_offset=end_offset if truncated else None,
            text=window_text,
            metadata={"start_chunk_index": start_chunk, "end_chunk_index": end_chunk},
        )


def _create_uncached_window(
        *,
        text: str,
        producer: str,
        source: str,
        content_type: str = "text/markdown",
        offset: int = 0,
        limit: int = 4000,
        cache_error: str = ContentCacheError.CONTENT_TOO_LARGE.value,
        normalize_text: bool = True,
) -> ContentWindow:
    """创建未缓存的内容窗口。

    当内容因过大或其他原因未能存入缓存时，直接对原始文本进行分片并返回内容窗口。
    结果中会标记 cached=False 并附带缓存失败的说明和警告。

    Args:
        text: 原始内容文本。
        producer: 产生内容的来源标识。
        source: 内容来源标签。
        content_type: 内容的 MIME 类型，默认为 text/markdown。
        offset: 读取起始位置的字符偏移量，默认为 0。
        limit: 分块的最大字符数，默认为 4000。
        cache_error: 缓存失败的原因描述，默认为 content_too_large。
        normalize_text: 是否在分片前裁剪头尾空白符，默认为 True。

    Returns:
        包含截断内容的 ContentWindow 对象，cached 字段为 False，并附带缓存失败的警告信息。
    """
    if normalize_text:
        text = text.strip()

    original_length = len(text)

    # 原文本身为空时没有可降级读取的内容，返回 empty_content window。
    if not text:
        return ContentWindow(
            content_id="",
            producer=producer,
            source=source,
            content_type=content_type,
            original_length=0,
            truncated=False, text="",
            error=ContentWindowError.EMPTY_CONTENT.value,
            cached=False,
            cache_error=ContentCacheError.EMPTY_CONTENT.value,
        )

    chunks = create_content_chunks(text, limit, content_type=content_type)
    chunk_count = len(chunks)
    chunk = find_chunk_by_offset(chunks, offset)

    # 未缓存路径下 offset 仍然越界时，返回 error window 而不是抛异常。
    if chunk is None:
        return ContentWindow(
            content_id="",
            producer=producer,
            source=source,
            content_type=content_type,
            original_length=original_length,
            chunk_count=chunk_count,
            offset=offset,
            truncated=False, text="",
            error=ContentWindowError.OFFSET_OUT_OF_RANGE.value,
            cached=False,
            cache_error=cache_error,
        )

    window_text = text[chunk.start_offset:chunk.end_offset]
    truncated = chunk.index < chunk_count - 1

    return ContentWindow(
        content_id="",
        producer=producer,
        source=source,
        content_type=content_type,
        original_length=original_length,
        chunk_index=chunk.index,
        chunk_count=chunk_count,
        offset=chunk.start_offset,
        returned_length=len(window_text),
        truncated=truncated,
        next_offset=None,
        text=window_text,
        cached=False,
        cache_error=cache_error,
        warning="Full content was too large to cache. Answer conservatively because the complete document is not available from cache.",
    )
