"""组合 Mongo 文档与 Chunk 内容操作，不管理发布指针或跨集合事务。"""

from pydantic import TypeAdapter
from pymongo import ASCENDING, IndexModel, ReplaceOne
from pymongo.asynchronous.collection import AsyncCollection

from rag.core.schema import Chunk, Document
from rag.storage.content import ContentStorage

_document_adapter = TypeAdapter(Document)
_chunk_adapter = TypeAdapter(Chunk)

_BATCH_SIZE = 500  # bulk_write 批大小，过大易超服务端命令体积上限


class MongoContentStorage(ContentStorage):
    """绑定两个内容集合，提供批量存取；实例不访问全局配置或连接。

    章节树和原文范围由 Common 构建，RAG 仅重写身份；读取不重复验证内部算法。
    """

    def __init__(
            self,
            *,
            documents: AsyncCollection,
            chunks: AsyncCollection,
    ) -> None:
        self._documents = documents
        self._chunks = chunks

    async def upsert_documents(self, documents: list[Document]) -> None:
        """按 (resource_id, content_revision) 替换写入文档，分批提交。"""
        for start in range(0, len(documents), _BATCH_SIZE):
            operations = [
                ReplaceOne(
                    {
                        "resource_id": document.resource_id,
                        "content_revision": document.content_revision,
                    },
                    _document_adapter.dump_python(document, mode="python"),
                    upsert=True,
                )
                for document in documents[start: start + _BATCH_SIZE]
            ]
            await self._documents.bulk_write(operations, ordered=True)

    async def get_documents(self, revisions: list[tuple[str, str]]) -> list[Document]:
        """按 (resource_id, content_revision) 批量读取；去重保序，缺失项跳过。"""
        unique_revisions = list(dict.fromkeys(revisions))
        if not unique_revisions:
            return []

        records = await self._documents.find(
            {
                "$or": [
                    {"resource_id": resource_id, "content_revision": content_revision}
                    for resource_id, content_revision in unique_revisions
                ]
            }
        ).to_list(length=None)
        records_by_revision = {
            (record["resource_id"], record["content_revision"]): record
            for record in records
        }
        # $or 结果顺序不保证，按输入顺序还原
        return [
            _document_adapter.validate_python(records_by_revision[revision])
            for revision in unique_revisions
            if revision in records_by_revision
        ]

    async def upsert_chunks(self, chunks: list[Chunk]) -> None:
        """按 chunk_id 替换写入 chunk，分批提交。"""
        for start in range(0, len(chunks), _BATCH_SIZE):
            operations = [
                ReplaceOne(
                    {"chunk_id": chunk.chunk_id},
                    _chunk_adapter.dump_python(chunk, mode="python"),
                    upsert=True,
                )
                for chunk in chunks[start: start + _BATCH_SIZE]
            ]
            await self._chunks.bulk_write(operations, ordered=True)

    async def get_documents_by_section_ids(
            self, section_ids: list[str]
    ) -> list[Document]:
        """定位章节事实所属的文档；不选择发布版本或判断可见性。"""
        unique_ids = list(dict.fromkeys(section_ids))
        if not unique_ids:
            return []
        records = await self._documents.find(
            {"structure.sections.section_id": {"$in": unique_ids}}
        ).to_list(length=None)

        documents_by_section = {}
        for record in records:
            document = _document_adapter.validate_python(record)
            for section in document.structure.sections:
                documents_by_section[section.section_id] = document

        # 同一文档可能匹配多个章节；按请求还原顺序且只返回一次。
        documents: dict[tuple[str, str], Document] = {}
        for section_id in unique_ids:
            if (document := documents_by_section.get(section_id)) is not None:
                documents.setdefault(
                    (document.resource_id, document.content_revision), document
                )

        return list(documents.values())

    async def get_chunks(self, chunk_ids: list[str]) -> list[Chunk]:
        """按 chunk_id 批量读取；去重保序，缺失项跳过。"""
        unique_ids = list(dict.fromkeys(chunk_ids))
        if not unique_ids:
            return []

        records = await self._chunks.find({"chunk_id": {"$in": unique_ids}}).to_list(
            length=None
        )
        records_by_id = {record["chunk_id"]: record for record in records}
        # $in 结果顺序不保证，按输入顺序还原
        return [
            _chunk_adapter.validate_python(records_by_id[chunk_id])
            for chunk_id in unique_ids
            if chunk_id in records_by_id
        ]

    async def get_revision_chunks(
            self,
            *,
            resource_id: str,
            content_revision: str,
    ) -> list[Chunk]:
        """读取指定 revision 下的全部 chunk，按 chunk_index 升序返回。"""
        records = await (
            self._chunks.find(
                {"resource_id": resource_id, "content_revision": content_revision}
            )
            .sort("chunk_index", ASCENDING)
            .to_list(length=None)
        )
        return [_chunk_adapter.validate_python(record) for record in records]

    async def delete_revisions(self, revisions: list[tuple[str, str]]) -> None:
        """按 (resource_id, content_revision) 物理删除文档与 chunk。

        调用方需先撤销可见性；两次删除串行执行，失败可重放但不补偿。
        """
        unique_revisions = list(dict.fromkeys(revisions))
        if not unique_revisions:
            return

        query = {
            "$or": [
                {"resource_id": resource_id, "content_revision": content_revision}
                for resource_id, content_revision in unique_revisions
            ]
        }
        await self._documents.delete_many(query)
        await self._chunks.delete_many(query)

    async def delete_resources(self, resource_ids: list[str]) -> None:
        """按 resource_id 物理删除其下全部文档与 chunk。"""
        unique_ids = list(dict.fromkeys(resource_ids))
        if not unique_ids:
            return

        query = {"resource_id": {"$in": unique_ids}}
        await self._documents.delete_many(query)
        await self._chunks.delete_many(query)

    async def ensure_indexes(self) -> None:
        """由后端初始化显式调用，不在普通读写中隐式创建索引。"""
        await self._documents.create_indexes(
            [
                IndexModel(
                    [("resource_id", ASCENDING), ("content_revision", ASCENDING)],
                    name="document_resource_revision_unique",
                    unique=True,
                ),
                IndexModel(
                    [("structure.sections.section_id", ASCENDING)],
                    name="document_section_id",
                ),
            ]
        )
        await self._chunks.create_indexes(
            [
                IndexModel(
                    [("chunk_id", ASCENDING)], name="chunk_id_unique", unique=True
                ),
                IndexModel(
                    [
                        ("resource_id", ASCENDING),
                        ("content_revision", ASCENDING),
                        ("chunk_index", ASCENDING),
                    ],
                    name="chunk_revision_index_unique",
                    unique=True,
                ),
            ]
        )
