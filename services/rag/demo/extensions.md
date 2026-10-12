# 垂类扩展包开发与注入

平台提供默认组件和连接生命周期；垂类在独立包中定义本体、metadata、摄入流程、业务用例和工具适配。平台以 Document.type 路由摄入 Pipeline，Kafka 消费始终由平台负责；不提供插件发现、全局 Registry 或在线运维接口。

## 目录与职责

例如独立 Python 包 `history_rag`：

```text
history_rag/
  ontologies/history.yaml       # 包自己维护，Ontology.from_file 显式加载
  metadata.py                   # 历史资料的业务字段与输入映射
  ingestion.py                  # 特定 Transformer / Indexer
  container.py                  # 组合平台依赖与垂类组件
  application.py                # 特定检索、年代追溯等用例
  tools.py                      # 输入/输出适配，transport 以后接入
  main.py                       # 外层组合入口
  tests/
```

业务模块通过构造函数接收 ContentReader/Writer、GraphReader/Writer、VectorReader/Writer、SnapshotReader 和模型客户端，不导入 `rag.container.container`。外层组合入口负责连接子容器与平台；垂类不建立数据库连接，不写数据库删除代码。

`rag.container.Container` 声明 provider，并保留默认 `container` 实例；`rag.main` 从 `app_settings.settings` 取得 Nacos 配置并注入实例。生命周期在 `main.lifespan`，与 Chat/Sandbox 的入口习惯相同。需要另一套装配时可建立 `Container(settings=...)`，也可覆盖具体 provider；无需修改通用容器。

## 基于真实接口的子容器

下面代码可放入垂类的 `container.py`。它扩展摄入流程，不修改平台默认 Pipeline。示例中的垂类明确要求生成图事实并完成图投影；这条注册链的所有阶段都属于发布必需阶段。平台默认链仍只保存普通事实/向量。

```python
# example: extension-composition
from dependency_injector import containers, providers
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
    pipeline = providers.Singleton(
        build_ingestion, platform=platform, ontology=ontology,
    )

```

`GraphExtractor` 和 `GraphIndexer` 工厂接受实际 Ontology 参数及原组件参数覆盖，例如 `platform.graph_extractor(ontology=ontology, gleaning=True)`；仅拓扑投影可以通过 `platform.graph_indexer(ontology=ontology, embedding_client=None, entity_writer=None, relationship_writer=None)` 明确关闭向量三件套。只投影向量时传 `writer=None`。不要通过业务开关从 GC 注销对应存储。

发布用例沿用 `stage → Document 保存 → Pipeline → 权威事实回查 → apply`，事实保存与普通向量确认必须保留。注册链内抽取或 GraphIndexer 失败都会阻止这个垂类的 apply；已完成写入保留，不做跨库补偿。平台默认发布不生成图事实，仍独立工作。若业务要求普通发布不等待图抽取，应使用默认 publication，另行声明后续图构建流程，不能把希望降级的图阶段放进这条注册必需链后又吞掉错误。本片不添加发布后阶段调度。

图附加阶段只读取已经完整保存的 Chunk，在写前复核完整 applied 状态，按同资源串行维护。失败保留 Mongo 事实与已完成投影，不撤销普通 apply、不伪装图阶段完成，也不补偿其他库。`ALREADY_APPLIED` 不证明图投影成功，重跑须再次执行投影。离线恢复可复用现有 `repair_graph --projection-only --with-graph --with-graph-vectors`；它校验当前 applied、写前状态并重建全部传入来源，具体 CLI 见 [graph.md](graph.md)。自建在线图构建队列、重试和修复调度留给后续生命周期切片。

Transformer 必须保留正文、身份、上下文及已有 metadata；ContentWriter 保存完整替换对象，不能用仅含图字段的新 Chunk 覆盖旧对象。本体约束写入，图读取不绑定单一本体。

## 外层入口与启动

下面放入垂类的 `main.py`，运行 `uvicorn history_rag.main:app`。Nacos 的默认配置与引导环境沿用 RAG；该示例要求完整 `INSTRUCTION_*`，需要图应用时还须启用 Neo4j。

```python
# example: extension-entry
from rag.main import app
from rag.core.schema import Ontology
from history_rag.container import HistoryContainer

platform = app.state.container
history = HistoryContainer(
    platform=platform,
    ontology=Ontology.from_file("history_rag/ontologies/history.yaml"),
)
platform.pipeline_routes()["history"] = history.pipeline()
app.state.history = history
# 自己的工具适配可接收平台 publication/检索用例或自己构造的用例。
# transport/路由由扩展入口声明；这里不引入虚构的宿主注册 API。
```

`app.state.container` 是实际生命周期管理的实例。扩展入口可用新 Container 替换它，再以相同的 `lifespan` 初始化和清理；`main` 的默认 Nacos 配置加载保留项目习惯。业务用例不读取 `app.state`，仍通过构造函数注入。

