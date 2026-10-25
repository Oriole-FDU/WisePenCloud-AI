"""从已授权 Document 结构提供目录、标题邻域与精确章节原文读取。"""

from dataclasses import dataclass
from enum import StrEnum

from common.utils.markdown import OutlineFormatter, Section
from rag.application.common.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.core.schema import Document


class SectionReadScope(StrEnum):
    """原文范围选择；资源权限由显式 snapshot 决定。"""

    OWN = "own"
    SUBTREE = "subtree"


@dataclass(frozen=True, slots=True)
class DocumentOutline:
    """可见版本的全局目录；目录行中的 ID 可直接用于章节读取。"""

    resource_id: str
    content_revision: str
    outline: str


@dataclass(frozen=True, slots=True)
class SectionOutline:
    """可见章节的标题邻域，保留 Common 的格式与定位信息。"""

    resource_id: str
    content_revision: str
    section_id: str
    section_path: list[str]
    outline: str


@dataclass(frozen=True, slots=True)
class SectionText:
    """完整章节范围内的原文，不重建标题或截取窗口。"""

    resource_id: str
    content_revision: str
    section_id: str
    section_path: list[str]
    text: str


@dataclass(frozen=True, slots=True)
class ChunkText:
    """按 Chunk ID 精确读取的原文；不重建标题、不拼接派生上下文。"""

    resource_id: str
    content_revision: str
    chunk_id: str
    section_id: str | None
    section_path: list[str]
    text: str


class DocumentReading:
    """只保存共享 Reader；本次定位、章节索引与 formatter 均为局部变量。"""

    def __init__(self, *, reader: ResourceSnapshotReader) -> None:
        self._reader = reader

    async def global_outline(
            self,
            resource_id: str,
            *,
            snapshot: ResourceSnapshot,
            max_level: int = 0,
    ) -> DocumentOutline | None:
        documents = await self._reader.read_documents([resource_id], snapshot=snapshot)
        if not documents:
            return None
        document = documents[0]
        return DocumentOutline(
            document.resource_id,
            document.content_revision,
            OutlineFormatter(
                sections=document.structure.sections,
                anchors=document.structure.anchors,
            ).global_outline(max_level=max_level),
        )

    async def neighborhood(
            self,
            section_ids: list[str],
            *,
            snapshot: ResourceSnapshot,
            sibling_steps: int = 1,
    ) -> list[SectionOutline]:
        documents = await self._reader.read_documents_by_section_ids(
            section_ids, snapshot=snapshot
        )
        # 一份文档只建一个 formatter；同次批量操作中复用，调用结束即释放。
        formatters = {
            document.content_revision: OutlineFormatter(
                sections=document.structure.sections,
                anchors=document.structure.anchors,
            )
            for document in documents
        }
        sections = _section_locations(documents)
        result = []
        for section_id in dict.fromkeys(section_ids):
            if (location := sections.get(section_id)) is None:
                continue
            document, section = location
            result.append(
                SectionOutline(
                    document.resource_id,
                    document.content_revision,
                    section.section_id,
                    section.section_path,
                    formatters[document.content_revision].neighborhood(
                        section_id, sibling_steps=sibling_steps
                    ),
                )
            )
        return result

    async def read_sections(
            self,
            section_ids: list[str],
            *,
            snapshot: ResourceSnapshot,
            scope: SectionReadScope = SectionReadScope.OWN,
    ) -> list[SectionText]:
        """按 own/subtree 半开字符区间原样读取，旧版本与不可见项省略。"""
        scope = SectionReadScope(scope)
        documents = await self._reader.read_documents_by_section_ids(
            section_ids, snapshot=snapshot
        )
        sections = _section_locations(documents)
        result = []
        for section_id in dict.fromkeys(section_ids):
            if (location := sections.get(section_id)) is None:
                continue
            document, section = location
            span = (
                section.own_span
                if scope is SectionReadScope.OWN
                else section.subtree_span
            )
            # Python 切片会静默截断越界范围；数据库事实损坏必须明确失败。
            if not 0 <= span.start_offset <= span.end_offset <= len(document.text):
                raise RuntimeError("invalid section source span")
            result.append(
                SectionText(
                    document.resource_id,
                    document.content_revision,
                    section.section_id,
                    section.section_path,
                    document.text[span.start_offset: span.end_offset],
                )
            )
        return result

    async def read_chunks(
            self, chunk_ids: list[str], *, snapshot: ResourceSnapshot
    ) -> list[ChunkText]:
        """按 Chunk ID 原样读取正文，供已持有定位的下游延迟取原文。"""
        chunks = await self._reader.read_chunks(
            list(dict.fromkeys(chunk_ids)), snapshot=snapshot
        )
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        return [
            ChunkText(
                chunk.resource_id,
                chunk.content_revision,
                chunk.chunk_id,
                chunk.section_id,
                chunk.section_path,
                chunk.raw_text,
            )
            for chunk_id in dict.fromkeys(chunk_ids)
            if (chunk := by_id.get(chunk_id)) is not None
        ]


def _section_locations(
        documents: list[Document],
) -> dict[str, tuple[Document, Section]]:
    """仅为已通过共享 Reader 检查的事实建立章节索引。"""
    return {
        section.section_id: (document, section)
        for document in documents
        for section in document.structure.sections
    }
