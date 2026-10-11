"""读取测试显式注入本地 ACL 替身与真实内容/发布 adapter，不加载运行配置。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.application.publication.publish import DocumentReadyEvent
from rag.application.snapshot import ResourceSnapshotReader
from rag.core.schema import Chunk, ContentRevision


@pytest.fixture
def make_read_document():
    def make(resource_id="resource", version=1, text="  中文原文😀\n"):
        return DocumentReadyEvent.model_validate(
            {"resourceId": resource_id, "version": version, "content": text}
        ).to_document()

    return make


@pytest.fixture
def make_read_chunk():
    def make(document, index=0):
        return Chunk(
            chunk_id=f"{document.content_revision}:chunk:{index}",
            resource_id=document.resource_id,
            content_revision=document.content_revision,
            chunk_index=index,
            section_id=None,
            section_path=[],
            raw_text=f"正文 {index}",
            content_token_count=2,
            anchor_labels=[],
        )

    return make


@pytest.fixture
def publish_read_document(storage, publication_store):
    async def publish(document):
        revision = ContentRevision.create(
            resource_id=document.resource_id,
            document_version=document.document_version,
            text=document.text,
        )
        await storage.upsert_documents([document])
        await publication_store.stage_revision(revision)
        await publication_store.apply_revision(revision)
        return document

    return publish


@pytest.fixture
def read_acls():
    return {}


@pytest.fixture
def read_acl_repository(read_acls):
    return SimpleNamespace(
        get_resource_acls=AsyncMock(
            side_effect=lambda resource_ids: {
                resource_id: read_acls[resource_id]
                for resource_id in resource_ids
                if resource_id in read_acls
            }
        )
    )


@pytest.fixture
def snapshot_reader(publication_store, read_acl_repository, storage):
    publication_store.get_states = AsyncMock(wraps=publication_store.get_states)
    return ResourceSnapshotReader(
        store=publication_store, resource_acls=read_acl_repository, reader=storage
    )
