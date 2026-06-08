from datetime import datetime
from typing import List

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class RagChunkContextCacheDocument(Document):

    user_id: str = Field(...)
    context_model_version: str = Field(...)
    context_prompt_version: str = Field(...)
    context_input_hash: str = Field(...)

    context_text: str = Field(...)

    source_material_hash: str = Field(...)
    source_display_name: str = Field(...)

    created_at: datetime = Field(...)

    class Settings:
        name = "rag_chunk_contexts"
        indexes = [
            IndexModel(
                [
                    ("user_id", ASCENDING),
                    ("context_model_version", ASCENDING),
                    ("context_prompt_version", ASCENDING),
                    ("context_input_hash", ASCENDING),
                ],
                unique=True,
                name="uniq_rag_chunk_context_cache_key",
            ),
        ]


class RagDenseEmbeddingCacheDocument(Document):

    dense_embedding_model_version: str = Field(...)
    text_hash: str = Field(...)
    vector: List[float] = Field(...)

    created_at: datetime = Field(...)

    class Settings:
        name = "rag_dense_embedding_cache"
        indexes = [
            IndexModel(
                [
                    ("dense_embedding_model_version", ASCENDING),
                    ("text_hash", ASCENDING),
                ],
                unique=True,
                name="uniq_rag_dense_embedding_cache_key",
            ),
        ]


class RagQueryEmbeddingCacheDocument(Document):

    dense_embedding_model_version: str = Field(...)
    query_text_hash: str = Field(...)
    query_text: str = Field(...)
    vector: List[float] = Field(...)

    created_at: datetime = Field(...)
    expires_at: datetime = Field(...)

    class Settings:
        name = "rag_query_embedding_cache"
        indexes = [
            IndexModel(
                [
                    ("dense_embedding_model_version", ASCENDING),
                    ("query_text_hash", ASCENDING),
                ],
                unique=True,
                name="uniq_rag_query_embedding_cache_key",
            ),
            IndexModel(
                [("expires_at", ASCENDING)],
                expireAfterSeconds=0,
                name="ttl_rag_query_embedding_cache",
            ),
        ]