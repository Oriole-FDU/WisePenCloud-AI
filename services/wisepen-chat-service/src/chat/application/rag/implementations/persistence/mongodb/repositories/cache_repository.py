import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Dict, List, Optional

from beanie.odm.operators.update.general import Set
from chat.application.rag.implementations.persistence.mongodb.entities.cache_documents import (
    RagChunkContextCacheDocument,
    RagDenseEmbeddingCacheDocument,
    RagQueryEmbeddingCacheDocument,
)

from chat.application.rag.domain.index_chunks import DenseVector
from chat.application.rag.domain.ports import (
    RagContextCacheLookup,
    RagContextCacheRepository,
    RagContextCacheWrite,
    RagDenseEmbeddingCacheLookup,
    RagDenseEmbeddingCacheRepository,
    RagDenseEmbeddingCacheWrite,
    RagQueryEmbeddingCacheLookup,
    RagQueryEmbeddingCacheRepository,
    RagQueryEmbeddingCacheWrite,
)

_CACHE_WRITE_CONCURRENCY = 16


class MongoRagContextCacheRepository(RagContextCacheRepository):

    async def get_contexts(
            self,
            lookups: List[RagContextCacheLookup],
    ) -> Dict[str, str]:
        if not lookups:
            return {}

        lookup_ids_by_key = defaultdict(list)
        for lookup in lookups:
            key = (
                lookup.user_id,
                lookup.context_model_version,
                lookup.context_prompt_version,
                lookup.context_input_hash,
            )
            lookup_ids_by_key[key].append(lookup.lookup_id)

        documents = await RagChunkContextCacheDocument.find(
            {
                "$or": [
                    {
                        "user_id": k[0],
                        "context_model_version": k[1],
                        "context_prompt_version": k[2],
                        "context_input_hash": k[3],
                    }
                    for k in lookup_ids_by_key
                ]
            }
        ).to_list()

        result: Dict[str, str] = {}
        for doc in documents:
            key = (
                doc.user_id,
                doc.context_model_version,
                doc.context_prompt_version,
                doc.context_input_hash
            )
            for lookup_id in lookup_ids_by_key.get(key, []):
                result[lookup_id] = doc.context_text

        return result

    async def put_contexts(
            self,
            writes: List[RagContextCacheWrite],
    ) -> None:
        if not writes:
            return

        now = datetime.now(timezone.utc)

        deduped_writes_by_key = {}
        for write in writes:
            key = (
                write.user_id,
                write.context_model_version,
                write.context_prompt_version,
                write.context_input_hash,
            )
            if key not in deduped_writes_by_key:
                deduped_writes_by_key[key] = write

        tasks = [
            RagChunkContextCacheDocument.find_one(
                {
                    "user_id": write.user_id,
                    "context_model_version": write.context_model_version,
                    "context_prompt_version": write.context_prompt_version,
                    "context_input_hash": write.context_input_hash,
                }
            ).upsert(
                Set(
                    {
                        "context_text": write.context_text,
                        "source_material_hash": write.source_material_hash,
                        "source_display_name": write.source_display_name,
                    }
                ),
                on_insert=RagChunkContextCacheDocument(
                    user_id=write.user_id,
                    context_model_version=write.context_model_version,
                    context_prompt_version=write.context_prompt_version,
                    context_input_hash=write.context_input_hash,
                    context_text=write.context_text,
                    source_material_hash=write.source_material_hash,
                    source_display_name=write.source_display_name,
                    created_at=now,
                ),
            )
            for write in deduped_writes_by_key.values()
        ]

        await _gather_limited(
            tasks,
            limit=_CACHE_WRITE_CONCURRENCY,
        )


class MongoRagDenseEmbeddingCacheRepository(RagDenseEmbeddingCacheRepository):

    async def get_vectors(
            self,
            lookups: List[RagDenseEmbeddingCacheLookup],
    ) -> Dict[str, DenseVector]:
        if not lookups:
            return {}

        lookup_ids_by_key = defaultdict(list)
        for lookup in lookups:
            key = (lookup.dense_embedding_model_version, lookup.text_hash)
            lookup_ids_by_key[key].append(lookup.lookup_id)

        documents = await RagDenseEmbeddingCacheDocument.find(
            {
                "$or": [
                    {
                        "dense_embedding_model_version": model_version,
                        "text_hash": text_hash,
                    }
                    for model_version, text_hash in lookup_ids_by_key.items()
                ]
            }
        ).to_list()

        result: Dict[str, DenseVector] = {}
        for doc in documents:
            key = (doc.dense_embedding_model_version, doc.text_hash)
            for lookup_id in lookup_ids_by_key.get(key, []):
                result[lookup_id] = doc.vector

        return result

    async def put_vectors(
            self,
            writes: List[RagDenseEmbeddingCacheWrite],
    ) -> None:
        if not writes:
            return

        now = datetime.now(timezone.utc)

        deduped_writes_by_key = {}
        for write in writes:
            key = (
                write.dense_embedding_model_version,
                write.text_hash,
            )
            if key not in deduped_writes_by_key:
                deduped_writes_by_key[key] = write

        tasks = [
            RagDenseEmbeddingCacheDocument.find_one(
                {
                    "dense_embedding_model_version": write.dense_embedding_model_version,
                    "text_hash": write.text_hash,
                }
            ).upsert(
                Set(
                    {
                        "vector": write.vector
                    }
                ),
                on_insert=RagDenseEmbeddingCacheDocument(
                    dense_embedding_model_version=write.dense_embedding_model_version,
                    text_hash=write.text_hash,
                    vector=write.vector,
                    created_at=now,
                ),
            )
            for write in deduped_writes_by_key.values()
        ]

        await _gather_limited(
            tasks,
            limit=_CACHE_WRITE_CONCURRENCY,
        )


class MongoRagQueryEmbeddingCacheRepository(RagQueryEmbeddingCacheRepository):

    async def get_vector(
            self,
            lookup: RagQueryEmbeddingCacheLookup,
    ) -> Optional[DenseVector]:
        document = await RagQueryEmbeddingCacheDocument.find_one(
            {
                "dense_embedding_model_version": lookup.dense_embedding_model_version,
                "query_text_hash": lookup.query_text_hash,
            }
        )
        return document.vector if document else None

    async def put_vector(
            self,
            write: RagQueryEmbeddingCacheWrite,
            ttl_days: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=ttl_days)

        await RagQueryEmbeddingCacheDocument.find_one(
            {
                "dense_embedding_model_version": write.dense_embedding_model_version,
                "query_text_hash": write.query_text_hash,
            }
        ).upsert(
            Set(
                {
                    "query_text": write.query_text,
                    "vector": write.vector,
                    "expires_at": expires_at,
                }
            ),
            on_insert=RagQueryEmbeddingCacheDocument(
                dense_embedding_model_version=write.dense_embedding_model_version,
                query_text_hash=write.query_text_hash,
                query_text=write.query_text,
                vector=write.vector,
                created_at=now,
                expires_at=expires_at,
            ),
        )


async def _gather_limited(
        awaitables: List[Awaitable],
        *,
        limit: int,
) -> List:
    semaphore = asyncio.Semaphore(limit)

    async def run(awaitable: Awaitable):
        async with semaphore:
            return await awaitable

    return await asyncio.gather(*[run(awaitable) for awaitable in awaitables])