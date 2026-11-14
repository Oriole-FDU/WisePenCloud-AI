from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from common.utils.text_chunking import (
    Anchor,
    MarkdownChunk,
    MarkdownChunker,
    Section,
)

from chat.core.config.app_settings import settings
from chat.domain.repositories import ToolContentRepository


@dataclass(slots=True)
class StoredToolContent:
    """一个会话内的原始工具正文及其可重建结构"""

    content_id: str
    session_id: str
    text: str
    sections: list[Section] = field(default_factory=list)
    anchors: list[Anchor] = field(default_factory=list)
    chunks: list[MarkdownChunk] = field(default_factory=list)

    @property
    def sections_by_id(self) -> dict[str, Section]:
        """建立按 ID 的索引"""
        return {section.section_id: section for section in self.sections}

class ToolContentStore:

    def __init__(self, *, tool_content_repository: ToolContentRepository) -> None:
        self._tool_content_repository = tool_content_repository
        self._max_chars = settings.tools.content.max_chars

    async def put_tool_content(
        self,
        *,
        session_id: str,
        text: str,
        is_chunk: bool = True,
    ) -> tuple[str, int] | None:
        if not text or text.isspace() or len(text) > self._max_chars:
            return None  # 空白或超限直接跳过

        if is_chunk:
            result = MarkdownChunker().chunk(text)
            chunks, sections, anchors = result.chunks, result.sections, result.anchors
        else:
            chunks, sections, anchors = [], [], []

        stored = StoredToolContent(
            content_id=f"cnt_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            text=text,
            chunks=chunks,
            sections=sections,
            anchors=anchors,
        )
        await self._tool_content_repository.put(stored)
        return stored.content_id, len(text)


    async def get_tool_content(self, *, content_id: str, session_id: str) -> StoredToolContent | None:
        stored = await self._tool_content_repository.get(content_id)
        if stored is None or stored.session_id != session_id:
            return None
        return stored
