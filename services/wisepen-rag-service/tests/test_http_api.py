"""RAG V3 HTTP 查询接口的传输边界测试。"""

import importlib
from dataclasses import dataclass

import pytest
from common.core.domain import GroupRoleType
from common.security import SecurityContextHolder
from common.utils.document import SourceSpan
from common.utils.ranking import RankDecision
from common.web.exception_handlers import setup_global_exception_handlers
from dependency_injector import providers
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag.application.reading import DocumentReadError, ReadPageItem, ReadSectionItem
from rag.application.retrieval.models import (
    ChunkHit,
    DynamicParent,
    HybridRetrievalResult,
)


@dataclass
class _Reader:
    """记录 API 传入 scope，证明请求体不会参与 ACL 构造。"""

    last_scope: object | None = None
    missing: bool = False

    async def read_pages(self, resource_id, page_labels, *, scope):
        self.last_scope = scope
        if self.missing:
            raise DocumentReadError("page is not visible")
        return [ReadPageItem(label, f"content:{label}") for label in page_labels]

    async def read_sections(self, section_ids, *, mode, max_depth, scope):
        self.last_scope = scope
        return [
            ReadSectionItem("resource-1", section_id, "A > B", f"{mode}:{max_depth}")
            for section_id in section_ids
        ]


@dataclass
class _Outline:
    missing: bool = False

    async def neighborhood(self, section_ids, *, sibling_steps, scope):
        if self.missing:
            raise DocumentReadError("section is not visible")
        return [
            type(
                "Item",
                (),
                {
                    "resource_id": "resource-1",
                    "section_id": section_id,
                    "section_path": "Title",
                    "outline": "- Title [C] (5 chars)",
                },
            )()
            for section_id in section_ids
        ]

    async def global_outline(self, resource_id, *, max_level, scope):
        if self.missing:
            raise DocumentReadError("document is not visible")
        return f"max_level={max_level}"


class _Retriever:
    async def retrieve(self, semantic_query, top_k, *, lexical_query, scope):
        return HybridRetrievalResult(
            relevance_decision=RankDecision.RELEVANT,
            hits=[
                ChunkHit(
                    chunk_id="chunk-1",
                    resource_id="resource-1",
                    content_revision="resource-1@1",
                    section_id="section-1",
                    section_path=["Title"],
                    rerank_score=0.9,
                    node_ids=["node-1"],
                )
            ],
            parents=[
                DynamicParent(
                    parent_id="parent-1",
                    resource_id="resource-1",
                    content_revision="resource-1@1",
                    section_id="section-1",
                    section_path=["Title", "Section"],
                    text="parent content",
                    source_spans=[SourceSpan(0, 14)],
                    matched_chunk_ids=["chunk-1"],
                    score=0.9,
                )
            ],
        )


@pytest.fixture
def api_client(monkeypatch):
    """使用内存 application 替身构造路由，不触发 Nacos 或外部存储。"""
    monkeypatch.setenv("NACOS_SERVER_ADDR", "127.0.0.1:8848")

    nacos = importlib.import_module("rag.core.config.nacos")

    async def pull_config():
        return """
MONGODB_URL: mongodb://localhost:27017
MONGODB_DB_NAME: rag
LLM_BASE_URL: https://example.test/v1
LLM_API_KEY: test
QUERY_MODEL: test-query
EMBEDDING_MODEL: test-embedding
EMBEDDING_DIMENSIONS: 3
QDRANT_HOST: localhost
"""

    monkeypatch.setattr(nacos.nacos_client_manager, "pull_config", pull_config)
    router_module = importlib.import_module("rag.api.router")
    container_module = importlib.import_module("rag.container")
    reading_module = importlib.import_module("rag.api.endpoints.reading")
    retrieval_module = importlib.import_module("rag.api.endpoints.retrieval")

    reader = _Reader()
    outline = _Outline()
    container_module.container.document_reader.override(providers.Object(reader))
    container_module.container.outline_builder.override(providers.Object(outline))
    container_module.container.hybrid_retriever.override(providers.Object(_Retriever()))
    container_module.container.wire(modules=[reading_module, retrieval_module])

    app = FastAPI()
    setup_global_exception_handlers(app)
    app.include_router(router_module.api_router, prefix="/rag")
    SecurityContextHolder.set_user_id("user-1")
    SecurityContextHolder.set_group_role_map('{"group-1": 1}')
    try:
        yield TestClient(app), reader, outline
    finally:
        container_module.container.unwire()
        container_module.container.document_reader.reset_override()
        container_module.container.outline_builder.reset_override()
        container_module.container.hybrid_retriever.reset_override()


def test_query_routes_return_transport_shapes_and_context_scope(api_client) -> None:
    client, reader, _ = api_client

    hybrid = client.post(
        "/rag/retrieval/searchHybrid",
        json={"semantic_query": "query", "lexical_query": "keywords", "top_k": 3},
    )
    assert hybrid.status_code == 200
    parent = hybrid.json()["data"]["parents"][0]
    assert parent == {
        "resource_id": "resource-1",
        "section_id": "section-1",
        "section_path": "Title > Section",
        "text": "parent content",
        "score": 0.9,
    }

    pages = client.post(
        "/rag/reading/readPages",
        json={"resource_id": "resource-1", "page_labels": ["A-2", "A-1"]},
    )
    assert pages.json()["data"]["pages"] == [
        {"page_label": "A-2", "content": "content:A-2"},
        {"page_label": "A-1", "content": "content:A-1"},
    ]
    assert reader.last_scope.user_id == "user-1"
    assert reader.last_scope.group_roles == {"group-1": GroupRoleType.ADMIN}

    sections = client.post(
        "/rag/reading/readSections",
        json={"section_ids": ["section-1"], "mode": "recursive", "max_depth": 0},
    )
    assert sections.json()["data"]["sections"][0]["content"] == "recursive:0"

    neighborhood = client.post(
        "/rag/reading/getNeighborhood",
        json={"section_ids": ["section-1"], "sibling_steps": 0},
    )
    assert neighborhood.json()["data"]["items"][0] == {
        "resource_id": "resource-1",
        "section_id": "section-1",
        "section_path": "Title",
        "outline": "- Title [C] (5 chars)",
    }

    outline = client.post(
        "/rag/reading/getGlobalOutline",
        json={"resource_id": "resource-1", "max_level": 0},
    )
    assert outline.json()["data"] == {
        "resource_id": "resource-1",
        "outline": "max_level=0",
    }


def test_query_routes_reject_unknown_fields_and_hide_missing_pages(api_client) -> None:
    client, reader, _ = api_client

    invalid = client.post(
        "/rag/retrieval/searchHybrid",
        json={"semantic_query": "query", "top_k": 1, "user_id": "forged"},
    )
    assert invalid.status_code == 400

    reader.missing = True
    missing = client.post(
        "/rag/reading/readPages",
        json={"resource_id": "resource-1", "page_labels": ["missing"]},
    )
    assert missing.status_code == 200
    assert missing.json()["msg"] == "资源不存在或不可访问"
