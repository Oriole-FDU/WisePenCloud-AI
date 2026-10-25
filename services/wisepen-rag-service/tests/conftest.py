"""内容存储测试使用 BSON 往返的内存 collection，不读取服务配置。"""

from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from bson import BSON
from pymongo.errors import DuplicateKeyError

from common.utils.markdown import MarkdownChunker
from rag.backends.mongo.content import MongoContentStorage
from rag.backends.mongo.publication import MongoPublicationStore
from rag.core.schema import (
    Chunk,
    Document,
    DocumentStructure,
    EntitySpec,
    GraphEdge,
    GraphNode,
    Ontology,
    RelationSpec,
)
from rag.core.schema.content import rag_section_id


@pytest.fixture
def platform_settings(monkeypatch):
    import json
    import sys
    from types import ModuleType

    values = {
        "MONGODB_URL": "mongodb://localhost:27017",
        "MONGODB_DB_NAME": "rag-test",
        "QDRANT_URL": "http://localhost:6333",
        "EMBEDDING_BASE_URL": "http://localhost:8000/v1",
        "EMBEDDING_API_KEY": "test-key",
        "EMBEDDING_MODEL": "test-embedding",
        "EMBEDDING_DIMENSIONS": 2,
        "QWEN_RERANKER_MODEL": "test-rerank",
        "QWEN_API_KEY": "test-qwen",
    }
    # app_settings 保持项目惯用的默认加载；测试只替换 Nacos 边界。
    module = ModuleType("rag.core.config.nacos")
    module.nacos_client_manager = SimpleNamespace(
        pull_config=AsyncMock(return_value=json.dumps(values))
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    from rag.core.config.app_settings import AppSettings

    return AppSettings(**values)


@pytest.fixture
def graph_ontology():
    return Ontology(
        domain="work",
        description="工作关系",
        entity_specs={
            "Person": EntitySpec("Person", "人员"),
            "Company": EntitySpec("Company", "公司"),
        },
        relation_specs={
            "works_at": RelationSpec(
                "works_at",
                "人员就职于公司",
                allowed_sources=["Person"],
                allowed_targets=["Company"],
            )
        },
    )


@pytest.fixture
def make_graph_facts():
    def make(chunk, person="张三", company="甲公司", description="来源描述"):
        nodes = [
            GraphNode(person, chunk.chunk_id, "work", "Person", description),
            GraphNode(company, chunk.chunk_id, "work", "Company", description),
        ]
        edge = GraphEdge(
            nodes[0].id,
            nodes[1].id,
            "works_at",
            description,
            ["任职"],
            [chunk.chunk_id],
            {"nested": {"value": 1}},
        )
        chunk.metadata.update(
            entities=[asdict(node) for node in nodes], relationships=[asdict(edge)]
        )
        return nodes, [edge]

    return make


@pytest.fixture
def graph_vector_writers():
    class MemoryVectors:
        """只模拟来源替换；Qdrant 的操作确认由后端测试验证。"""

        def __init__(self):
            self.records = {}
            self.upsert = AsyncMock(side_effect=self._upsert)
            self.delete = AsyncMock(side_effect=self._delete)

        async def _upsert(self, records):
            self.records.update((record.record_id, record) for record in records)

        async def _delete(self, filters):
            chunk_ids = filters.filters[0].value
            self.records = {
                key: record
                for key, record in self.records.items()
                if record.metadata["chunk_id"] not in chunk_ids
            }

    return MemoryVectors(), MemoryVectors()


def _matches(record, query):
    for key, value in query.items():
        if key == "$or":
            if not any(_matches(record, clause) for clause in value):
                return False
        elif isinstance(value, dict) and "$in" in value:
            # 模拟 Mongo 点路径穿过嵌套文档和数组，验证真实章节定位查询形态。
            values = [record]
            for part in key.split("."):
                next_values = []
                for item in values:
                    children = item if isinstance(item, list) else [item]
                    next_values.extend(
                        child[part] for child in children if part in child
                    )
                values = next_values
            if not any(item in value["$in"] for item in values):
                return False
        elif isinstance(value, dict) and "$gt" in value:
            if record[key] <= value["$gt"]:
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

    def limit(self, count):
        self._records = self._records[:count]
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

    def _find(self, query, projection=None):
        # 返回与插入相反的顺序，避免测试意外依赖 Mongo 的自然顺序。
        records = [
            BSON(BSON.encode(record)).decode()
            for record in reversed(self.records)
            if _matches(record, query)
        ]
        if projection is not None:
            records = [
                {key: value for key, value in record.items() if projection.get(key)}
                for record in records
            ]
        return MemoryCursor(records)

    async def _delete_many(self, query):
        self.records[:] = [
            record for record in self.records if not _matches(record, query)
        ]


class MemoryPublicationCollection(MemoryCollection):
    """模拟发布用到的相等条件更新与唯一资源身份；真实竞争另由集成测试验证。"""

    def __init__(self):
        super().__init__()
        self.before_insert = None
        self.before_update = None
        self.after_find = None
        self.find_one = AsyncMock(side_effect=self._find_one)
        self.insert_one = AsyncMock(side_effect=self._insert_one)
        self.update_one = AsyncMock(side_effect=self._update_one)

    async def _find_one(self, query):
        records = self._find(query)._records
        record = records[0] if records else None
        # 先取观察副本，再触发竞争写入，模拟读与条件更新之间的状态变化。
        hook, self.after_find = self.after_find, None
        if hook is not None:
            await hook()
        return record

    async def _insert_one(self, record):
        hook, self.before_insert = self.before_insert, None
        if hook is not None:
            await hook()
        if any(
                existing["resource_id"] == record["resource_id"]
                for existing in self.records
        ):
            raise DuplicateKeyError("duplicate resource_id")
        record = BSON(BSON.encode(record)).decode()
        record["_id"] = len(self.records)
        self.records.append(record)
        return SimpleNamespace(inserted_id=record["_id"])

    async def _update_one(self, query, update, *, upsert=False):
        hook, self.before_update = self.before_update, None
        if hook is not None:
            await hook()
        for index, existing in enumerate(self.records):
            if _matches(existing, query):
                record = BSON(BSON.encode({**existing, **update["$set"]})).decode()
                self.records[index] = record
                return SimpleNamespace(
                    matched_count=1,
                    modified_count=int(record != existing),
                    upserted_id=None,
                )
        if upsert:
            record = {**query, **update.get("$setOnInsert", {}), **update["$set"]}
            result = await self._insert_one(record)
            return SimpleNamespace(
                matched_count=0, modified_count=0, upserted_id=result.inserted_id
            )
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

    async def _bulk_write(self, operations, *, ordered):
        assert ordered is True
        if self.bulk_write.await_count == self.fail_bulk_call:
            raise self.error
        for operation in operations:
            await self._update_one(
                operation._filter, operation._doc, upsert=operation._upsert
            )


@pytest.fixture
def publication_collection():
    return MemoryPublicationCollection()


@pytest.fixture
def publication_store(publication_collection):
    return MongoPublicationStore(publication_collection)


@pytest.fixture
def document():
    markdown = (
        "# 标题\n\n  中文正文。\n\n表 1: 数据\n\n| A | B |\n| - | - |\n| 1 | 2 |\n"
    )
    result = MarkdownChunker().chunk(markdown)
    section_ids = {
        section.section_id: rag_section_id(
            resource_id="resource",
            content_revision="revision",
            common_section_id=section.section_id,
        )
        for section in result.sections
    }
    sections = [
        replace(
            section,
            section_id=section_ids[section.section_id],
            parent_section_id=(
                section_ids[section.parent_section_id]
                if section.parent_section_id is not None
                else None
            ),
        )
        for section in result.sections
    ]
    return Document(
        resource_id="resource",
        document_version=7,
        content_revision="revision",
        text=markdown,
        structure=DocumentStructure(len(markdown), sections, result.anchors),
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
