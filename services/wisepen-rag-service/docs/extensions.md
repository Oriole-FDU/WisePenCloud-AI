# 扩展包开发指南

扩展负责业务校验、完整 Pipeline、本体、专用索引与工具；平台负责共享连接、唯一 publication/Kafka、来源授权、初始化、就绪与销毁 GC。先读[模型](models.md)、[核心抽象](abstractions.md)和[实际架构](architecture.md)，完整示例见 [Employment](employment.md)。

## 1. 登记与装配产物

[extensions/__init__.py](../src/rag/extensions/__init__.py) 的 `_BUILDERS` 显式登记容器构造器，`EXTENSIONS` 选择启用名称（默认 []）。构造器接收 platform，`extension()` 返回 [Extension](../src/rag/extensions/base.py)：

| 字段 | 平台消费方式 |
|---|---|
| pipeline | 注册为 pipeline_routes()[name]，按 Document.type 选择 |
| vector_collections | list[VectorCollection]，默认空；初始化声明索引，并将 storage 加入共享 GC |
| api | 可选 FastAPI 子应用，默认 None；挂载 `/rag/extensions/{name}` |

名称是摄入路由键，不等于本体 domain。只建文件夹不能启用扩展；未知名称启动失败，未知 type 在发布写前失败。配置变更后正常重启，不支持动态安装/卸载或自动发现。

宿主在共享客户端登记后构造扩展，保存 `app.state.extensions`，初始化全部存储、挂载 API，再启动后台任务。构造/初始化失败传播并统一清理，登记不是跨扩展事务。业务包不导入全局 container，不接触 Kafka。

## 2. 基础接入：文件入库

无专用索引和工具时仅声明 Pipeline：

```python
# example: basic-extension
from dependency_injector import containers, providers
from rag.extensions.base import Extension


class FilesContainer(containers.DeclarativeContainer):
    platform = providers.DependenciesContainer()
    extension = providers.Singleton(Extension, pipeline=platform.pipeline)
```

平台有两条共享默认链：pipeline_with_context()、pipeline_without_context()；pipeline() 根据 ENABLE_CONTEXTUALIZE 惰性二选一，未选中链不解析 instruction 客户端。扩展可显式选链，复用平台 Contextualizer；需要不同策略则自行构造组件。

默认链为 MarkdownChunker → 可选 Contextualizer → ChunkFactsIndexer → VectorIndexer。业务 metadata 与版本约束直接遵循[核心抽象](abstractions.md)，普通文件不要求本体。

## 3. 基础接入：追加图阶段

用包内本体文件或显式依赖构造完整链：

```python
# example: extension-composition
from dependency_injector import containers, providers
from rag.extensions.base import Extension
from rag.ingestion import IngestionPipeline


def build_ingestion(platform, ontology):
    default = platform.pipeline()
    return IngestionPipeline(
        chunker=default.chunker,
        transformers=[*default.transformers, platform.graph_extractor(ontology=ontology)],
        indexers=[*default.indexers, platform.graph_indexer(ontology=ontology)],
    )


class HistoryContainer(containers.DeclarativeContainer):
    platform = providers.DependenciesContainer()
    ontology = providers.Dependency()
    pipeline = providers.Singleton(build_ingestion, platform=platform, ontology=ontology)
    extension = providers.Singleton(Extension, pipeline=pipeline)
```

登记此构造器时补齐 ontology，或在容器中声明包内文件 Singleton。GraphExtractor 需要完整 INSTRUCTION_*；可显式覆盖 gleaning 等真实参数。

GraphIndexer 统一负责来源拓扑和两种来源向量。仅写向量可覆盖 writer=None；仅写拓扑可把 embedding_client/entity_writer/relationship_writer 全部设为 None。读取图用例需要 Neo4j，查询不绑定写入本体。

外层手工注册 Pipeline 的入口也存在：

```python
# example: pipeline-registration
platform.pipeline_routes()["history"] = history.pipeline()
```

此方式只登记 Pipeline，不接入额外存储或 API；完整扩展走 Extension/配置登记。通用 Container 不新增垂类分支。

## 4. 专用索引与 metadata 增量

额外集合要与平台三套集合和其他扩展集合隔离，重复集合名拒绝。以下声明仅作为组合函数，实际包中注入平台客户端：

