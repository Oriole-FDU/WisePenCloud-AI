# RAG 垂类扩展开发指南

扩展负责业务输入、Pipeline、本体、专用索引和用例；平台负责共享连接、唯一 publication/Kafka、来源授权、生命周期及销毁 GC。下面分开介绍**基础文件入库/图索引**和**专用索引/高级工具/API**，完整高级示例在 [employment/](employment/README.md)。

## 1. 接入合同：配置选择、Document.type 路由、平台启动

入口是 [__init__.py](__init__.py)。源码表 `_BUILDERS` 显式登记构造器；业务容器接收 `platform=...` 并提供 `extension()`。这个 provider 返回轻量 [Extension](base.py)：

| 字段 | 平台实际消费方式 |
| --- | --- |
| `pipeline` | 注册到同一份 `platform.pipeline_routes()[name]`，由 Document.type 选择 |
| `vector_collections` | 额外的 VectorCollection：storage + list[IndexableField]；启动通过 VectorStorage 协议初始化，storage 加入平台 GC |
| `api` | 可选、已经注入用例的 FastAPI 子应用，挂在 `/rag/extensions/{name}` |

```text
_BUILDERS 源码登记 → AppSettings.EXTENSIONS 配置选择
  → main lifespan 登记共享客户端
  → register_extensions 构造并保存装配产物
  → 默认及扩展存储初始化、API 挂载
  → GC 启动、Kafka 可选连接握手 → ready
```

Nacos 配置 `EXTENSIONS: [employment]` 启用实际示例，默认 `[]` 不接入垂类。名称同时是 Document.type 路由键，和本体 domain 含义不同。未知扩展名启动失败；未知 Document.type 在 stage/事实写入前失败。无 type 的旧事件继续走默认 Pipeline。

只登记名称或只新增文件夹不能启用扩展。修改配置后正常重启，不支持在线安装/卸载或插件自动发现。启动注册不是跨扩展事务，失败传播并由 lifespan 关闭所有已登记共享客户端。额外 collection 必须与平台三套集合及其他扩展集合隔离；重复集合拒绝，不覆盖。

平台保留实际装配产物在 `app.state.extensions`；API 闭包和 Pipeline 持有已注入组件，不从路由字典反向寻找业务容器。子应用不自行创建/关闭数据库客户端，也不声明后台生命周期；宿主负责就绪及停止。取消、初始化失败或消费者退出时先撤销就绪、取消并等待任务，再关闭共享连接一次。

## 2. 基础需求：文件入库与图索引

### 2.1 普通文件入库

默认链是 MarkdownChunker → [Contextualizer] → ChunkFactsIndexer → 普通 VectorIndexer。无专用索引或工具时，扩展只返回 `Extension(pipeline=...)`，其余两项保持默认值即可。平台声明两条默认链 `platform.pipeline_with_context()` 与 `platform.pipeline_without_context()`，`platform.pipeline()` 则由平台按 `ENABLE_CONTEXTUALIZE` 惰性二选一（未选中的链不会解析 instruction 客户端）。扩展要带平台的 Contextualizer 就用 `pipeline_with_context()`，不带就用 `pipeline_without_context()`；选中的链带的是平台统一持有的实例，不能替换它的构造方式，需要自定义行为时自行 `from rag.ingestion.transformers import Contextualizer` 构造。

```python
# example: basic-extension
from dependency_injector import containers, providers
from rag.extensions.base import Extension


class FilesContainer(containers.DeclarativeContainer):
    platform = providers.DependenciesContainer()
    extension = providers.Singleton(Extension, pipeline=platform.pipeline)
```

普通文件不要求本体。默认 settings、索引、查询及 GC 见 [platform.md](../../../demo/platform.md) 和 [platform-settings.yaml](../../../demo/platform-settings.yaml)。GC 和 rerank 始终运行；KAFKA_ENABLED=false 只关闭 broker 消费。

### 2.2 在完整必需链上追加图索引

下面的子容器声明自己的 Pipeline。Ontology 来自显式注入，实际包内 JSON/YAML 写法参考 [employment/container.py](employment/container.py) 和 [employment/ontology.yaml](employment/ontology.yaml)。它们取代旧 demo 示例，并进一步展示高级接入。

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

