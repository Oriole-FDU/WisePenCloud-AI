from datetime import datetime, timezone

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from chat.application.rag.enums import ResourceKind
from chat.application.rag.runtime.models import RetrieveChunk, SearchChunk


class RetrieveChunkDocument(Document):

    user_id: str = Field(..., description="用户 ID")
    resource_kind: ResourceKind = Field(..., description="资源类型")
    resource_id: str = Field(..., description="资源 ID")
    index_version: str = Field(..., description="索引版本")

    chunk_id: str = Field(..., description="父块 ID")
    chunk_index: int = Field(..., description="父块序号")
    text: str = Field(..., description="父块原文")
    content_hash: str = Field(..., description="父块原文 hash")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "rag_retrieve_chunks"
        indexes = [
            IndexModel(
                [
                    ("user_id", ASCENDING),
                    ("resource_kind", ASCENDING),
                    ("resource_id", ASCENDING),
                    ("index_version", ASCENDING),
                    ("chunk_id", ASCENDING),
                ],
                unique=True,
                name="uq_retrieve_chunk",
            ),
            IndexModel(
                [
                    ("user_id", ASCENDING),
                    ("resource_kind", ASCENDING),
                    ("resource_id", ASCENDING),
                    ("index_version", ASCENDING),
                    ("chunk_index", ASCENDING),
                ],
                name="idx_retrieve_chunk_order",
            ),
        ]

    def to_domain(self) -> RetrieveChunk:
        return RetrieveChunk(
            chunk_id=self.chunk_id,
            resource_id=self.resource_id,
            resource_kind=self.resource_kind,
            chunk_index=self.chunk_index,
            text=self.text,
            content_hash=self.content_hash,
        )

    @classmethod
    def from_domain(
        cls,
        *,
        user_id: str,
        index_version: str,
        chunk: RetrieveChunk,
    ) -> "RetrieveChunkDocument":
        return cls(
            user_id=user_id,
            resource_kind=chunk.resource_kind,
            resource_id=chunk.resource_id,
            index_version=index_version,
            chunk_id=chunk.chunk_id,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            content_hash=chunk.content_hash,
        )


class SearchChunkDocument(Document):

    user_id: str = Field(..., description="用户 ID")
    resource_kind: ResourceKind = Field(..., description="资源类型")
    resource_id: str = Field(..., description="资源 ID")
    index_version: str = Field(..., description="索引版本")

    chunk_id: str = Field(..., description="子块 ID")
    parent_chunk_id: str = Field(..., description="父块 ID")
    parent_chunk_index: int = Field(..., description="父块序号")
    chunk_index: int = Field(..., description="子块在父块内的序号")

    text: str = Field(..., description="子块原文")
    content_hash: str = Field(..., description="子块原文 hash")
    retrieval_context: str = Field(..., description="Context Indexing 生成的检索上下文")
    semantic_indexing_text: str = Field(..., description="dense / sparse 召回使用的增强索引文本")
    keyword_text: str = Field(..., description="keyword exact 使用的原文索引文本")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "rag_search_chunks"
        indexes = [
            IndexModel(
                [
                    ("user_id", ASCENDING),
                    ("resource_kind", ASCENDING),
                    ("resource_id", ASCENDING),
                    ("index_version", ASCENDING),
                    ("chunk_id", ASCENDING),
                ],
                unique=True,
                name="uq_search_chunk",
            ),
            IndexModel(
                [
                    ("user_id", ASCENDING),
                    ("resource_kind", ASCENDING),
                    ("resource_id", ASCENDING),
                    ("index_version", ASCENDING),
                    ("parent_chunk_id", ASCENDING),
                ],
                name="idx_search_parent_chunk",
            ),
        ]

    def to_domain(self) -> SearchChunk:
        return SearchChunk(
            chunk_id=self.chunk_id,
            parent_chunk_id=self.parent_chunk_id,
            resource_id=self.resource_id,
            resource_kind=self.resource_kind,
            parent_chunk_index=self.parent_chunk_index,
            chunk_index=self.chunk_index,
            text=self.text,
            content_hash=self.content_hash,
        )

    @classmethod
    def from_domain(
        cls,
        *,
        user_id: str,
        index_version: str,
        chunk: SearchChunk,
        retrieval_context: str,
        semantic_indexing_text: str,
        keyword_text: str,
    ) -> "SearchChunkDocument":
        return cls(
            user_id=user_id,
            resource_kind=chunk.resource_kind,
            resource_id=chunk.resource_id,
            index_version=index_version,
            chunk_id=chunk.chunk_id,
            parent_chunk_id=chunk.parent_chunk_id,
            parent_chunk_index=chunk.parent_chunk_index,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            content_hash=chunk.content_hash,
            retrieval_context=retrieval_context,
            semantic_indexing_text=semantic_indexing_text,
            keyword_text=keyword_text,
        )