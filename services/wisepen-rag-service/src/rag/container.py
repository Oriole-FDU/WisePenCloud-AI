"""平台默认装配；显式配置与 provider 覆盖支持外层垂类组合，不加载 Nacos。"""

from collections.abc import Callable
from operator import attrgetter, getitem
from typing import TYPE_CHECKING

from dependency_injector import containers, providers
from neo4j import AsyncDriver, AsyncGraphDatabase
from openai import AsyncOpenAI
from pymongo import AsyncMongoClient
from qdrant_client import AsyncQdrantClient

from common.utils.ai_clients import EmbeddingClient, InstructionClient, RerankClient
from common.utils.retrieval import ModelReranker
from rag.application.acl import AclEventConsumer, AclSynchronizer
from rag.application.common.snapshot import ResourceSnapshotReader
from rag.application.gc import ResourceGarbageCollector
from rag.application.graph import GraphRetrieval, GraphTraversal
from rag.application.publication import DocumentPublication, PublicationEventConsumer
from rag.application.reading import DocumentReading
from rag.application.retrieval import VectorRetrieval
from rag.core.messaging import KafkaConsumerClient
from rag.core.persistence.mongo.acl import MongoResourceAclRepository
from rag.core.persistence.mongo.content import MongoContentStorage
from rag.core.persistence.mongo.publication import MongoPublicationStore
from rag.core.persistence.neo4j import Neo4jGraphStorage
from rag.core.persistence.qdrant import QdrantVectorStorage
from rag.domain.models import Ontology
from rag.domain.storage.vector import IndexKind, IndexableField, VectorCollection
from rag.ingestion import IngestionPipeline
from rag.ingestion.chunkers.markdown import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer, GraphIndexer, VectorIndexer
from rag.ingestion.transformers import Contextualizer, GraphExtractor

if TYPE_CHECKING:
    from rag.core.config.app_settings import AppSettings


def _instruction_client(
    settings: "AppSettings", openai_provider: Callable[[], AsyncOpenAI]
) -> InstructionClient:
    """构造指令模型客户端；未配置模型时报错，避免半装配。"""
    if settings.INSTRUCTION_MODEL is None:
        raise RuntimeError("configure INSTRUCTION_* or inject an instruction client")
    return InstructionClient(openai_provider(), model=settings.INSTRUCTION_MODEL)


def _graph_storage(
    settings: "AppSettings", driver_provider: Callable[[], AsyncDriver]
) -> Neo4jGraphStorage | None:
    """图存储未启用时返回 None，让上层显式选择走图或不走图。"""
    if not settings.ENABLE_GRAPH_STORAGE:
        return None
    return Neo4jGraphStorage(
        driver=driver_provider(),
        database=settings.NEO4J_DATABASE,
        graph_name=settings.NEO4J_GRAPH_NAME,
    )


def _require_graph(storage: Neo4jGraphStorage | None) -> Neo4jGraphStorage:
    """需要图的用例入口；未启用图存储时直接报错。"""
    if storage is None:
        raise RuntimeError("graph application requires ENABLE_GRAPH_STORAGE")
    return storage


def _contextualize_branch(enabled: bool) -> str:
    """Selector 只接受字符串键；把开关映射到带/不带 Contextualizer 的默认链。"""
    return "with_context" if enabled else "without_context"


def _garbage_collector(
    settings, store, content, acls, vectors, graph
) -> ResourceGarbageCollector:
    # 清理目标来自平台存储，不取决于本次文档或垂类是否启用图算法。
    return ResourceGarbageCollector(
        store=store,
        content_writer=content,
        resource_acls=acls,
        vector_writers=vectors,
        graph_writers=[graph] if graph is not None else [],
        batch_size=settings.GC_BATCH_SIZE,
        interval_seconds=settings.GC_INTERVAL_SECONDS,
    )


