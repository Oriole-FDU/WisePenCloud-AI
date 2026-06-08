import asyncio
from collections import defaultdict
from typing import Any, Dict, List, Optional

from chat.application.rag.implementations.persistence.mongodb.entities.chunk_documents import (
    RetrieveChunkDocument,
    SearchChunkDocument,
)

from chat.application.rag.domain.index_chunks import (
    IndexingTextPair,
    SearchChunkContext,
)
from chat.application.rag.domain.index_chunks import RetrieveChunk, SearchChunk
from chat.application.rag.domain.ports import (
    RagChunkRepository,
    RagNeighborChunkLookup,
    RagRetrieveChunkLookup,
    RagSearchChunkLookup,
    RagSearchChunkRecord,
)
from chat.application.rag.enums import ResourceKind


def _merge_intervals(ranges):
    if not ranges:
        return []

    ordered_ranges = sorted(ranges)
    merged = []

    for start, end in ordered_ranges:
        if not merged or merged[-1][1] < end:
            merged.append((start, end))
        else:
            last_start, last_end = merged[-1]
            if end > last_end:
                merged[-1] = (last_start, end)

    return merged


class MongoChunkRepository(RagChunkRepository):

    def __init__(self, mongo_client: Any) -> None:
        self._mongo_client = mongo_client

    async def replace_chunks(
            self,
            user_id: str,
            resource_kind: ResourceKind,
            resource_id: str,
            index_version: str,
            retrieve_chunks: List[RetrieveChunk],
            search_chunks: List[SearchChunk],
            contexts: List[SearchChunkContext],
            indexing_text_pairs: Dict[str, IndexingTextPair],
    ) -> None:
        context_map = {ctx.chunk_id: ctx for ctx in contexts}

        retrieve_documents = [
            RetrieveChunkDocument.from_domain(
                user_id=user_id, index_version=index_version, chunk=chunk
            )
            for chunk in retrieve_chunks
        ]

        search_documents: List[SearchChunkDocument] = []
        for chunk in search_chunks:
            context = context_map.get(chunk.chunk_id)
            if context is None:
                raise ValueError(f"Context not found for search chunk: {chunk.chunk_id}")

            pair = indexing_text_pairs.get(chunk.chunk_id)
            if pair is None:
                raise ValueError(f"Indexing text not found for search chunk: {chunk.chunk_id}")

            search_documents.append(
                SearchChunkDocument.from_domain(
                    user_id=user_id,
                    index_version=index_version,
                    chunk=chunk,
                    retrieval_context=context.context_text,
                    semantic_indexing_text=pair.semantic_indexing_text,
                    keyword_text=pair.keyword_text,
                )
            )

        async with await self._mongo_client.start_session() as session:
            async with session.start_transaction():
                filter_query = {
                    "user_id": user_id,
                    "resource_kind": resource_kind,
                    "resource_id": resource_id,
                    "index_version": index_version,
                }
                await RetrieveChunkDocument.find(filter_query).delete_many(session=session)
                await SearchChunkDocument.find(filter_query).delete_many(session=session)

                if retrieve_documents:
                    await RetrieveChunkDocument.insert_many(retrieve_documents, session=session)
                if search_documents:
                    await SearchChunkDocument.insert_many(search_documents, session=session)

    async def get_retrieve_chunk(
            self,
            user_id: str,
            resource_kind: ResourceKind,
            resource_id: str,
            index_version: str,
            chunk_id: str,
    ) -> Optional[RetrieveChunk]:
        document = await RetrieveChunkDocument.find_one({
            "user_id": user_id,
            "resource_kind": resource_kind.value,
            "resource_id": resource_id,
            "index_version": index_version,
            "chunk_id": chunk_id,
        })
        return document.to_domain() if document else None

    async def get_search_chunk_record(
            self,
            user_id: str,
            resource_kind: ResourceKind,
            resource_id: str,
            index_version: str,
            chunk_id: str,
    ) -> Optional[RagSearchChunkRecord]:
        document = await SearchChunkDocument.find_one({
            "user_id": user_id,
            "resource_kind": resource_kind.value,
            "resource_id": resource_id,
            "index_version": index_version,
            "chunk_id": chunk_id,
        })

        return RagSearchChunkRecord(
            chunk=document.to_domain(),
            retrieval_context=document.retrieval_context,
            semantic_indexing_text=document.semantic_indexing_text,
            keyword_text=document.keyword_text,
        ) if document else None

    async def get_neighbor_retrieve_chunks(
            self,
            user_id: str,
            resource_kind: ResourceKind,
            resource_id: str,
            index_version: str,
            center_chunk_index: int,
            before: int,
            after: int,
    ) -> List[RetrieveChunk]:
        start_index = max(0, center_chunk_index - before)
        end_index = center_chunk_index + after

        documents = await RetrieveChunkDocument.find({
            "user_id": user_id,
            "resource_kind": resource_kind.value,
            "resource_id": resource_id,
            "index_version": index_version,
            "chunk_index": {
                "$gte": start_index,
                "$lte": end_index
            }
        }).sort("chunk_index").to_list()

        return [doc.to_domain() for doc in documents if doc.chunk_index != center_chunk_index]

    async def get_search_chunk_records(
            self,
            lookups: List[RagSearchChunkLookup],
    ) -> Dict[str, RagSearchChunkRecord]:
        return await self._batch_lookup_core(
            lookups=lookups,
            document_cls=SearchChunkDocument,
            transform_fn=lambda doc: RagSearchChunkRecord(
                chunk=doc.to_domain(),
                retrieval_context=doc.retrieval_context,
                semantic_indexing_text=doc.semantic_indexing_text,
                keyword_text=doc.keyword_text,
            )
        )

    async def get_retrieve_chunks(
            self,
            lookups: List[RagRetrieveChunkLookup],
    ) -> Dict[str, RetrieveChunk]:
        return await self._batch_lookup_core(
            lookups=lookups,
            document_cls=RetrieveChunkDocument,
            transform_fn=lambda doc: doc.to_domain()
        )

    async def get_neighbor_retrieve_chunks_batch(
            self,
            lookups: List[RagNeighborChunkLookup],
    ) -> Dict[str, List[RetrieveChunk]]:
        if not lookups:
            return {}

        grouped_lookups = defaultdict(list)
        for l in lookups:
            grouped_lookups[l.user_id, l.resource_kind, l.resource_id, l.index_version].append(l)

        async def fetch_neighbor_scope(scope, scoped_lookups):
            u_id, r_kind, r_id, idx_ver = scope

            def get_range(item):
                return max(0, item.center_chunk_index - item.before), item.center_chunk_index + item.after

            ranges = [get_range(l) for l in scoped_lookups]
            merged_ranges = _merge_intervals(ranges)
            if not merged_ranges:
                return {l.lookup_id: [] for l in scoped_lookups}

            documents = await RetrieveChunkDocument.find({
                "user_id": u_id,
                "resource_kind": r_kind.value,
                "resource_id": r_id,
                "index_version": idx_ver,
                "$or": [
                    {"chunk_index": {"$gte": s, "$lte": e}}
                    for s, e in merged_ranges
                ],
            }).sort("chunk_index").to_list()

            scoped_res = defaultdict(list)
            for doc in documents:
                chunk_idx = doc.chunk_index
                domain_doc = None

                for l in scoped_lookups:
                    start, end = get_range(l)

                    if not (start <= chunk_idx <= end) or chunk_idx == l.center_chunk_index:
                        continue

                    if domain_doc is None:
                        domain_doc = doc.to_domain()
                    scoped_res[l.lookup_id].append(domain_doc)

            return scoped_res

        scoped_results = await asyncio.gather(
            *(fetch_neighbor_scope(k, v)
              for k, v in grouped_lookups.items())
        )

        return {
            l_id: chunk_list
            for scoped_result in scoped_results
            for l_id, chunk_list in scoped_result.items()
        }

    async def list_index_versions(
            self,
            user_id: str,
            resource_kind: ResourceKind,
            resource_id: str,
    ) -> List[str]:
        retrieve_versions = await RetrieveChunkDocument.find({
            "user_id": user_id,
            "resource_kind": resource_kind.value,
            "resource_id": resource_id,
        }).distinct("index_version")

        search_versions = await SearchChunkDocument.find({
            "user_id": user_id,
            "resource_kind": resource_kind.value,
            "resource_id": resource_id,
        }).distinct("index_version")

        return list(set(retrieve_versions) | set(search_versions))

    async def delete_chunks_by_index_version(
            self,
            user_id: str,
            resource_kind: ResourceKind,
            resource_id: str,
            index_version: str,
    ) -> None:
        filter_query = {
            "user_id": user_id,
            "resource_kind": resource_kind,
            "resource_id": resource_id,
            "index_version": index_version,
        }

        async with await self._mongo_client.start_session() as session:
            async with session.start_transaction():
                await SearchChunkDocument.find(filter_query).delete_many(session=session)
                await RetrieveChunkDocument.find(filter_query).delete_many(session=session)

    async def _batch_lookup_core(self, lookups, document_cls, transform_fn):
        if not lookups:
            return {}

        grouped_chunk_ids = defaultdict(set)
        lookup_ids_by_doc_key = defaultdict(list)

        for l in lookups:
            grouped_chunk_ids[
                l.user_id,
                l.resource_kind,
                l.resource_id,
                l.index_version
            ].add(l.chunk_id)

            lookup_ids_by_doc_key[
                l.user_id,
                l.resource_kind,
                l.resource_id,
                l.index_version,
                l.chunk_id
            ].append(l.lookup_id)

        async def fetch_scope(scope, ids):
            u_id, r_kind, r_id, idx_ver = scope

            documents = await document_cls.find({
                "user_id": u_id,
                "resource_kind": r_kind.value,
                "resource_id": r_id,
                "index_version": idx_ver,
                "chunk_id": {"$in": list(ids)},
            }).to_list()

            scoped_res = {}
            for doc in documents:
                d_key = (
                    doc.user_id,
                    doc.resource_kind,
                    doc.resource_id,
                    doc.index_version,
                    doc.chunk_id
                )
                for l_id in lookup_ids_by_doc_key.get(d_key, []):
                    scoped_res[l_id] = transform_fn(doc)

            return scoped_res

        scoped_results = await asyncio.gather(
            *(fetch_scope(k, v) for k, v in grouped_chunk_ids.items())
        )

        return {
            l_id: record
            for scoped_result in scoped_results
            for l_id, record in scoped_result.items()
        }