"""雇佣合同组合入口：共享平台连接，声明业务 Pipeline、专用索引和 API。"""

from pathlib import Path

from dependency_injector import containers, providers

from rag.application.retrieval import VectorRetrieval
from rag.backends.qdrant import QdrantVectorStorage
from rag.core.schema import Ontology
from rag.extensions.base import Extension
from rag.ingestion import IngestionPipeline
from rag.ingestion.indexers import VectorIndexer
from rag.storage.vector import IndexableField, IndexKind, VectorCollection

from .api import build_api
from .metadata import EmploymentMetadataTransformer, employment_projection
from .tools import EmploymentTools


def build_pipeline(platform, ontology, contract_indexer) -> IngestionPipeline:
    # 垂类显式选择平台默认链：本链不取平台的 Contextualizer，防止额外模型调用；
    # 需要平台统一的上下文增强时改用 platform.pipeline_with_context()。
    default = platform.pipeline_without_context()
    # 业务字段先校验；完整事实保存后写普通、图及专用投影，任一失败都阻止 apply。
    return IngestionPipeline(
        chunker=default.chunker,
        transformers=[
            EmploymentMetadataTransformer(),
            *default.transformers,
            platform.graph_extractor(ontology=ontology),
        ],
        indexers=[
            *default.indexers,
            platform.graph_indexer(ontology=ontology),
            contract_indexer,
        ],
    )


def build_extension(platform, pipeline, collection, tools) -> Extension:
    # 安全配置在连接生命周期内检查，失败由平台关闭已经创建的客户端。
    secret = platform.settings().FROM_SOURCE_SECRET
    if secret is None or not secret.strip():
        raise ValueError("employment API requires FROM_SOURCE_SECRET")
    return Extension(
        pipeline=pipeline,
        vector_collections=[collection],
        api=build_api(tools, from_source_secret=secret),
    )


class EmploymentContainer(containers.DeclarativeContainer):
    """依赖注入保持惰性；集合名称可在外层覆盖，不增加通用业务配置分支。"""

    platform = providers.DependenciesContainer()
    collection = providers.Dependency(default="employment_contract_vectors_v4")
    ontology = providers.Singleton(
        Ontology.from_file, Path(__file__).with_name("ontology.yaml")
    )
    storage = providers.Singleton(
        QdrantVectorStorage,
        client=platform.qdrant_client,
        collection=collection,
        dense_vector_size=platform.settings.provided.EMBEDDING_DIMENSIONS,
        sparse_vector_name=None,
    )
    vector_collection = providers.Singleton(
        VectorCollection,
        storage=storage,
        metadata_index_fields=[
            IndexableField("resource_id", IndexKind.KEYWORD),
            IndexableField("year", IndexKind.INTEGER),
            IndexableField("employee", IndexKind.KEYWORD),
        ],
    )
    contract_indexer = providers.Singleton(
        VectorIndexer,
        embedding_client=platform.embedding_client,
        writer=storage,
        metadata_factory=employment_projection,
    )
    pipeline = providers.Singleton(
        build_pipeline,
        platform=platform,
        ontology=ontology,
        contract_indexer=contract_indexer,
    )
    contracts = providers.Singleton(
        VectorRetrieval,
        snapshot_reader=platform.snapshot_reader,
        vector_reader=storage,
        embedding_client=platform.embedding_client,
        reranker=platform.chunk_reranker,
    )
    tools = providers.Singleton(
        EmploymentTools,
        contracts=contracts,
        graph_retrieval=platform.graph_retrieval,
        graph_traversal=platform.graph_traversal,
        document_reading=platform.document_reading,
    )
    extension = providers.Singleton(
        build_extension,
        platform=platform,
        pipeline=pipeline,
        collection=vector_collection,
        tools=tools,
    )
