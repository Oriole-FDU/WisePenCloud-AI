"""文档与分块的批量内容存储合同；不解释 ACL、active 或发布生命周期。"""

from typing import Protocol

from rag.core.schema import Chunk, Document


class ContentReader(Protocol):
    """按明确身份读取内容；批量结果去重、保序并省略缺失对象。"""

    async def get_documents(
        self,
        revisions: list[tuple[str, str]],  # (resource_id, content_revision)
    ) -> list[Document]: ...

    async def get_documents_by_section_ids(
        self, section_ids: list[str]
    ) -> list[Document]:
        """按章节 ID 批量定位文档；文档去重，按首次匹配章节的请求顺序返回。"""
        ...

    async def get_chunks(self, chunk_ids: list[str]) -> list[Chunk]: ...

    async def get_revision_chunks(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> list[Chunk]:
        """读取指定 revision 的全部 Chunk，按 chunk_index 升序返回。"""
        ...


class ContentWriter(Protocol):
    """完整对象 upsert 与物理删除；部分成功保留，不负责撤销可见性。"""

    async def upsert_documents(self, documents: list[Document]) -> None: ...

    async def upsert_chunks(self, chunks: list[Chunk]) -> None: ...

    async def delete_revisions(self, revisions: list[tuple[str, str]]) -> None:
        """物理删除指定 (resource_id, content_revision) 的 Document 和 Chunk。"""
        ...

    async def delete_resources(self, resource_ids: list[str]) -> None:
        """物理删除指定资源的全部 Document 和 Chunk，调用方先撤销可见性。"""
        ...


class ContentStorage(ContentReader, ContentWriter, Protocol):
    """同一内容后端组合读写接口，不合并 Document 与 Chunk 模型。"""
