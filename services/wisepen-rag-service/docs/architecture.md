# 实际架构

本图依据当前 [Container](../src/rag/container.py)、[main](../src/rag/main.py) 和 application 实现。术语见[模型](models.md)、[抽象](abstractions.md)；图中出现的存储是实际装配对象，Neo4j、Contextualizer、Kafka 和扩展按配置启用。

## 1. 模块职责与依赖

实线表示组合或消费方向；backends 实现 storage 和应用拥有的持久化端口。

```mermaid
flowchart TB
    Main[main / Container 组合入口] --> App[application 用例]
    Main --> Ext[extensions 垂类装配]
    Main --> Backend[backends 后端实现]
    Ext --> App
    Ext --> Ing[ingestion 分块 / 增强 / 索引]
    Ext --> Storage[storage 读写端口及投影对象]
    App --> Ing
    App --> Ret[retrievers 候选召回]
    App --> Storage
    Ing --> Storage
    Ret --> Storage
    Backend --> Storage
    Backend --> Ports[application ACL / publication 持久化端口]
    App --> Core[core.schema 中立事实]
    Ing --> Core
    Storage --> Core
    Backend --> Core
    App --> Common[Common Markdown / AI clients / RetrievalPipeline / 安全]
    Ing --> Common
    Ret --> Common
    Backend --> Mongo[(Mongo 内容 / 发布 / ACL)]
    Backend --> Qdrant[(Qdrant 普通 / 图来源 / 扩展向量)]
    Backend --> Neo[(Neo4j 可选来源拓扑)]
    Backend --> Kafka[(Kafka 可选消费 / 重试 / DLQ)]
```

core.schema 不依赖 ingestion、application 或具体数据库。Common 提供 Markdown 与检索算法；RAG 不复用旧 Repository/插件 runtime。配置加载属于正常入口，离线脚本自行组合现有组件。

## 2. 发布与投影

```mermaid
flowchart TD
    Event[DocumentReadyEvent / 可信直接调用] --> Pub[DocumentPublication 身份核对 / type 选择]
    Pub --> Stage{条件 stage}
    Stage -->|已发布 / 旧版本 / destroyed| Skip[返回跳过结果]
    Stage -->|需要构建| Doc[保存完整 Document]
    Doc --> Chunker[MarkdownChunker]
    Chunker --> Trans[固定 Transformers / 可选上下文 / 扩展图抽取]
    Trans --> Facts[ChunkFactsIndexer 最终事实]
    Facts --> Vec[普通 VectorIndexer dense + sparse]
    Vec --> Extra[扩展必需 Indexers / GraphIndexer / 专用向量]
    Extra --> Check[回查 Document 身份 / 本次预期 Chunk ID 集合]
    Check --> Apply[条件 apply]
    Doc --> Mongo[(Mongo)]
    Facts --> Mongo
    Vec --> Q[(Qdrant)]
    Extra --> Q
    Extra --> N[(Neo4j 可选)]
```

默认链没有图阶段。GraphIndexer 在一个组件内写拓扑和实体/关系来源向量；额外阶段顺序由扩展固定。全部必需阶段成功才 apply，失败保留旧 applied 与部分写入。详见[发布用例](usecases-publication.md)。

## 3. 查询与授权证据

```mermaid
flowchart TD
    Scope[可信 PermissionScope] --> Snapshot[每请求 ResourceSnapshot]
    Query[查询及本次预算 / filters] --> Usecase[VectorRetrieval / GraphRetrieval]
    Usecase --> Retriever[本次 from_storage 构造 Retriever]
    Retriever --> Q[(Qdrant 候选定位)]
    Q --> Prepare[Common Pipeline 异步准备]
    Snapshot --> Reader[ResourceSnapshotReader]
    Prepare --> Reader
    Reader --> Mongo[(Mongo 发布状态 / ACL / 当前内容事实)]
    Reader --> Verify[授权正文 / 版本和投影事实核验]
    Verify --> Graph[图种子确认及一跳扩展]
    Graph --> N[(Neo4j 来源拓扑)]
    N --> Reader
    Verify --> Rank[Common 重排 / MMR]
    Graph --> Sources[共享图上下文及来源预选]
    Sources --> Rank
    Rank --> Connections[最终 Chunk 实体的可见连接]
    Connections --> Reader
    Connections --> N
    Connections --> Result[ChunkHit / GraphRetrievalResult]
    Snapshot --> Reading[DocumentReading / GraphTraversal]
    Reading --> Reader
    Reading --> N
```

向量查询直接把授权 Chunk 送入排序；图查询先确认种子再组装授权证据。拓扑返回的新来源仍需 Mongo 授权回查，循环表示来源读取而非自动多跳。GraphTraversal 不调用模型；DocumentReading 只通过 Reader 读取完整 Document。详情见[向量](usecases-vector.md)、[图](usecases-graph.md)及[阅读](usecases-reading.md)。

## 4. 平台生命周期

```mermaid
flowchart TD
    Boot[bootstrap 环境 / Nacos AppSettings] --> Own[登记共享客户端关闭动作]
    Own --> Ext[register_extensions / 保存产物]
    Ext --> Init[Mongo 探活和索引 / 所有 VectorCollection / 可选 Neo4j schema]
    Init --> Mount[挂载扩展 API / 构造必要模型组件]
    Mount --> Pipeline[默认双链由 Selector 惰性选择]
    Pipeline --> GC[启动 GC 首轮立即运行]
    Pipeline --> Consumers[可选 ACL / publication 消费者]
    Consumers --> Wait[等待主消费者连接]
    Wait --> Reg[Nacos 注册尝试]
    Reg --> Ready[宿主及子 API ready]
    Ready --> Stop[退出或后台异常 / 撤销 ready]
    Stop --> Cancel[取消并等待后台任务]
    Cancel --> Close[按登记逆序关闭共享客户端一次]
    Close --> Dereg[Nacos 注销尝试]
```

首轮 GC 不参与就绪等待；Nacos 注册失败记录后仍可 ready。Kafka 关闭时连接等待为空，GC 仍启动。pipeline_with_context/pipeline_without_context 是平台共享链，默认 pipeline 按 ENABLE_CONTEXTUALIZE 选择；扩展可以显式选另一条或自行组合。

永久销毁先撤销发布可见性，GC 后续清理所有已配置向量、拓扑、ACL 和内容，永久标记保留。离线 repair 是独立维护入口，不属于 lifespan，也不会改变发布状态。配置与操作见[运行说明](deployment.md)、[GC](usecases-gc.md)和[运维手册](operations.md)。