```python
# example: dedicated-indexes
from rag.backends.qdrant import QdrantVectorStorage
from rag.storage.vector import IndexableField, IndexKind, VectorCollection


def contract_collection(client, dimensions, collection="employment_contract_vectors_v4"):
    storage = QdrantVectorStorage(
        client=client, collection=collection, dense_vector_size=dimensions,
        sparse_vector_name=None,
    )
    return VectorCollection(storage, [
        IndexableField("resource_id", IndexKind.KEYWORD),
        IndexableField("year", IndexKind.INTEGER),
        IndexableField("employee", IndexKind.KEYWORD),
    ])
```

VectorStorage.ensure_collection 要求必填 list[IndexableField]，空列表也初始化并核对集合；索引 SDK 翻译在 backend。无 DATETIME，时间需业务转整数。配置不匹配失败，不自动重建或删除既有字段索引。

专用 VectorIndexer 使用 metadata_factory 提供业务增量，身份合并规则见[抽象](abstractions.md)。字段出现在 payload 不等于必须创建字段索引：Employment 保留 content_revision 核验，但不声明其索引。

extra storage 登记后由平台初始化并共享 GC；业务 adapter 不管理客户端关闭。停用扩展前回收其数据，停用后平台不再知道未配置集合。新增其他后端需明确接入平台生命周期。

## 5. 工具与 API

业务工具保留参数约束，将员工/年份等翻译为 MetadataFilters，再调用绑定专用集合的通用 [VectorRetrieval](usecases-vector.md)。不重复实现 Retriever/prepare、授权回查或排序，不在授权后再次解析业务 DTO。写入时业务 DTO 可校验，读取直接返回通用 ChunkHit。

图工具只传通用参数与[允许的图筛选](usecases-graph.md)，返回 GraphHit/GraphEvidence，不二次加载转换。示例：

```python
# example: ontology-scoped-graph
from rag.core.schema.filters import MetadataFilter, MetadataFilters


async def follow_employment(traversal, node_ids, snapshot, direction=None):
    return await traversal.traverse(
        node_ids, snapshot=snapshot, direction=direction,
        relation_types=["in_department"],
        filters=MetadataFilters([MetadataFilter("ontology_name", "employment")]),
    )
```

本体条件允许共享命名空间的其他可见来源参与；限定具体资料时组合资源条件，不把业务年份塞进图过滤。每次一跳，提示不承诺当前关系存在。

Extension.api 是已注入用例的子应用，不创建/关闭客户端、不声明后台 lifespan。宿主同步 ready，关闭前撤销 ready。可信网关 Header 经 Common SecurityHeaderMiddleware 校验，再创建每请求 PermissionScope/ResourceSnapshot；公开 DTO 不允许声明身份。Employment 需要 FROM_SOURCE_SECRET，接口及错误码见[示例](employment.md)。

入库仍走唯一 publication；示例只开放读取 API。Document 已在 Transformer 前保存，Transformer 不借此回写 Document。完整必需链失败阻止 apply；独立维护的部分成功遵循[运维](operations.md)，不会自动重建专用业务集合。

## 6. provider 覆盖与替身验证

下面执行实际 DI 组合，不启动应用或连接服务：

```python
# example: extension-test
from types import SimpleNamespace
from dependency_injector import containers, providers
from rag.ingestion import IngestionPipeline
from rag.application.publication import DocumentPublication


class TestPlatform(containers.DeclarativeContainer):
    pipeline = providers.Object(IngestionPipeline(
        chunker=object(), transformers=[], indexers=[object(), object()],
    ))
    graph_extractor = providers.Factory(SimpleNamespace)
    graph_indexer = providers.Factory(SimpleNamespace)
    publication_store = providers.Object(object())
    content_storage = providers.Object(object())
    pipeline_routes = providers.Singleton(dict)
    publication = providers.Singleton(
        DocumentPublication, store=publication_store, reader=content_storage,
        writer=content_storage, pipeline=pipeline, pipelines=pipeline_routes,
    )


platform = TestPlatform()
history = HistoryContainer(platform=platform, ontology=object())
assert len(history.pipeline().transformers) == 1
assert history.extension().pipeline is history.pipeline()
assert history.pipeline().indexers[:2] == platform.pipeline().indexers
assert platform.pipeline().transformers == []
```

组合示例按本页顺序执行，替身对象只用于装配断言。真实运行注入符合端口合同的组件。集合、工具和 Pipeline 都可用标准 DI override；测试替换真实外部边界，不改变生产导入或生命周期。

示例执行和登记验证见 [test_extensions.py](../tests/test_extensions.py)，业务链与 HTTP 替身验证见 [test_employment_extension.py](../tests/application/test_employment_extension.py)。真实后端只在显式 RAG_TEST 配置下使用隔离 fixture，见[运行说明](deployment.md)。
