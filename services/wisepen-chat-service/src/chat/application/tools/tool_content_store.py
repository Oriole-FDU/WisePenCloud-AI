from typing import Any, Dict, Optional, Tuple

from chat.application.infra.content_store import ContentStore
from chat.application.infra.content_store.formatting import (
    format_tool_content_window,
)
from chat.application.infra.content_store.models import (
    ContentReceipt,
    ContentWindow,
    StoredContent,
)
from chat.core.config.app_settings import settings

_TOOL_CONTENT_STORE_TTL_SECONDS = 30 * 60
_TOOL_CONTENT_STORE_MAX_ITEM_CHARS = 20_000_000
CONTENT_ROLE_WRAPPER = "wrapper"
CONTENT_ROLE_PARSED = "parsed"
CONTENT_ROLE_SEARCH_PACK = "search_pack"
CONTENT_ROLE_WINDOW = "window"


class ToolContentStore:
    def __init__(
        self,
        *,
        content_store: ContentStore,
    ):
        """初始化 ToolContentStore。

        Args:
            content_store: 由容器注入的基础内容存储。
        """
        self._store = content_store


    def put(
        self,
        *,
        session_id: str,
        tool_name: str,
        source: str,
        text: str,
        content_type: str = "text/markdown",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """缓存工具输出的文本内容。

        将工具输出的文本内容存储到缓存中，并返回对应的内容 ID。

        Args:
            session_id: 会话 ID，用于隔离不同会话的内容。
            tool_name: 产生内容的工具名称。
            source: 内容来源标识。
            text: 要缓存的内容文本。
            content_type: 内容的 MIME 类型，默认为 text/markdown。
            metadata: 可选的键值对元数据。

        Returns:
            缓存成功时返回内容 ID，失败时返回 None。
        """
        return self._store.put_content(
            scope_id=session_id,
            producer=tool_name,
            source=source,
            text=text,
            content_type=content_type,
            metadata=metadata,
        )

    def get(self, *, content_id: str, session_id: str) -> Optional[StoredContent]:
        """从缓存中获取已存储的工具内容。

        根据内容 ID 和会话 ID 从缓存中检索已存储的内容。

        Args:
            content_id: 内容 ID。
            session_id: 会话 ID，用于验证内容所属的会话。

        Returns:
            找到内容时返回 StoredContent 对象，未找到或已过期时返回 None。
        """
        return self._store.get_content(
            content_id=content_id,
            scope_id=session_id,
        )

    def resolve_canonical_content_id(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> str:
        """把 wrapper/window 等可跳转内容 ID 解析为 canonical 内容 ID。"""
        stored = self.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return content_id

        canonical_content_id = stored.metadata.get("canonical_content_id")
        if isinstance(canonical_content_id, str) and canonical_content_id:
            return canonical_content_id

        parsed_content_id = stored.metadata.get("parsed_content_id")
        if isinstance(parsed_content_id, str) and parsed_content_id:
            return parsed_content_id

        return content_id

    def canonicalize_content_id(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> Tuple[str, Optional[str]]:
        canonical_content_id = self.resolve_canonical_content_id(
            content_id=content_id,
            session_id=session_id,
        )
        if canonical_content_id == content_id:
            return canonical_content_id, None
        return canonical_content_id, (
            f"content_id {content_id} is a wrapper or redirect receipt. "
            f"This tool already used canonical_content_id {canonical_content_id} automatically; "
            "no manual ID switch is required for this call."
        )

    def update_metadata(
        self,
        *,
        content_id: str,
        session_id: str,
        metadata: Dict[str, Any],
    ) -> Optional[StoredContent]:
        stored = self.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return None

        stored.metadata.update(metadata)
        self._store.put_stored_content(stored)
        return stored

    def put_receipt(
        self,
        *,
        session_id: str,
        tool_name: str,
        source: str,
        text: str,
        content_type: str = "application/json",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[ContentReceipt]:
        """缓存工具输出并返回结构化回执。

        将工具的输出内容缓存，并返回包含缓存详情（如内容 ID、长度、分块数等）的回执对象。

        Args:
            session_id: 会话 ID。
            tool_name: 产生内容的工具名称。
            source: 内容来源标识。
            text: 要缓存的内容文本。
            content_type: 内容的 MIME 类型，默认为 application/json。
            metadata: 可选的键值对元数据。

        Returns:
            缓存成功时返回 ContentReceipt 回执对象，失败时返回 None。
        """
        return self._store.put_content_receipt(
            scope_id=session_id,
            producer=tool_name,
            source=source,
            text=text,
            content_type=content_type,
            metadata=metadata,
        )

    def read_chunk_window_by_offset(
        self,
        *,
        content_id: str,
        session_id: str,
        offset: int,
        limit: int,
    ) -> Optional[ContentWindow]:
        """从缓存中读取指定偏移量和长度的内容窗口。

        Args:
            content_id: 内容 ID。
            session_id: 会话 ID。
            offset: 读取起始位置的字符偏移量。
            limit: 返回内容的最大字符数。

        Returns:
            成功时返回 ContentWindow 窗口对象，内容不存在或已过期时返回 None。
        """
        return self._store.read_chunk_window_by_offset(
            content_id=content_id,
            scope_id=session_id,
            offset=offset,
            limit=limit,
        )

    def put_and_read_window(
        self,
        *,
        session_id: str,
        tool_name: str,
        source: str,
        text: str,
        content_type: str = "text/markdown",
        metadata: Optional[Dict[str, Any]] = None,
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> ContentWindow:
        """缓存工具输出并立即读取指定的内容窗口。

        先缓存工具输出的文本内容，然后根据偏移量和限制长度读取对应的内容窗口。
        如果内容因过大等原因未能缓存，会返回一个包含截断内容的未缓存窗口。

        Args:
            session_id: 会话 ID。
            tool_name: 产生内容的工具名称。
            source: 内容来源标识。
            text: 要缓存的内容文本。
            content_type: 内容的 MIME 类型，默认为 text/markdown。
            metadata: 可选的键值对元数据。
            offset: 读取起始位置的字符偏移量，默认为 0。
            limit: 返回内容的最大字符数，不指定则使用默认分块大小。

        Returns:
            包含请求内容片段的 ContentWindow 对象。
        """
        return self._store.put_and_read_window(
            scope_id=session_id,
            producer=tool_name,
            source=source,
            text=text,
            content_type=content_type,
            metadata=metadata,
            offset=offset,
            limit=limit,
        )

    def read_chunk_window_by_index(
        self,
        *,
        content_id: str,
        session_id: str,
        chunk_index: int,
        before_chunks: int = 0,
        after_chunks: int = 0,
    ) -> Optional[ContentWindow]:
        """以指定分块为中心读取内容窗口。

        根据分块索引定位内容，并可选地包含前后相邻的分块，返回合并后的内容窗口。

        Args:
            content_id: 内容 ID。
            session_id: 会话 ID。
            chunk_index: 目标分块的索引。
            before_chunks: 目标分块之前额外包含的分块数，默认为 0。
            after_chunks: 目标分块之后额外包含的分块数，默认为 0。

        Returns:
            成功时返回合并后的 ContentWindow 对象，内容不存在时返回 None。
        """
        return self._store.read_chunk_window_by_index(
            content_id=content_id,
            scope_id=session_id,
            chunk_index=chunk_index,
            before_chunks=before_chunks,
            after_chunks=after_chunks,
        )


def read_tool_content_window_by_offset(
    *,
    session_id: str,
    content_id: str,
    offset: int = 0,
    limit: Optional[int] = None,
    content_store: ToolContentStore,
) -> str:
    """读取已缓存的工具内容并返回格式化后的窗口字符串。

    根据内容 ID 从缓存中读取指定偏移量和长度的内容片段，并格式化为可读的字符串。
    如果内容不存在或已过期，返回错误提示信息。

    Args:
        session_id: 会话 ID。
        content_id: 内容 ID。
        offset: 读取起始位置的字符偏移量，默认为 0。
        limit: 返回内容的最大字符数，默认为系统配置的工具结果最大字符数。

    Returns:
        格式化后的内容窗口字符串。内容不可用时返回错误提示信息。
    """
    if limit is None:
        limit = settings.TOOL_RESULT_MAX_CHARS * 2

    window = content_store.read_chunk_window_by_offset(
        content_id=content_id,
        session_id=session_id,
        offset=offset,
        limit=limit,
    )

    if window is None:
        return "[Tool Result] Cached tool content not found, expired, or inaccessible."

    return format_tool_content_window(window)


def read_tool_content_window_by_index(
    *,
    session_id: str,
    content_id: str,
    chunk_index: int,
    before_chunks: int = 1,
    after_chunks: int = 1,
    content_store: ToolContentStore,
) -> str:
    """以指定分块为中心读取已缓存的工具内容并返回格式化后的窗口字符串。

    根据分块索引定位内容，并包含前后相邻的分块，将合并后的内容格式化为可读的字符串。

    Args:
        session_id: 会话 ID。
        content_id: 内容 ID。
        chunk_index: 目标分块的索引。
        before_chunks: 目标分块之前额外包含的分块数，默认为 1。
        after_chunks: 目标分块之后额外包含的分块数，默认为 1。

    Returns:
        格式化后的内容窗口字符串。内容不可用时返回错误提示信息。
    """
    window = content_store.read_chunk_window_by_index(
        content_id=content_id,
        session_id=session_id,
        chunk_index=chunk_index,
        before_chunks=before_chunks,
        after_chunks=after_chunks,
    )

    if window is None:
        return "[Tool Result] Cached tool content not found, expired, or inaccessible."

    return format_tool_content_window(window)