注册构造器时补齐必要依赖，或在子容器中把本体声明为包内文件 Singleton。不要在通用 Container 增加垂类分支。GraphExtractor 需要完整 INSTRUCTION 配置；工厂可覆盖 `gleaning=True` 等实际参数。GraphIndexer 同时负责来源拓扑和实体/关系来源向量；只写图向量可传 writer=None，只写拓扑可覆盖三个向量依赖为 None。读取图用例需要启用 Neo4j，读取侧不绑定 Ontology。

较低层的外层组合入口可以手工注册 Pipeline：

```python
# example: pipeline-registration
platform.pipeline_routes()["history"] = history.pipeline()
```

这只接入 Pipeline，**不会接入额外存储或 API**；完整扩展应走 manifest 和配置登记。业务用例通过构造函数接收平台依赖，不导入全局 container，不接触 Kafka。

### 2.3 metadata、发布与版本合同

上游事件透传可选 type/metadata；默认 MarkdownChunker 对每个 Chunk 深复制业务 metadata。业务 Transformer 可校验并规范化自己的 Chunk 字段，同时保留正文、身份和其他 metadata。Document 已在 Pipeline 前保存；Transformer 不借此改写已保存的 Document。

平台流程仍是身份核对 → 选择链 → stage → 保存 Document → 全部 Transformer → 事实、普通向量和其他必需 Indexer → 事实回查 → apply。任何必需阶段失败都阻止新版本发布，保留旧 applied 和已经完成的部分写入，无跨库补偿。业务字段校验失败可能已留下 staged Document，但不会写 Chunk/投影或 apply。

ContentRevision 不加入 type/metadata/本体/配置。**影响摄入的 type 或 metadata 变化须提高上游 version**；同一已发布 revision 仍跳过。更换本体或 Pipeline 不自动重建历史文档。图 repair 的 projection-only 可重建通用图投影，但不会自动重建业务专用集合；Employment 提供自己的显式维护方法，见示例 README。

## 3. 高级需求：专用索引与高级专有工具

### 3.1 Employment：业务事实先落库，再投影

完整示例以某公司内部合同为输入。上游 metadata 只提供 employee/start_date，原样保存到 Document，逐 Chunk 深拷贝；校验 Transformer 只读检查，不重新序列化或覆盖字段。employee/year 作为专用向量增量，year 由 start_date 派生。人员、部门及 in_department 关系仅从正文抽取，不把业务 metadata 塞进提示词。

阅读顺序：

| 文件 | 职责 |
| --- | --- |
| [metadata.py](employment/metadata.py) | 写入边界业务 DTO、只读校验 Transformer、员工字段校验和增量投影 |
| [container.py](employment/container.py) | 全链装配、专用集合及 integer/keyword 索引、工具注入 |
| [通用正文检索](../application/retrieval.py) | VectorRetrieval 三模式、授权准备、完整 metadata 和 Common Pipeline |
| [tools.py](employment/tools.py) | 映射业务参数，编排正文检索、图三模式、一跳 traversal 和章节读取 |
| [api.py](employment/api.py) | 可信网关身份 → 每请求 snapshot → 已注入工具 |
| [contracts.jsonl](employment/contracts.jsonl) | 同一员工先后两次入职不同部门、同一部门多员工的多个独立资源 |

专用集合 `employment_contract_vectors_v4` 使用平台相同 embedding 模型/维度，dense-only，每 Chunk 一条记录，record_id 沿用 chunk_id。普通集合及两个通用图集合仍然照常写入。专用 VectorIndexer 复用现有组件，通过 `metadata_factory=employment_projection` 提供业务增量，与通用 resource_id/content_revision 合并，不复制 embedding 校验实现。

payload 保存 resource_id/content_revision、整数 year 和校验后的 employee。factory 只返回 employee/year；身份字段由通用 VectorIndexer 取自 Chunk，业务增量不能覆盖它们。Document/Chunk 的原始业务值不被规范化或补写 year。业务 metadata 变更要求提高 version，不在每个工具加入同 revision 摘要保护。员工名称按校验值精确匹配，不处理大小写、内部空白或别名。默认 Indexer 不自动索引任意业务字段。