class Container(containers.DeclarativeContainer):
    """共享平台资源与通用用例；实例化时传 settings，不保存请求 scope/snapshot。"""

    settings = providers.Dependency()

    # ------------------------------------------------------------------
    # MongoDB：四个自有集合由实体声明，外部资源库保持独立。
    # ------------------------------------------------------------------
    mongo_client = providers.Singleton(AsyncMongoClient, settings.provided.MONGODB_URL)
    database = providers.Callable(
        getitem, mongo_client, settings.provided.MONGODB_DB_NAME
    )
    resource_database = providers.Callable(
        getitem, mongo_client, settings.provided.resource_mongodb_db_name
    )
    resource_items_collection = providers.Callable(
        getitem, resource_database, "wisepen_resource_items"
    )
    acl_repository = providers.Singleton(MongoResourceAclRepository)
    content_storage = providers.Singleton(MongoContentStorage)
    publication_store = providers.Singleton(MongoPublicationStore)

    # ------------------------------------------------------------------
    # Qdrant：客户端与三套向量集合
    # ------------------------------------------------------------------
    qdrant_client = providers.Singleton(
        AsyncQdrantClient,
        url=settings.provided.QDRANT_URL,
        api_key=settings.provided.QDRANT_API_KEY,
        cloud_inference=True,
        timeout=30.0,
    )
    vector_storage = providers.Singleton(
        QdrantVectorStorage,
        client=qdrant_client,
        collection=settings.provided.VECTOR_COLLECTION,
        dense_vector_size=settings.provided.EMBEDDING_DIMENSIONS,
    )
    entity_vector_storage = providers.Singleton(
        QdrantVectorStorage,
        client=qdrant_client,
        collection=settings.provided.GRAPH_ENTITY_VECTOR_COLLECTION,
        dense_vector_size=settings.provided.EMBEDDING_DIMENSIONS,
        sparse_vector_name=None,
    )
    relationship_vector_storage = providers.Singleton(
        QdrantVectorStorage,
        client=qdrant_client,
        collection=settings.provided.GRAPH_RELATION_VECTOR_COLLECTION,
        dense_vector_size=settings.provided.EMBEDDING_DIMENSIONS,
        sparse_vector_name=None,
    )
    vector_collections = providers.Singleton(
        list,
        providers.List(
            providers.Singleton(
                VectorCollection,
                storage=vector_storage,
                metadata_index_fields=[
                    IndexableField("resource_id", IndexKind.KEYWORD)
                ],
            ),
            providers.Singleton(
                VectorCollection,
                storage=entity_vector_storage,
                metadata_index_fields=[
                    IndexableField(name, IndexKind.KEYWORD)
                    for name in ("resource_id", "chunk_id", "ontology_name")
                ],
            ),
            providers.Singleton(
                VectorCollection,
                storage=relationship_vector_storage,
                metadata_index_fields=[
                    IndexableField(name, IndexKind.KEYWORD)
                    for name in ("resource_id", "chunk_id", "ontology_name")
                ],
            ),
        ),
    )
    # GC 保留共享 writer 列表；登记扩展时追加 storage，不让清理用例解释索引配置。
    vector_storages = providers.Singleton(
        lambda collections: [item.storage for item in collections],
        vector_collections,
    )

    # ------------------------------------------------------------------
    # Neo4j：驱动、存储与需要图存储的读取入口
    # ------------------------------------------------------------------
    neo4j_driver = providers.Singleton(
        AsyncGraphDatabase.driver,
        settings.provided.NEO4J_URI,
        auth=providers.Callable(
            lambda user, password: (user, password),
            settings.provided.NEO4J_USERNAME,
            settings.provided.NEO4J_PASSWORD,
        ),
    )
    graph_storage = providers.Singleton(
        _graph_storage, settings=settings, driver_provider=neo4j_driver.provider
    )
    graph_reader = providers.Callable(_require_graph, graph_storage)

    # ------------------------------------------------------------------
    # AI 客户端：embedding / instruction / rerank
    # ------------------------------------------------------------------
    embedding_openai = providers.Singleton(
        AsyncOpenAI,
        api_key=settings.provided.EMBEDDING_API_KEY,
        base_url=settings.provided.EMBEDDING_BASE_URL,
    )
    embedding_client = providers.Singleton(
        EmbeddingClient,
        embedding_openai,
        model=settings.provided.EMBEDDING_MODEL,
        dimensions=settings.provided.EMBEDDING_DIMENSIONS,
    )
    instruction_openai = providers.Singleton(
        AsyncOpenAI,
        api_key=settings.provided.INSTRUCTION_API_KEY,
        base_url=settings.provided.INSTRUCTION_BASE_URL,
    )
    instruction_client = providers.Singleton(
        _instruction_client,
        settings=settings,
        openai_provider=instruction_openai.provider,
    )
    rerank_client = providers.Singleton(
        RerankClient,
        api_key=settings.provided.QWEN_API_KEY,
        model=settings.provided.QWEN_RERANKER_MODEL,
    )
    # 普通与图检索都以完整 Chunk 正文精排。
    chunk_reranker = providers.Singleton(
        ModelReranker, client=rerank_client, text_getter=attrgetter("chunk.raw_text")
    )

    # ------------------------------------------------------------------
    # 摄取流水线：chunker / transformers / indexers
    # ------------------------------------------------------------------
    chunker = providers.Singleton(MarkdownChunker)
    contextualizer = providers.Singleton(Contextualizer, client=instruction_client)
    facts_indexer = providers.Singleton(ChunkFactsIndexer, writer=content_storage)
    vector_indexer = providers.Singleton(
        VectorIndexer, embedding_client=embedding_client, writer=vector_storage
    )
    # 两条默认链：只有 with_context 引用 contextualizer，未选中时不会被求值。
    pipeline_without_context = providers.Singleton(
        IngestionPipeline,
        chunker=chunker,
        transformers=[],
        indexers=providers.List(facts_indexer, vector_indexer),
    )
    pipeline_with_context = providers.Singleton(
        IngestionPipeline,
        chunker=chunker,
        transformers=providers.List(contextualizer),
        indexers=providers.List(facts_indexer, vector_indexer),
    )
    # 平台按配置二选一；Selector 只解析选中的分支，另一条的 instruction_client 不会被解析。
    pipeline = providers.Selector(
        providers.Callable(
            _contextualize_branch, settings.provided.ENABLE_CONTEXTUALIZE
        ),
        with_context=pipeline_with_context,
        without_context=pipeline_without_context,
    )
    # 每个容器实例独立持有路由；垂类在启动消费前注册，不接触 Kafka。
    pipeline_routes = providers.Singleton(dict)

    # 本体是实际工厂调用参数，没有通用默认本体或全局注册表。
    graph_extractor = providers.Factory(
        GraphExtractor,
        ontology=providers.Dependency(instance_of=Ontology),
        client=instruction_client,
    )
    graph_indexer = providers.Factory(
        GraphIndexer,
        ontology=providers.Dependency(instance_of=Ontology),
        writer=graph_storage,
        embedding_client=embedding_client,
        entity_writer=entity_vector_storage,
        relationship_writer=relationship_vector_storage,
    )

    # ------------------------------------------------------------------
    # 应用用例：发布、检索、读取、图遍历、GC
    # ------------------------------------------------------------------
    snapshot_reader = providers.Singleton(
        ResourceSnapshotReader,
        store=publication_store,
        resource_acls=acl_repository,
        reader=content_storage,
    )
    publication = providers.Singleton(
        DocumentPublication,
        store=publication_store,
        reader=content_storage,
        writer=content_storage,
        pipeline=pipeline,
        pipelines=pipeline_routes,
    )
    vector_retrieval = providers.Singleton(
        VectorRetrieval,
        snapshot_reader=snapshot_reader,
        vector_reader=vector_storage,
        graph_reader=graph_storage,
        embedding_client=embedding_client,
        reranker=chunk_reranker,
    )
    document_reading = providers.Singleton(DocumentReading, reader=snapshot_reader)
    graph_retrieval = providers.Singleton(
        GraphRetrieval,
        snapshot_reader=snapshot_reader,
        graph_reader=graph_reader,
        embedding_client=embedding_client,
        entity_reader=entity_vector_storage,
        relationship_reader=relationship_vector_storage,
        reranker=chunk_reranker,
    )
    graph_traversal = providers.Singleton(
        GraphTraversal, snapshot_reader=snapshot_reader, graph_reader=graph_reader
    )
    garbage_collector = providers.Singleton(
        _garbage_collector,
        settings=settings,
        store=publication_store,
        content=content_storage,
        acls=acl_repository,
        vectors=vector_storages,
        graph=graph_storage,
    )

    # ------------------------------------------------------------------
    # Kafka 消费者：ACL 同步与发布事件
    # ------------------------------------------------------------------
    acl_message_consumer = providers.Factory(
        KafkaConsumerClient,
        bootstrap_servers=settings.provided.KAFKA_BOOTSTRAP_SERVERS,
        topic=settings.provided.KAFKA_RESOURCE_ACL_RECALC_TOPIC,
        group_id=settings.provided.KAFKA_RAG_ACL_RECALC_GROUP_ID,
        max_poll_interval_ms=settings.provided.KAFKA_MAX_POLL_INTERVAL_MS,
    )
    publication_content_consumer = providers.Factory(
        KafkaConsumerClient,
        bootstrap_servers=settings.provided.KAFKA_BOOTSTRAP_SERVERS,
        topic=settings.provided.KAFKA_DOCUMENT_READY_TOPIC,
        group_id=settings.provided.KAFKA_RAG_PUBLICATION_GROUP_ID,
        max_poll_interval_ms=settings.provided.KAFKA_MAX_POLL_INTERVAL_MS,
    )
    publication_destroy_consumer = providers.Factory(
        KafkaConsumerClient,
        bootstrap_servers=settings.provided.KAFKA_BOOTSTRAP_SERVERS,
        topic=settings.provided.KAFKA_RESOURCE_PHYSICAL_DESTROY_TOPIC,
        group_id=settings.provided.KAFKA_RAG_PUBLICATION_GROUP_ID,
        max_poll_interval_ms=settings.provided.KAFKA_MAX_POLL_INTERVAL_MS,
    )
    acl_synchronizer = providers.Singleton(
        AclSynchronizer, collection=resource_items_collection, repository=acl_repository
    )
    acl_consumer = providers.Factory(
        AclEventConsumer,
        projection=acl_synchronizer,
        consumer=acl_message_consumer,
    )
    publication_consumer = providers.Factory(
        PublicationEventConsumer,
        publisher=publication,
        content_consumer=publication_content_consumer,
        destroy_consumer=publication_destroy_consumer,
    )


# 默认实例由 main 注入 settings；外层入口也可建立自己的 Container。
container = Container()