平台唯一 publication 接收同一份 pipeline_routes 字典；Kafka 消费者无需修改，垂类也不覆盖整个 publisher。最小注册代码：

```python
# example: pipeline-registration
platform.pipeline_routes()["history"] = history.pipeline()
```

每个 Container 实例各有一份映射。构造 publication 前后赋值都可见，正常开发在启动消费者前完成注册；普通字典允许显式替换同名键，不增加冻结、重复拒绝或在线热更新协议。类型按原值精确匹配，`history` 与 `History` 是不同键。

Document.type 为可选开放字符串：None/旧事件走默认链，显式未知 type 在 stage 或内容写入前抛出 ValueError，按已有 Kafka 永久错误合同进入 DLQ。空字符串不会被当作默认类别。一个 type 表达摄入类别，与资源物理类型、Ontology domain 分开；不同类别可以组装不同本体或共享本体。

未来上游可以携带以下字段。本片只扩展 RAG 接收接口，不修改上游生产者：

```json
{
  "resourceId": "document-001",
  "version": 2,
  "content": "# 文献\n\n完整原文",
  "type": "history",
  "metadata": {"year": 1850, "catalog": {"tags": ["历史"]}}
}
```

事件 type/metadata 进入 Document，默认 MarkdownChunker 将完整业务 metadata 深复制到各 Chunk，互不共享嵌套可变值；type 只在 Document 保存，不另造 Chunk.type 或影子 metadata 字段。

ContentRevision 仍由 resource_id、version、完整正文摘要决定，不包含 type、metadata 或 Pipeline 配置。**影响摄入的 type/metadata 变化必须提高上游 version**；相同已发布 revision 仍跳过，不更新旧事实。注册或替换路由也不自动重建历史文档，重建继续走显式维护流程。

## 特定用例与 metadata

仅改工具输入输出可以直接复用 `HybridRetrieval`、`DocumentReading`、`GraphRetrieval`、`GraphTraversal`。需要年代准备、文献间特殊排序或其他业务阶段时，组合 Retriever、Common `RetrievalPipeline`、SnapshotReader；不能把未经授权的投影文本送入模型或结果。请求入口从可信身份建立 ResourceSnapshot，相关调用共享同一个快照；不要把快照保存在 Singleton。

例如 `year=1850` 必须先成为当前 Document/Chunk metadata 的权威字段。默认 VectorIndexer **只投影 resource_id/content_revision**，不会自动索引任意业务 metadata；在自己的向量 Indexer 中显式复制 `year`，在平台初始化为该字段增加正确的 payload index，再在准备阶段核对当前 Chunk.metadata["year"]，才能把年份用于业务筛选。图来源 Indexer 同样不会自动复制业务字段；当前 GraphRetrieval/GraphTraversal filters 仅接受 resource_id/content_revision，不能直接传年代条件。年代追溯用例应自行定义投影与候选准备边界，并保留原有身份、revision、授权和事实摘要回查。

向量/拓扑是候选投影，Mongo 才是正文和来源事实权威。隐藏、destroyed、旧 revision 与过期文本摘要不能进入业务结果。跨本体 traversal 使用逻辑种子 ID 与写入时固化的类别关系提示；模型选到没有可见事实支持的关系，返回空即可。

## 依赖替身测试

示例直接使用前面定义的 `HistoryContainer`；不需要连接 Nacos、模型、Mongo 或图库。完整启动测试则替换配置拉取边界及底层客户端，不通过关闭 GC/Rerank 来绕过生命周期。

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
ontology = object()
history = HistoryContainer(platform=platform, ontology=ontology)
assert len(history.pipeline().transformers) == 1
assert history.pipeline().indexers[:2] == platform.pipeline().indexers
assert platform.pipeline().transformers == []
platform.pipeline_routes()["history"] = history.pipeline()
assert platform.pipeline_routes()["history"] is history.pipeline()
assert history.pipeline().transformers[0].ontology is ontology
assert history.pipeline().indexers[-1].ontology is ontology
```

上例仅验证装配，算法使用真实 Ontology + 假 Instruction/Embedding/Writer，授权用真实 SnapshotReader + 假 publication/ACL/content 端口。源码中的 `test_container.py`、`test_lifespan.py` 和 `test_gc.py` 提供平台装配、启动及清理替身测试。

## 清理合同

所有来源仍保存平台 resource_id/content_revision/chunk_id，GC 使用明确 resource_id 删除三个向量集合、已配置 Neo4j 来源贡献、本地 ACL 和全部内容 revision，保留永久销毁标记。垂类使用共享存储无需编写删除代码；新增底层集合或存储需由平台接入生命周期和 GC，不能在垂类中藏一个无人回收的数据库。

GC 反复扫描同一永久标记，允许重复执行和多实例幂等清理；迟到写入由后续轮次回收。停用后端前应先完成数据回收，不能先从平台配置撤掉后端再期待 GC 清理它。历史 revision 的非销毁回收、在线重构建、业务效果基线、mix 和 MCP transport 均留后。