### 3.2 正确声明 Qdrant 的字段类型和清理目标

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

索引声明来自 storage/vector.py。IndexKind 仅包含 KEYWORD/INTEGER/FLOAT/BOOLEAN，不提供 DATETIME；需要时间索引时业务自行转换 Unix 时间戳并使用 INTEGER。字段是 metadata 内相对的平面名称，数据库 SDK 类型只在对应后端翻译；业务包不导入 Qdrant models。

content_revision 仍是必需 payload 定位字段，用于当前版本核验；本工具不按它过滤，因此只声明 resource_id/year/employee 的数据库字段索引。字段存在与创建索引分开决定，不为删除索引而删除授权所需身份。

VectorStorage 协议要求 collection、读写和 `ensure_collection(metadata_index_fields: list[IndexableField])`。字段列表必填，空列表也会初始化/核对集合。平台每次启动统一遍历 vector_collections，把声明传给协议方法，等待索引写入确认；不使用无参方法、字符串字段列表或构造器 metadata_indexes。旧向量配置不匹配直接失败，不悄悄重建。

把配置放进 `Extension.vector_collections` 即接入初始化。登记时将配置中的 storage 加入平台共享 writer 列表，ResourceGarbageCollector 不解释索引配置。collector 先构造后登记扩展仍能看到新增 storage；删除仍按明确 resource_id，覆盖所有 revision，永久 destroyed 标记保留。adapter 复用平台客户端，不自行管理连接。

取消/写入失败保留已完成投影，重跑相同 staged 版本可覆盖恢复。仅 upsert 当前传入 Chunk，旧 revision 或改变分块后的残留不在本片全面回收。停用扩展前需回收专用集合数据；停用后平台不再知道未配置存储，不能承诺继续清理它。新增其他底层后端仍需平台生命周期接入，不把数据库删除实现丢给业务包。

### 3.3 高级工具只映射业务参数，再调用通用正文用例

通用 `VectorRetrieval` 支持 dense/sparse/hybrid，默认 hybrid；单路使用 candidate_limit，hybrid 默认每路 30。Employment 的专用集合是 dense-only，因此子容器把同一个通用用例绑定到专用 storage，工具明确选择 dense：

```text
工具将 year_from/year_to/employee 翻译为 MetadataFilters
  → VectorRetrieval(mode=DENSE, filters=...)
  → VectorRetriever / VectorReader 履行过滤合同
  → 同一 SnapshotReader 核对授权、资源/revision、记录身份
  → 通用 Common rerank / diversity
  → ChunkHit（完整正文、定位、seed_entities、完整 metadata）
```

完整实现见 [EmploymentTools.search_contracts](employment/tools.py)。工具保留年份范围和员工名称等业务输入约束，不重新实现 Retriever/Pipeline/prepare，也不在授权后再次执行年份条件。写入时使用业务 DTO，通用读取不解析垂类 schema。

`ChunkHit.metadata` 直接来自当前已授权 Chunk，保留原有全部字段，不从向量 payload 拼装，也不设置字段白名单。调用方从 metadata.employee/start_date 获取员工和入职日期；正文 text 仍是 raw_text，不把上下文拼入正文。API 同样返回这个通用 dataclass。

filters 由 VectorReader 正确执行；业务 metadata 的影响摄入变更必须提高 version，新旧 Chunk 由请求快照按 revision 区分。通用正文检索不额外保证同 revision 被直接修改业务字段后的投影新鲜度，不加业务/文本摘要或另一套过滤解释器。隐藏、destroyed、旧 revision 和错误定位仍在通用准备阶段丢弃，无合法候选不补召回，空候选不调用重排，重排失败沿用原顺序降级。

GraphRetrieval/GraphTraversal 的 filters 支持 resource_id/content_revision/ontology_name 的字符串 EQ、字符串列表 IN 与 AND/OR/NOT。图向量和拓扑读取接收同一条件；当前 Mongo 回查从实体事实核对 ontology_name，关系的同来源两端都必须满足条件。不把年份范围加入图过滤器；图事实摘要、合法端点和来源核验保持。

