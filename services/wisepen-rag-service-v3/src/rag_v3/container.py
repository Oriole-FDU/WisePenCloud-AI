"""P0/P1 外部依赖装配：Mongo 客户端、仓储和 application 用例。"""

from common.utils.ranking import RankingPipeline
from common.utils.ranking.rank_gates import (
    HighLowRelevanceGate,
    HighLowRelevanceGateConfig,
)
from common.utils.ranking.rerankers import (
    ZeroEntropyReranker,
    ZeroEntropyRerankerConfig,
)
from dependency_injector import containers, providers
from openai import AsyncOpenAI
from pymongo import AsyncMongoClient
from qdrant_client import AsyncQdrantClient
from zeroentropy import AsyncZeroEntropy

from rag_v3.application.document import DocumentIndexBuilder, DocumentPreparer
from rag_v3.application.publication import AclSynchronizer, DocumentPublication
from rag_v3.application.retrieval import HybridRetriever
from rag_v3.application.snapshot import ActiveDocumentSnapshotLoader
from rag_v3.core.config.app_settings import settings
from rag_v3.core.persistence.mongo import (
    MongoAuthoritativeAclReader,
    MongoDocChunkRepository,
    MongoDocumentRepository,
    MongoResourceAclRepository,
    MongoResourceIndexStateRepository,
)
from rag_v3.core.persistence.qdrant import QdrantDocumentVectorRepository


def _resource_items_collection(client: AsyncMongoClient):
    return client[settings.resource_mongodb_db_name]["wispen_resource_items"]


def _build_ranking_pipeline(
    zero_entropy_client: AsyncZeroEntropy,
) -> RankingPipeline:
    """V3 只保留精排和相关性门控；两路 Qdrant 结果不在此处融合。"""
    return RankingPipeline(
        reranker=ZeroEntropyReranker(
            client=zero_entropy_client,
            config=ZeroEntropyRerankerConfig(model=settings.RERANKER_MODEL),
        ),
        gate=HighLowRelevanceGate(
            HighLowRelevanceGateConfig(
                low_watermark=settings.RAG_RERANK_RELEVANCE_LOW_WATERMARK,
                high_watermark=settings.RAG_RERANK_RELEVANCE_HIGH_WATERMARK,
                uncertain_limit=settings.RAG_RERANK_UNCERTAIN_LIMIT,
            )
        ),
    )


class Container(containers.DeclarativeContainer):
    """集中管理当前已落地的 Mongo 生命周期和 application 用例。"""

    mongo_client = providers.Singleton(AsyncMongoClient, settings.MONGODB_URL)
    resource_items_collection = providers.Factory(
        _resource_items_collection,
        client=mongo_client,
    )

    documents = providers.Singleton(MongoDocumentRepository)
    doc_chunks = providers.Singleton(MongoDocChunkRepository)
    index_states = providers.Singleton(MongoResourceIndexStateRepository)
    resource_acls = providers.Singleton(MongoResourceAclRepository)
    authoritative_acls = providers.Singleton(
        MongoAuthoritativeAclReader,
        collection=resource_items_collection,
    )
    openai_client = providers.Singleton(
        AsyncOpenAI,
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
    )
    zero_entropy_client = providers.Singleton(
        AsyncZeroEntropy,
        api_key=settings.ZERO_ENTROPY_API_KEY,
    )
    hybrid_ranking_pipeline = providers.Singleton(
        _build_ranking_pipeline,
        zero_entropy_client=zero_entropy_client,
    )
    qdrant_client = providers.Singleton(
        AsyncQdrantClient,
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        api_key=settings.QDRANT_PASSWORD or None,
    )
    document_vectors = providers.Singleton(
        QdrantDocumentVectorRepository,
        client=qdrant_client,
        collection_name=settings.QDRANT_DOCUMENT_CHUNK_COLLECTION_NAME,
        dense_vector_size=settings.EMBEDDING_DIMENSIONS,
        dense_vector_name=settings.QDRANT_DOCUMENT_DENSE_VECTOR_NAME,
        sparse_vector_name=settings.QDRANT_DOCUMENT_SPARSE_VECTOR_NAME,
    )

    document_publication = providers.Factory(
        DocumentPublication,
        documents=documents,
        doc_chunks=doc_chunks,
        document_vectors=document_vectors,
        index_states=index_states,
    )
    document_preparer = providers.Factory(
        DocumentPreparer,
        publication=document_publication,
        doc_chunks=doc_chunks,
    )
    document_index_builder = providers.Factory(
        DocumentIndexBuilder,
        documents=documents,
        doc_chunks=doc_chunks,
        resource_acls=resource_acls,
        index_states=index_states,
        publication=document_publication,
        document_vectors=document_vectors,
        openai_client=openai_client,
        query_model=settings.QUERY_MODEL,
        embedding_model=settings.EMBEDDING_MODEL,
        embedding_dimensions=settings.EMBEDDING_DIMENSIONS,
        max_concurrency=settings.DOCUMENT_ENHANCEMENT_MAX_CONCURRENCY,
    )
    acl_synchronizer = providers.Factory(
        AclSynchronizer,
        authoritative_reader=authoritative_acls,
        local_repository=resource_acls,
    )
    active_document_snapshots = providers.Factory(
        ActiveDocumentSnapshotLoader,
        documents=documents,
        index_states=index_states,
        resource_acls=resource_acls,
    )
    hybrid_retriever = providers.Factory(
        HybridRetriever,
        documents=documents,
        doc_chunks=doc_chunks,
        document_vectors=document_vectors,
        index_states=index_states,
        resource_acls=resource_acls,
        ranking_pipeline=hybrid_ranking_pipeline,
        openai_client=openai_client,
        embedding_model=settings.EMBEDDING_MODEL,
        embedding_dimensions=settings.EMBEDDING_DIMENSIONS,
    )


container = Container()
