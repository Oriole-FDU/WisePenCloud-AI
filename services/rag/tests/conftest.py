"""内容存储测试使用 BSON 往返的内存 collection，不读取服务配置。"""

from unittest.mock import AsyncMock, Mock

import pytest
from bson import BSON
from common.utils.markdown import MarkdownChunker

from rag.backends.mongo.content import MongoContentStorage
from rag.core.schema import Chunk, Document, DocumentStructure


def _matches(record, query):
    for key, value in query.items():
        if key == "$or":
            if not any(_matches(record, clause) for clause in value):
                return False
        elif isinstance(value, dict) and "$in" in value:
            if record.get(key) not in value["$in"]:
                return False
        elif record.get(key) != value:
            return False
    return True


class MemoryCursor:
    def __init__(self, records):
        self._records = records

    def sort(self, key, direction):
        self._records.sort(key=lambda record: record[key], reverse=direction < 0)
        return self

    async def to_list(self, *, length):
        return self._records


class MemoryCollection:
    """只模拟测试用到的查询与写入；真正唯一索引语义由 Mongo 集成测试验证。"""

    def __init__(self):
        self.records = []
        self.error = RuntimeError("Mongo 写入失败")
        self.fail_bulk_call = None
        self.bulk_write = AsyncMock(side_effect=self._bulk_write)
        self.find = Mock(side_effect=self._find)
        self.delete_many = AsyncMock(side_effect=self._delete_many)
        self.create_indexes = AsyncMock()

    async def _bulk_write(self, operations, *, ordered):
        assert ordered is True
        if self.bulk_write.await_count == self.fail_bulk_call:
            raise self.error
        # PyMongo 的操作对象没有公开读属性；fake 读取驱动 payload 并真正执行 BSON 编码。
        for operation in operations:
            assert operation._upsert is True
            record = BSON(BSON.encode(operation._doc)).decode()
            for index, existing in enumerate(self.records):
                if _matches(existing, operation._filter):
                    record["_id"] = existing["_id"]
                    self.records[index] = record
                    break
            else:
                record["_id"] = len(self.records)
                self.records.append(record)

    def _find(self, query):
        # 返回与插入相反的顺序，避免测试意外依赖 Mongo 的自然顺序。
        return MemoryCursor(
            [
                BSON(BSON.encode(record)).decode()
                for record in reversed(self.records)
                if _matches(record, query)
            ]
        )

    async def _delete_many(self, query):
        self.records[:] = [
            record for record in self.records if not _matches(record, query)
        ]


@pytest.fixture
def document():
    markdown = (
        "# 标题\n\n  中文正文。\n\n表 1: 数据\n\n| A | B |\n| - | - |\n| 1 | 2 |\n"
    )
    result = MarkdownChunker().chunk(markdown)
    return Document(
        resource_id="resource",
        document_version=7,
        content_revision="revision",
        text=markdown,
        structure=DocumentStructure(len(markdown), result.sections, result.anchors),
        metadata={"custom": {"enabled": True}},
    )


@pytest.fixture
def chunk():
    return Chunk(
        chunk_id="chunk",
        resource_id="resource",
        content_revision="revision",
        chunk_index=0,
        section_id="section",
        section_path=["标题"],
        raw_text="  中文正文\n",
        content_token_count=5,
        anchor_labels=["Table 1"],
        metadata={"contextual_context": "文档上下文", "custom": [1, None, True]},
    )


@pytest.fixture
def collections():
    return MemoryCollection(), MemoryCollection()


@pytest.fixture
def storage(collections):
    documents, chunks = collections
    return MongoContentStorage(documents=documents, chunks=chunks)