### 3.4 用通用用例封装业务工具

EmploymentTools 注入通用 GraphRetrieval、GraphTraversal 和 DocumentReading；graph/search 支持 local/global/hybrid，follow 固定 in_department 并允许 outgoing/incoming/both/None。两个图工具都传入 ontology_name=employment 的 MetadataFilters，直接返回通用用例结果，不再读取 Chunk、校验 employment DTO 或转换证据组。

本体条件在向量候选和拓扑逻辑关系预算前由后端执行，其他本体不会占用名额；授权或过期过滤后仍不补召回。ontology_name 选择事实命名空间，不要求来源带有某个业务 metadata，也不区分共享同一本体的不同业务资料。需要进一步限定资源时组合 resource_id/content_revision 条件，不在工具里重写证据加载器。合法实体没有可见关系时仍返回实体证据，relationships 为空。

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

通用用例仍不依赖 Ontology 对象，不解析逻辑 ID；未指定 filters 时允许跨本体读取。组内关系保持原方向、同来源端点和独立描述，工具不扩张 global 关系组、不自动多跳，也不按来源数量重排。

ChunkHit.seed_entities 暴露逻辑实体 ID 及类别允许的 relations。调用方从合同命中选择员工种子，先 outgoing 找部门，再从部门 incoming 找其他雇员；每次显式一跳、各自重新授权。没有可见关系时 relationships 为空，不把本体允许的关系当作存在事实。

旧 Neo4j 来源没有 ontology_name 时不会匹配本体筛选。先使用 repair_graph 的 --projection-only --with-graph 按现有事实重建拓扑；不调用抽取、不保存 Chunk。没有新增 Mongo 字段或查询侧本体注册表。

## 4. 高级需求：把工具封装成 API 并挂载

Extension.api 是子 FastAPI 应用；平台把它挂在 `/rag/extensions/{name}`，无需改通用 api/router，也无需让垂类接触消费者。Employment 包完整实现五个只读工具接口，路径、curl 和启用配置见 [README](employment/README.md)。

复用 Common SecurityHeaderMiddleware：`FROM_SOURCE_SECRET` 校验可信网关 X-From-Source，再从 X-User-Id/群组角色建立 PermissionScope 和请求级 ResourceSnapshot。每个请求独立快照，不存在 Singleton 用户状态；请求正文禁止额外 user_id/ACL 字段。直接向可访问服务发送伪造身份不应通过网关密钥校验。部署须保持密钥秘密并限制后端访问，这是现有可信 Header 合同。

root 健康接口继续公开；子 API 与宿主同步 ready。缺少网关令牌 404，未就绪 503，缺少可信用户 401，隐藏合同目录 404。参数在 HTTP 边界用 DTO 校验；存储、embedding 和权威事实解析失败继续传播，不用一个 ValueError handler 把数据损坏包装成输入错误。子应用不启动或关闭客户端。

入库继续使用平台 Kafka 的 DocumentReadyEvent/publication，本示例不开放无写权限约束的 publish HTTP 接口。工具 API 是读取 transport，不能凭请求正文赋予所有者/ACL。

## 5. 外层覆盖、替身验证和维护

下例使用实际 DI provider 覆盖，不加载默认 Nacos 配置或启动消费者：

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

完整雇佣发布、专用召回、三模式图检索、API、共享 GC 和故障恢复见 [test_employment_extension.py](../../../tests/application/test_employment_extension.py)；登记/文档代码测试见 [test_extensions.py](../../../tests/test_extensions.py)。真实后端测试只在显式 RAG_TEST 配置下使用隔离 fixture，本示例不会启动业务消费者或自动导入样例数据。

扩展集合可通过 `EmploymentContainer(..., collection="...")` 覆盖；工具/存储/Pipeline provider 都可使用标准 DI override。新增扩展修改源码登记和外层配置，不修改默认 Pipeline，不预建插件 Runtime、在线 Registry、MCP transport 或发布后调度。
