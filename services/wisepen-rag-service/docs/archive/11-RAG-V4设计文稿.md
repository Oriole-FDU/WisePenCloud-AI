# RAG V4 设计文稿

## 1. 文档目的和范围

本文是 RAG V4 的实现设计合同，整合 00–10 方案中已经确认的边界。它描述核心模型、写入管线、存储端口、普通检索、图谱能力、权限和版本语义。

2026-10-12 已落地共享向量模型、通用过滤、Qdrant 后端及普通向量发布闭环，见 [17](17-vector-publication-implementation.md)
；请求内资源快照与内容读取见 [18](18-application-snapshot-implementation.md)，Common 泛型 Pipeline、VectorRetriever 与
VectorRetrieval 见 [19](19-hybrid-retrieval-implementation.md)
，统一章节身份与结构化读取见 [20](20-structured-reading-implementation.md)。Contextualizer、标题路径前缀与维护窗口离线
repair 已完成，见 [21](21-contextualizer-implementation.md)。图事实首片已完成（见 22），多来源拓扑投影已完成（见
23），图向量和三种种子召回组件已完成（见 24），授权图检索、一跳 traversal 与 Chunk 实体候选已实现（见 25）；父块暂缓，不做检索效果基线；P8
首片已实现生产配置、通用装配、服务 GC 与生命周期（见 26）；在线重构建、历史贡献回收和真实环境联调留后。

2026-10-12 P5 首片已完成 Ontology、GraphExtractor、增量复用、可选补漏、Chunk 图事实落库与离线图
repair，见 [22](22-graph-extraction-implementation.md)。图事实/本体统一为 core.schema.graph 中的 dataclass，只有 Instructor
输出 DTO 使用 Pydantic。GraphStorage/GraphIndexer、Neo4j 多来源投影与可选 repair 已完成（见 23）；当前排期不做确定性
Producer。

本设计暂不装配通用业务 HTTP/MCP transport；Employment 扩展示例已经实现只读子 API
并由平台挂载，见 [28](28-employment-extension-implementation.md)。MCP 只作为可信身份来源的既有边界被引用：最终用户身份由可信安全上下文注入，RAG
application 接收 `PermissionScope`，不从业务参数读取用户 ID。

本设计也不追求 Mongo、Qdrant、Neo4j 的全局强一致。它只消除会造成错误的内容混版、越权访问、跨版本图谱读取和不受控的索引语义漂移。

## 2. 核心设计结论

### 2.1 五类核心抽象

写入侧只有五类核心抽象：

```text
Document
  -> Chunker.split(document)
  -> list[Chunk]
  -> Transformer.transform(chunks, document=document) ...
  -> Indexer.index(chunks) ...
  -> Storage

Retriever
  <- Storage Reader
```

| 抽象          | 负责                                             | 不负责                                                   |
|---------------|--------------------------------------------------|----------------------------------------------------------|
| `Chunker`     | Document 分块、边界、顺序和结构归属              | Embedding、图谱抽取、持久化                              |
| `Transformer` | 对完整 Chunk 列表做内容加工或知识增强            | Storage、Embedding、持久化                               |
| `Indexer`     | 保存最终 Chunk 事实或生成检索投影，并调用 Writer | 底层 provider CRUD、检索策略                             |
| `Storage`     | 内容、向量、图存取原语                           | ACL、revision 生命周期、检索模式                         |
| `Retriever`   | 查询构造、候选生成和融合算法；固定依赖与配置     | 请求身份、资源版本选择、授权、正文回查、索引构建和持久化 |

`IngestionPipeline` 只是顺序编排器，不是新的业务仓储层、事务协调器或错误补偿器。

读取与检索用例属于 application。普通混合检索 use case 编排召回、资源版本与授权、正文回查、重排和响应组装，不能与执行融合算法的
HybridRetriever 混为一体。Retriever.retrieve 只接收字符串查询；预算、filters 和返回向量选项在初始化时确定，由 application
直接调用 VectorRetriever.from_storage 按本次配置装配；请求内版本与权限决定不注入 Retriever constructor，也不通过全局变量或隐式上下文传递。

### 2.2 最小接口

```python
class Chunker(Protocol):
    async def split(self, document: Document) -> list[Chunk]: ...


class Transformer(Protocol):
    async def transform(
        self,
        chunks: list[Chunk],
        *,
        document: Document | None = None,
    ) -> list[Chunk]: ...


class Indexer(Protocol):
    async def index(self, chunks: list[Chunk]) -> None: ...
```

```python
class IngestionPipeline:
    def __init__(
        self,
        *,
        chunker: Chunker,
        transformers: list[Transformer],
        indexers: list[Indexer],
    ):
        self.chunker = chunker
        self.transformers = transformers
        self.indexers = indexers

    async def run(self, document: Document) -> list[Chunk]:
        chunks = await self.chunker.split(document)
        for transformer in self.transformers:
            chunks = await transformer.transform(chunks, document=document)
        for indexer in self.indexers:
            await indexer.index(chunks)
        return chunks
```

接口约束如下：

- `Document` 进入 `Chunker.split()`，并作为可选参数传给 Transformer；
- Transformer 和 Indexer 接收完整 `list[Chunk]`；
- Transformer 可以原地修改 Chunk，也可以返回新 Chunk；Pipeline 始终使用返回值；
- Pipeline 不自动深拷贝，不要求不可变 Chunk；
- Chunk identity、resource/revision 来源、顺序和 raw text 必须保持稳定；
- Indexer 只读共享 Chunk，不使用 `pop()` 消费 metadata；
- Pipeline 按配置顺序串行执行；组件内部可以自行批处理或并发；
- 任一未处理异常直接传播；Transformer 失败停止后续阶段，Indexer 失败停止后续 Indexer，已经完成的写入保留；
- Pipeline 不提供回滚、`continue_on_error`、跨 Indexer 并行或统一 failure object；
- 只有所有阶段成功返回，部分索引可见性和 revision 发布由外部 capability 决定。

### 2.3 开发者装配合同

Pipeline 是内部执行组件，不增加装配校验、组件类型识别、依赖图或空配置检查。开发者在外部固定普通 Pipeline 顺序，并将完整
Pipeline 注入应用用例，保证必需 Indexer/Writer 齐全，测试验证实际执行行为。各 Indexer 仅在全部必需写入确认后成功，应用用例在所有阶段成功后才能发布。

GraphExtractor/GraphIndexer 配对和 `graph_enabled=False` 时的组件选择同样属于开发者合同。Pipeline 全部成功后返回最终同一
`list[Chunk]`，供发布用例核对落库集合。

## 3. Document、Chunk 和 metadata

Document、DocumentStructure 和 Chunk 统一定义在 `core/schema/content.py`，独立模型共同服务 ingestion 与 ContentStorage。框架
schema 不依赖执行阶段或具体后端。

### 3.1 Document

`Document` 是分块入口，至少表达：

```text
resource_id
document_version
content_revision
text
structure
metadata
type  # 可选摄入类别，None 使用默认链
```

Document 结构沿用 Common Section/Anchor，但章节及父章节 ID 在摄入适配时统一为
`rsec_ + sha256(resource_id + "\0" + content_revision + "\0" + common_section_id)[:24]`；Chunker 共用同一身份函数，ChunkHit
传递同一 ID。树、范围、路径、摘要和 preview 保留；不改 Chunk.chunk_id 或向量 record_id。调用方只传章节 ID，不解析 ID
或额外携带资源/revision。

Document.type 由 publication 在正文身份校验后、stage 前按原值精确选择注册 Pipeline；None 使用默认链，未知类别抛出
ValueError，不进行写入。Document metadata 保存垂类业务事实，组件不自行选择摄入链；type 与 Ontology domain、资源物理类型分开。

平台不预设 graph_enabled 等垂类业务字段。外层组合入口把完整 IngestionPipeline 注册到 Container.pipeline_routes ()
的类别键，平台消费者和发布用例共享这一映射；type 使用开放字符串，允许不同类别装配不同本体。

默认 MarkdownChunker 将 Document.metadata 深复制到每个 Chunk，嵌套可变对象互不共享。不另增 Chunk.type 或影子 metadata
字段，不自动将任意业务字段投影到向量/图后端。Mongo 旧 Document 缺失 type 读为 None，位置参数的 metadata 兼容保留。

### 3.2 Chunk

```python
@dataclass(slots=True)
class Chunk:
    chunk_id: str
    resource_id: str
    content_revision: str
    chunk_index: int
    section_id: str | None
    section_path: list[str]
    raw_text: str
    content_token_count: int
    anchor_labels: list[str]
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def retrieval_text(self) -> str:
        context = str(self.metadata.get("contextual_context") or "").strip()
        prefixes = [part for part in (">".join(self.section_path), context) if part]
        return "\n\n".join([*prefixes, self.raw_text])
```

`metadata` 是普通 `dict[str, object]`，依赖开发者遵守约定，不引入 Pydantic 模型、metadata 注册表或通用 schema 校验。

约定键为：

```python
metadata["contextual_context"]  # 检索上下文，可缺失
metadata["context_status"]     # ContextStatus 的字符串值：ok/fallback/failed
metadata["context_version"]    # 仅成功结果的生成指纹
metadata["entities"]            # GraphExtractor 的实体结果
metadata["relationships"]       # GraphExtractor 的关系结果
```

metadata 不得改变 Chunk identity、顺序、ACL 或 active revision。图谱启用时，`entities` 和 `relationships` 是随 Chunk
持久化的可重建抽取事实；其来源就是所属 Chunk，不再复制到独立 GraphFactStore/GraphChunkSource 集合。`contextual_context`
属于可重建的检索派生值，不要求作为图谱事实持久化。

## 4. 权威事实、版本和权限

### 4.1 身份边界

平台保证 resource ID、user ID 和 group ID 全局唯一。RAG 不增加 tenant/workspace 维度。

用户身份只来自可信安全上下文。MCP adapter 将其转换为：

```python
PermissionScope(
    user_id=trusted_user_id,
    group_roles=trusted_group_roles,
)
```

application、repository、Retriever 和 GraphRetriever 不解析 HTTP header、MCP metadata 或用户传入的身份字段。

### 4.2 ACL

上游 Resource Service 是 ACL 权威。RAG 只消费现有 `AclRecalculateMessage`，读取当时可见的 ACL 状态，完整替换本地
`ResourceAcl`。

现有事件只是刷新触发，不证明上游 `computedGroupAcls` 已完成重算。因此本地 ACL 允许短暂最终一致：

- 新授权可能延迟生效；
- 新撤权可能存在短暂安全风险窗口；
- RAG 不使用时间戳、伪造版本、等待或重复读取猜测上游完成语义。

查询中所有结果必须经过同一个 `ResourceAcl.can_read(scope)`。Qdrant/Neo4j ACL 条件只能做候选预过滤，不能取代最终判权。没有本地
ACL 投影时不能直接判定资源可读。

判权以 Document 所属 resource_id 为单位，Chunk、标题树及图谱来源继承这一决定，不增加 Chunk 或 Section 级
ACL。一次调用中已确定的可见性保持固定；新调用重新读取本地投影。

### 4.3 ContentRevision

```text
resource_id        上游资源全局身份
document_version   上游内容版本
content_sha256     RAG 对完整 Markdown 计算的摘要
content_revision   上述字段组成的本地组合键
```

ContentRevision 是 `core.schema` 的不可变内容身份；摘要基于完整 `Document.text` 的 UTF-8 字节，不去空白或规范化。组合键为
`resource_id@document_version#完整SHA-256`，不是旧实现的摘要前缀。

同一版本同一 hash 的事件幂等；同一版本不同 hash 是内容合同冲突，拒绝覆盖；低版本事件不能恢复旧 staged/applied；高版本才创建新
staged revision。

DocumentRevision、Chunk 和各类向量/图投影都必须携带 `resource_id/content_revision`。一次能力调用中，资源第一次确定 applied
revision 后，后续正文、Chunk 和投影回查都固定使用该 revision。active 期间变化不触发重试或切换。

`application/snapshot.py` 是多个读取／检索用例共享的入口。`ResourceSnapshot(scope)` 只保存本次调用逐资源确定的
`ContentRevision | None`；`ResourceSnapshotReader(store, resource_acls, reader)` 内部批量解析状态和 ACL，公开读取
Document／Chunk 及按章节 ID 定位的可见 Document，不保留请求状态。普通混合检索、目录/邻域与章节读取已使用
Reader，图谱用例已复用同一入口，不再单独装配 resolver。

每次独立调用创建新 ResourceSnapshot，同一次编排显式共享它。首次不可见也缓存；调用期间 applied、ACL 或 destroyed
变化均沿用首次完成的决定，不增加输出前回查。已选事实缺失或身份不符则省略，不切换新版或回退其他 revision。Document
回查重新计算完整正文身份；Chunk 继承资源级判权并匹配 selected revision。批量去重保序、空输入无
I/O，连接／查询／解码异常直接传播。这个请求内资源快照不提供跨后端原子观察点，也不保存正文。

### 4.4 staged/applied

staged 是后台构建中的候选，不对用户可见。只有正文/结构、Chunk 集合、普通检索所需向量投影和 staged 条件仍成立时，外部
publication capability 才能切换 applied。

publication 属于 `application.publication` 的用例编排：接收资源变更后，协调内容保存、Pipeline、事实回查与可见性切换。ResourcePublicationState
是该用例的流程状态，不是与 ACL 并列的独立领域能力。Mongo 在 `resource_publications` 保存 staged/applied 两个完整
ContentRevision 与永久 destroyed 标记。

模块收敛为 models.py、store.py、publish.py，另有包导出 __init__.py。publish.py 同时定义事件、消费者和
DocumentPublication，消费者仅注入 publisher 并调用 publish (document) 或 destroy_resources。DocumentPublication.publish
接收已构造 Document，校验身份后选定本次 Pipeline，再由 Store 判断版本、冲突与销毁状态并 stage，再保存 Document、执行外部完整
Pipeline，最后回查正文身份和精确有序的预期 Chunk ID 集合并条件 apply。摄入是发布的内部步骤，不再提供独立 DocumentIngestion
或公开 stage/apply 方法。注册链内全部阶段都是必需阶段，包含 GraphIndexer 时其失败同样阻止 apply；可选发布后图流程不放进这条必需链。投影完整性依赖必需
Indexer 的成功合同，不增加向量完整性查询接口。同资源构建由调用方串行；重复已发布、旧版本和已销毁提交返回明确跳过结果，不重写
metadata。允许零 Chunk 内容。

图谱是附加能力。GraphIndexer 失败不会回滚已完成的普通向量写入；是否允许该 revision 成为普通检索 active 由外部发布策略决定，不由
Pipeline 伪造跨存储原子性。

物理销毁先置永久 destroyed 标记并撤销 staged/applied，再清理内容与投影。同 ID 不提供恢复；迟到内容不能重新发布。上游个人
`.Trash`、软删除审计集合与物理销毁是不同阶段，前两者不调用永久销毁操作；回收站搜索可见性尚未处理。新调用观察到 destroyed
后不得返回残留事实；已经确定可见性与版本的调用沿用首次决定，事实缺失则省略。

## 5. 写入管线

### 5.1 Chunker

Chunker 封装现有 `MarkdownChunker`，负责分块边界、chunk_index、section 归属、anchor labels 和本地 Chunk identity。它不调用
LLM、Embedding、Storage 或 Mongo。

### 5.2 Transformer

#### Contextualizer

已实现在 `ingestion/transformers/contextualizer.py`。非空输入要求匹配 Document 与同一 resource/revision 的全部
Chunk；只增强三个上下文字段，保持原列表、顺序、正文 token count 与身份。普通顺序为
`Chunker → Contextualizer → ChunkFactsIndexer → VectorIndexer`。

默认完整提示文本上限 8000 token、目标 100 字、API 输出硬上限 150 token、before_ratio=0.5、大纲占比 0.15、实例模型并发上限
4。注入 Common TokenCounter，默认离线 o200k_base；计数包括材料、metadata、大纲、目标、指令及分隔符。InstructionClient 初始化绑定模型，只读
model 用于指纹；finish_reason=length 拒绝输出。

全文作为虚拟根先检查，能放下就省略 metadata/大纲。否则遍历当前章节全部祖先，选择完整提示词可容纳的最高 subtree；再降级为完整
own 范围或目标 Chunk 种子。全局窗口允许跨章节，每个邻居 Chunk 只加入一次，按 chunk_index 原文顺序拼接；没有 source spans
时不按字符串去重或伪造 offsets。新增邻居按 token 权重和 before_ratio
选择比例误差最小的一侧，平局优先上文；每侧都重新计数完整提示词，一侧放不下仍尝试另一侧，双侧不可容纳才停止。此为连续扩展，不保证全局最优装箱。

局部提示展示非空 metadata、纯标题骨架和选定材料，明确其为局部原文，要求与目标同语言、只据材料、只输出上下文。Metadata 先
Pydantic JSON 序列化再确定性 Unicode JSON 渲染，未知对象不强制字符串化。OutlineFormatter.show_extra_info 默认 True
保持旧输出；False 去除锚点、身份、偏移、当前标记、摘要/preview，anchors 可省略。大纲按 floor (预算×占比)
限制，超限保留当前祖先链，再补邻近兄弟和顶层标题，维持父链与树序；最小链不适配时省略大纲，不截断标题。

成功写 contextual_context、ok 和 SHA-256 context_version。指纹含策略版本、固定模型、tokenizer
名称、生成参数及实际完整提示词，不含并发数；已有成功有效输出与指纹匹配则复用。失败/过期再次尝试。空目标或输入预算不足为
fallback，metadata 序列化、供应商或输出检查失败为
failed，删除旧上下文与旧指纹并记录失败；程序/结构错误和取消传播。失败块不阻止其他块，必需事实/向量写入失败仍阻止发布。语言和真实性由提示词约束，不增加第二次
LLM 验真。

`Chunk.retrieval_text` 按 `">".join(section_path)`、有效上下文、raw_text 空行拼接；无标题省略路径。前缀不存
raw_text，不截断/转义路径；展示、章节读取和 rerank 使用正文，dense 与 BM25 使用同一派生检索文本。

维护窗口入口 `scripts/repair_context.py` 仅处理显式资源范围当前 applied 未 destroyed revision，验证完整 Document 身份、读取既有
Chunk、不重分块；写前复核完整发布状态，变化则跳过。不加载 Nacos、不启动消费者、不 stage/apply。复用增量上下文后保存全体 Chunk，
**每次重写全部向量**，保证事实成功/向量失败后可重跑。逐块模型失败继续落库/索引但退出非零；文档级数据库/向量异常记录后继续其余资源，已完成写入保留，所有客户端关闭。目标
Chunk 写入不得存在其他并发任务，此维护约定不等于并发修复协议。装配与配置见 [demo/contextual.md](../demo/contextual.md)。

#### GraphExtractor

GraphExtractor 已实现在 ingestion/transformers/graph.py。接收显式 Ontology、InstructionClient 和完整 Chunk 列表，只从当前
raw_text 提取有向实体/关系，section_path 只用于消歧，不读取生成上下文或其他正文，写入：

```python
chunk.metadata["entities"]
chunk.metadata["relationships"]
chunk.metadata["graph_version"]
```

GraphNode/GraphEdge/本体为 core.schema.graph 的 dataclass；Instructor 输出 DTO 的每个字段有 description。程序绑定 Chunk
来源并计算已有稳定 ID，同块首次合法事实保留。未知类别、谓词或非法端点逐事实丢弃；整个模型/解析异常传播，取消并等待其他任务，所有块成功后才替换图
metadata。

成功指纹覆盖策略、模型、tokenizer、本体、schema、生成配置和实际消息；复用前解析缓存并检查本体/来源，合法空列表可复用。补漏默认关闭，开启最多一次，不修改/重复首轮合法事实，无增量允许空返回；新关系可引用首轮实体。默认输入
8000 token、输出 4096 token、实例并发 4；首轮超预算抛错，补漏历史超预算记录并接受首轮结果。

独立 scripts/repair_graph.py 读取显式资源当前 applied 身份与已有块，写前复核完整发布状态，再经 ChunkFactsIndexer
保存全部块；force 禁用复用，资源级异常继续其他资源并非零退出。维护窗口同资源无其他写任务；不重新分块、不
stage/apply、不写普通向量，不连接生产入口。

### 5.3 Indexer

#### VectorIndexer

VectorIndexer：

1. 选择 `chunk.retrieval_text`；
2. 调用 embedder；
3. 生成 `VectorRecord`；
4. 调用 Chunk collection 对应的 `VectorWriter.upsert()`；
5. 每批写入成功才处理下一批，全部批次成功才返回；不承担查询策略。

当前实现在 `ingestion/indexers/vector.py`。显式注入 已绑定 model/dimensions 的 Common EmbeddingClient 与
VectorWriter，embedding 默认每批 32 条。投影只包含 Chunk ID、retrieval_text、dense embedding 与 resource/revision
metadata；不修改 Chunk。供应商响应数量、维度或数值错误属于可重试的 RuntimeError，I/O 原样传播。零 Chunk 无 embedding/数据库
I/O。普通 Qdrant point 同时写 dense 与服务端 BM25 sparse，任一失败阻止 apply；本轮不删除批次外旧投影，不实现换分块配置后的同
revision 替换。

#### ChunkFactsIndexer

完整 ingestion 配置包含一个具体的 `ChunkFactsIndexer`，通过 ContentWriter 把 Transformer 返回的 Chunk（包括普通 dict
metadata）写入内容事实集合。ContentReader、ContentWriter 两个接口组合为 ContentStorage，MongoContentStorage 保存独立的
Document 和 Chunk，不增加 Repository、Node 或通用 Mongo 框架。ChunkFactsIndexer 必须在投影 Indexer 前成功，使派生事实可以在进程退出后用于重建。

#### GraphIndexer

GraphIndexer 已实现在 ingestion/indexers/graph.py：只读完整 Chunk 列表，解析实体/关系、本体校验和来源核对，再生成
GraphSourceProjection 并调用 Writer.replace_sources；启用向量时随后执行来源向量替换。缺少图列表或结构/来源损坏写前报错；本体非法事实逐条丢弃，同来源重复
ID 保留首次合法事实。合法空列表仍写完整来源替换输入。

它不调用抽取模型、不访问 publication/ACL、不修改 Chunk。GraphIndexer 现直接依赖 GraphWriter 与 VectorWriter
两种端口，完整图事实仅解析一次，依次写拓扑与来源向量；没有独立 GraphVectorIndexer。向量阶段显式注入 EmbeddingClient。repair
两个开关选择同一 Indexer 的 writer，即使抽取缓存全部复用也重写所有启用投影。见 24。

## 6. Storage

### 6.1 Reader/Writer 隔离

Reader/Writer 是依赖能力约束，不是额外仓储层。同一个 Adapter 可以实现 Reader 和 Writer。

```python
class VectorReader(Protocol):
    async def query(self, request: VectorQuery) -> list[VectorMatch]: ...


class VectorWriter(Protocol):
    async def upsert(self, records: list[VectorRecord]) -> None: ...
    async def delete(self, filters: MetadataFilters) -> None: ...


class GraphReader(Protocol):
    async def get_nodes(self, node_ids, *, filters=None) -> list[GraphNode]: ...
    async def get_relations(self, edge_ids, *, filters=None) -> list[GraphEdge]: ...
    async def get_neighbors(self, node_ids, *, direction=GraphDirection.OUTGOING,
                            relation_types=None, filters=None, limit=100) -> list[GraphEdge]: ...


class GraphWriter(Protocol):
    async def replace_sources(self, projections: list[GraphSourceProjection]) -> None: ...
    async def delete_sources(self, filters: MetadataFilters) -> None: ...
```

### 6.2 VectorStorage

VectorRecord/VectorQuery/VectorMatch 与读写 Protocol 统一定义在 storage/vector.py；原 core/schema/vector.py 和
core.schema 导出已移除，不保留兼容别名。Chunk、Entity 和 Relationship 的语义由各自 Indexer 负责投影，Storage 不接收业务实体；当前只落地普通
Chunk 用例。

Record 包含 record_id、text、embedding 与普通 dict metadata。Query 只允许 embedding/text 二选一，分别执行 dense/sparse
召回；Match 返回原 record_id、provider score 与 metadata，不返回正文。VectorQuery.include_embedding 默认为 False，按需返回
VectorMatch.embedding 中已存的 dense 向量；dense 与 sparse 查询均使用配置的 dense 名称请求向量。缺失、错误维度与非有限数值作为后端数据错误传播，不重做候选
embedding。upsert/delete 成功返回 None，不新增 Receipt；GraphWriter 同样确认全部写入后返回 None，不建立通用 Receipt 层。

不同 collection 通过不同 Storage 实例绑定：

```python
chunk_vectors = QdrantVectorStorage(
    client=client,
    collection="document_chunk_vectors_v4",
    dense_vector_size=embedding_dimensions,
    dense_vector_name="dense",
    sparse_vector_name="sparse",
)
await chunk_vectors.ensure_collection()
```

Record 不携带 namespace。resource、content revision 和 plugin projection metadata 只是可过滤字段。

Qdrant 客户端由外部注入且须设置 cloud_inference=True，使 qdrant/bm25 Document 转发到服务端；写入和查询使用相同的
multilingual tokenizer。ensure_collection 显式创建或核对 dense Cosine、sparse IDF 和资源/revision keyword 索引。CRUD
不隐式初始化。默认每批 256 条；upsert/delete 使用 wait=True，并核对 completed，部分成功保留，不自动重试。point ID 为 uuid5
(NAMESPACE_URL, "wisepen-rag-v4:vector:" + record_id)，原 record_id 存在 payload；metadata 下只写当前消费者需要的
resource_id/content_revision，不持久化 text、ACL 或未来图字段。

### 6.3 GraphStorage 与 Chunk 图事实

GraphStorage 已实现在 storage/graph.py，按 Chunk 完整来源输入
GraphSourceProjection（chunk_id、metadata、nodes、edges）替换/删除贡献，读回仍是 GraphNode/GraphEdge 来源事实。同一逻辑 ID
可返回多个来源，不合并描述。一跳支持 outgoing/incoming/both、谓词筛选及默认 100 个逻辑关系预算，匹配来源完整返回，多跳留到实际查询切片。

Neo4jGraphStorage 绑定 driver/database/graph_name，默认 neo4j/rag_v4 和每批 64 来源。共享实体/有向关系图元只存身份，来源关联各自保存
JSON 事实与原生平面 metadata 属性。ensure_schema 显式创建独立标签约束，CRUD
不初始化。每批显式事务移除旧贡献、写新事实并清理受影响孤儿；前置异常回滚当前批，前批提交保留；提交网络异常可能结果未知，传播后幂等重跑，不自动重试。

Mongo Chunk metadata 仍是权威重建输入；没有 Mongo 图事实新集合。Storage 不判 ACL、选择 applied 或调用模型。后续 application
必须用 ResourceSnapshotReader 回查 selected revision 和授权来源，从事实组装材料，不能把图 JSON
或跨来源描述作为正文权威。允许跨可见文档形成路径；图向量、种子检索与授权材料用例已实现（见 25），全面回收留后。

### 6.4 MetadataFilters

当前通用模型位于 `core/schema/filters.py`。单项 MetadataFilter 使用 key/value/operator，key 为 metadata 内相对路径；支持
EQ、IN、GT/GTE/LT/LTE。EQ 接受字符串、布尔与有限数值；IN 接受非空同类型字符串或整数列表，范围只接受有限数值且排除
bool。MetadataFilters 支持递归 AND/OR/NOT，组必须非空，NOT 恰好一个子条件。Qdrant 转换位于后端，实际字段前缀为
metadata.；不支持的操作和值明确报错。查询 filters=None 表示不限制，删除必须提供非空条件。

Plugin 负责：

- 哪些 Document metadata 可以进入 projection metadata；
- 哪些请求过滤字段对外开放；
- 请求字段如何映射为通用 filter key；
- 请求值和类型校验。

Storage 只执行通用 `MetadataFilters`，不解释 plugin、ACL、active revision 或 graph mode。

## 7. Retriever

### 7.1 Chunk 混合检索

普通检索复用完整 Common RetrievalPipeline。Common 只理解 Candidate[T] 的 candidate_id、payload、rank、score；业务 payload
不被排名算法改写，RankedCandidate 已移除。

```text
application: query + ResourceSnapshot + 本次检索配置
  -> 直接 VectorRetriever.from_storage 构造召回组件
  -> VectorRetriever.retrieve(query: str)
  -> Common 并发 dense/sparse + 默认 RoundRobin（可注入 RRF）
  -> Candidate[VectorMatch]
  -> Pipeline.prepare_candidates: ResourceSnapshotReader.read_chunks
  -> 资源授权、selected revision、向量投影 resource/revision 身份核对
  -> Candidate[PreparedChunk]
  -> Common rerank / diversity
  -> application ChunkHit
```

VectorRetriever.from_storage 初始化选择 VectorRetrievalMode（dense/sparse/hybrid，默认 hybrid）；dense/hybrid 注入
VectorReader 与已配置的 EmbeddingClient，sparse 只需 VectorReader。在装配时确定 hybrid 的 dense/sparse 预算（默认各为
30）、candidate_limit=60、filters=None、include_embedding=False 和融合策略。仅 hybrid 通过 Common FusionRetriever
并发两路；单路直接召回，保留 provider score。启用路线各自构造 Storage VectorQuery；dense/hybrid 查询 embedding 只生成一次，sparse
不生成。单路使用 candidate_limit 作为仓储 top_k，拒绝 dense_top_k/sparse_top_k；hybrid 两路预算未传时各为
30，candidate_limit 限制融合输出。每路初始 Candidate.score 是 provider score，融合只改 Candidate 排名字段，VectorMatch.score
保留 provider 分数。非法配置在初始化时失败，空白查询在 I/O 前失败，任一路异常直接传播。

VectorRetrieval 显式注入 ResourceSnapshotReader、VectorReader、已配置的 EmbeddingClient、可选 Fusion[VectorMatch] 与可选
Reranker[PreparedChunk]。用例直接调用 VectorRetriever.from_storage，按 mode 选择 dense/sparse/hybrid（默认 hybrid），将固定依赖与本次
dense_top_k、sparse_top_k、candidate_limit、filters、include_embedding 组合成召回组件。已连接的客户端与仓储共享，每次组装不新建数据库连接，不修改共享组件的配置。

用例 retrieve (query, *, snapshot, mode=HYBRID, top_k=5, focus="", diversity="off", filters=None, dense_top_k=None,
sparse_top_k=None, candidate_limit=60) 先通过显式快照批量读取，过滤缺失定位、不可见、旧版本、错误投影身份和缺失事实。PreparedChunk
只承载授权 Chunk 与可选候选 embedding。ModelReranker 通过文本提取函数只读取 Chunk.raw_text；focus 去空白为空时使用
query。MMR 使用向量提取函数，off 不请求向量，moderate/high 使用已存向量和 Common 的 0.75/0.5 参数，不重新 embedding 正文。

授权过滤后不补召回、不重查 publication/ACL，结果允许少于
top_k。召回或准备为空时跳过排序；准备失败不调用外部模型。重排失败记录日志并保留重排前顺序与分数，召回、准备与多样化异常直接传播。

ChunkHit 包含 chunk_id、resource_id、content_revision、section_id、section_path、text（raw_text）、rank、score 和默认空列表
seed_entities、默认空字典 metadata；score 属于最终排序阶段，不限定为 0–1，不命名为 rerank_score。metadata 是当前已授权 Chunk
的完整字典，不来自向量 payload、不设字段白名单；向量只用于内部排序。Pipeline 截取 top_k 并连续编号，application
组装后直接返回命中；命中后处理接口已移除。

Common BM25Retriever 使用调用方提供的 payload 文本提取函数在内存建索引，不等同于 Qdrant sparse 查询。Chat 缓存输出搜索已迁移到类型化
payload，保留原文定位与会话隔离行为。图谱不可用或未启用时普通检索独立工作。

### 7.2 图谱检索

GraphRetriever.from_storage 已实现 local/global/hybrid 三种种子路线：实体来源向量、关系来源向量、两路融合。只读取
VectorReader，不持有 GraphReader、Mongo、ACL 或请求快照。每次直接 embedding 查询一次，两路共用向量并发读取；默认 hybrid、各 30
条、总 60、Common RoundRobin，可注入 RRF，单路使用总预算且保留 provider 分数。

返回 Candidate[GraphMatch]，GraphMatch 包含 GraphVectorKind 与原始 VectorMatch；融合身份按 kind:record_id，不按逻辑图 ID
提前合并不同来源。候选不是业务响应。GraphRetrieval 已装配 Common RetrievalPipeline，prepare_candidates 负责同一
ResourceSnapshot 下授权、selected revision、向量身份/fact_sha256/graph_version 和 Mongo 当前事实检查，之后才扩展一跳并核对其全部来源。local
读取实体自身来源与一跳；global 读取选中关系及其同来源端点；hybrid 按逻辑种子形成独立证据组，共享读取不能扩大关系组范围。top_k
限制证据组，首个合法候选决定顺序、分数与 MMR 向量。

实体/关系向量按来源分别保存，文本加入本体/类别/谓词业务说明与当前来源描述；Ontology 统一事实构造和持久化解析，不把本体对象放进
Node/Edge 持久化字段。Qdrant 图集合是 dense-only，普通 Chunk 双向量不变。repair 支持 --with-graph-vectors 与免抽取/免事实写入的
--projection-only。详情与失败语义见 24。

RAG 不裁剪输出原文/描述，不管理结果缓存或懒读取，预算归 Chat。`mix` 是否排期待定；若未来接入，仍属于 application 外层组合，不下沉到
GraphStorage 或 GraphRetriever。

### 7.3 Chunk 种子

ChunkHit.seed_entities 是默认空列表，元素 GraphNodeReference 只含 id/relations。VectorRetrieval 不依赖 Ontology，从最终已授权
Chunk 当前 metadata 生成引用；逻辑 ID 沿用本体、类别和规范化名称的身份，不解析 ID
字符串。它们只是候选，普通检索不会自动遍历。没有事实时为空，补充解析失败记录日志并保留正文命中。

GraphTraversal 接收调用方选择的逻辑 node_ids，先从当前任意合法可见来源确认节点，再按可选方向和谓词读取一跳。来源不必绑定原
ChunkHit，但必须经同一 ResourceSnapshotReader 的资源授权和 selected revision 回查，并在当前 Mongo metadata 中存在合法事实。None
方向为 BOTH，None 谓词不限制，[] 不读关系；不暴露 depth，模型看到结果后再选择下一跳。

GraphEvidence 包含 kind/fact_id/entities/relationships/sources，实体视图 GraphEntity 为
id/source_id/description/relations，一个逻辑种子一组；GraphHit 再附最终 rank/score。关系端点来自支持该关系的同一
Chunk，各来源描述独立返回，所有事实均可定位到组内完整原文。一跳默认批量上限 100
个逻辑关系候选，已选合法来源不截取；过滤后不补读取。实体/关系/来源去重保序。来源对象不携带 contextual_context 或任意
metadata。

GraphRetrieval / GraphTraversal / VectorRetrieval 不依赖 Ontology。Ontology.create_node 在写入时根据 category 整理
relations（谓词与允许方向），随 GraphNode 普通字典保存；空端点类别限制适用该侧任意类别。它是类别允许的关系提示，不声明当前可见事实存在；查询侧不反向限制遍历，指定无事实的谓词返回空关系。

text_sha256 继续记录实际索引文本；查询使用 fact_sha256 与 graph_version 核对当前事实输入，无需加载本体重新渲染文本。GraphNode
仍保留生产与索引需要的名称和类别，应用实体视图只保留 ID 及来源材料，不解析 ID 字符串。

两个用例共享公开 GraphEvidenceLoader / EvidenceBuilder，调用内合并来源读取和事实解析；底层 adapter
已保证的投影结构和身份不再重复检查。两个图列表均缺失作为无图事实，部分缺失、结构或来源损坏异常传播，读取不套用单一本体规则，缺失合法端点的关系逐条丢弃。filters
支持 resource_id/content_revision/ontology_name 的字符串 EQ、字符串列表 IN 与 AND/OR/NOT；表达式形状与类型由底层 adapter
检查，同时约束向量、拓扑和当前来源事实。GraphIndexer 将绑定的 domain 写入来源 metadata.ontology_name；Mongo 回查按当前
GraphNode.ontology_name 筛选，关系保留满足条件的同来源两端，不读取业务 metadata 或解析 ID。默认不筛选本体，查询不依赖
Ontology。旧 Neo4j 来源属性用 projection-only 重建，见 [30](30-graph-ontology-filter-implementation.md)。

### 7.4 父块

父块构建因收益尚不明确而暂缓，不保留命中后处理接口、ParentBuilder 或空 parents 字段。显式章节读取独立于检索，可读取 own 或完整
subtree；它不自动扩展命中正文。

### 7.5 结构化读取（已实现）

DocumentReading 仅注入共享 ResourceSnapshotReader，提供 global_outline (resource_id, *, snapshot, max_level=0)
、neighborhood (section_ids, *, snapshot, sibling_steps=1) 与 read_sections (section_ids, *, snapshot, scope=own)。目录及邻域复用
Common OutlineFormatter，不重写格式或树算法。结果包含资源/revision 及 outline 或 text，章节结果另含 section_id 与 list[str]
section_path。

ContentReader.get_documents_by_section_ids 批量查询 Document 中的章节 ID；Mongo 为 structure.sections.section_id
建普通索引，按首个匹配章节的请求顺序返回去重文档。Storage 不选择版本或判权。Reader 内部定位后核对 selected
revision、ACL、文档版本和完整正文摘要，旧版本不回退。不可信定位正文不提前交给用例。

SectionReadScope 为 own/subtree，分别按 own_span/subtree_span 完整切原文；保留标题、空白和 Markdown，无递归深度、窗口或续读。范围越过正文长度抛
RuntimeError；SourceSpan 自身拒绝负数或倒序，数据库解码异常直接传播。批量输入去重保序，不可见或缺失省略；全局目录不可见为
None，可见但无章节返回空目录。负目录/邻域参数及非法 scope 在 I/O 前失败。共享用例无请求缓存，同次上层操作显式共享 snapshot。

## 8. Ontology、Producer 和 Plugin

### 8.1 Ontology 三层约束

1. Prompt renderer 告知允许的 Entity category、Relationship type、端点方向和字段格式；
2. 结构化输出校验保证类型、端点、名称、描述和长度合法；
3. GraphIndexer 写入前再次执行统一业务硬校验。

非法事实按事实粒度丢弃，合法事实继续写入。Ontology 不增加独立版本号。

### 8.2 当前 LLM 路径与确定性 Producer 边界

当前排期不做确定性 Producer，只实现 LLM GraphExtractor。将来接入确定性 Producer 时，应在 ChunkFactsIndexer 保存前生产事实，不能放在只读
GraphIndexer 内；它仍应使用相同 GraphNode/GraphEdge 与本体校验规则。

当前 Chunk metadata 保存每个来源的合法抽取结果。跨 Chunk 的归一和多来源投影留给
GraphIndexer，不设计事实优先级、冲突仲裁或跨类型实体链接。补漏只增加事实，不覆盖首轮合法 description。

### 8.3 Plugin identity

Plugin 只增加：

```text
name
version
plugin_id = hash(canonical(name) + "\0" + canonical(version))
```

PluginRegistry 负责空值、重复注册和名称冲突判定。不为 ontology、producer、filter 或 extractor 增加独立 ID。

## 9. 包结构和依赖方向

建议新实现目录如下：

```text
src/rag/
  core/schema/     # content.py: ContentRevision, Document, DocumentStructure, Chunk; graph.py
  acl/
  application/
    publication/  # models.py、store.py、publish.py；后者包含事件、消费者及完整发布
    snapshot.py   # ResourceSnapshot 与 ResourceSnapshotReader，共享版本／权限决定和内容读取
    retrieval.py  # VectorRetrieval、PreparedChunk、ChunkHit 与固定读取/模型依赖装配
                   # 目录/邻域与章节读取已实现；图谱检索与显式一跳已实现，MCP transport 留后
  ingestion/       # Chunker, Transformer, Indexer, IngestionPipeline
  storage/         # ContentStorage, VectorStorage, GraphStorage
  retrieval/       # vector.py: 配置式 VectorRetriever；graph 抽象留到图切片
  # 图/本体定义在 core/schema/graph.py，抽取位于 ingestion/transformers/graph.py；插件未实现
  backends/         # Mongo, Kafka, Qdrant, Neo4j
```

依赖方向：

```text
core.schema <- ingestion/storage/retrieval/graph
storage ports <- ingestion indexers/retrieval/application
backends -> storage ports
backends -> graph/acl/application.publication ports
composition -> all concrete implementations
```

Transformer 不依赖 Storage；Storage 不依赖业务策略；Retriever 不依赖 Indexer；HTTP/MCP 未来只依赖 application/composition
入口。

Kafka 后端提供通用传输重试而不导入 application/acl。内容、销毁与 ACL 的 handler 和异常分类由各用例拥有；retry topic
只等待并送回原主分区，不执行业务。默认快速重试 1/2/4 秒，再延迟 1 分钟、10 分钟各一次，最终进入 `<main>.DLT`。转发确认后才提交源
offset，允许故障窗口中的重复投递，由内容版本和 ACL 幂等合同承接；不承诺 Kafka 与 Mongo/向量之间的事务。

### 9.1 通用平台装配与生命周期（P8 首片）

`core/config/app_settings.py` 统一定义运行配置与 Nacos 默认 settings；`container.py` 声明通用 provider 和默认实例；
`main.py` 单个 lifespan 负责存储初始化、消费者握手、ready、GC 和客户端收尾，沿用 Chat/Sandbox 的入口习惯。外层可构造新
Container (settings=...) 或显式覆盖 provider，不新增 runtime/models 文件或第二套启动框架。

Mongo 内容/发布/ACL 索引、三个 Qdrant collection 与 metadata 索引、可选 Neo4j schema 均显式初始化。普通集合
dense/sparse，图集合 dense-only，共享 embedding 模型和维度。普通/图查询共享 SnapshotReader 与 Qwen RerankClient，各自
ModelReranker 的材料分别是完整 raw_text、种子 ID 与来源图描述/关键词；空候选不调用模型，失败按 Common 原顺序降级。

默认摄入只执行 MarkdownChunker → [Contextualizer] → ChunkFactsIndexer → VectorIndexer。ENABLE_CONTEXTUALIZE 默认为
false；事实与普通向量不可禁用。GraphExtractor/GraphIndexer 工厂要求调用方传入
Ontology，通用容器不持有业务本体；读取不反向绑定本体。GraphRetrieval/GraphTraversal 需要已启用拓扑存储。

GC 和 rerank 没有启用开关；GC_INTERVAL_SECONDS=600、GC_BATCH_SIZE=100。KAFKA_ENABLED=false 仅关闭消费，GC 和查询组件仍装配。Kafka
开启后 ACL 与发布使用不同 group，发布内容/销毁两个主消费者均连接才发出 started；全部启用消费者与存储就绪后置 ready，不等待
GC 首轮，不调用模型作启动探测。任务意外退出撤销 ready，停止先取消并等待后台任务，再关闭所有已创建底层客户端；部分初始化失败同样清理，共享实例只关闭一次，RerankClient
沿用 SDK 没有关闭接口。

### 9.2 永久销毁资源 GC

PublicationStore.list_destroyed_resource_ids 使用 destroyed/resource_id 复合索引，按字符串游标升序分页，不用
offset。ResourceGarbageCollector.collect 返回本轮 scanned/cleaned/errors；run 启动立即执行，结束后等待间隔，不重叠。每轮从头扫描永久标记，不记录已清理完成。

逐资源按明确 resource_id 删除普通/实体/关系向量，再删除 Neo4j 来源贡献并清理孤儿，再删除本地 ACL，最后删除全部
Document/Chunk revision。永久 destroyed 与发布记录保留，GC
不操作上游资源主库。单资源失败记录阶段、保留已完成删除并继续其他资源，扫描失败结束本轮并等待下一轮；取消传播。执行错误不撤销服务
ready，循环意外退出由宿主观察；多实例可幂等重复清理，无锁/租约/leader、同轮重试或跨库补偿。

迟到摄入/ACL 写入可能发生在某次删除之后，下轮重复回收，不承诺一次清理永久无残留。清理目标由平台已配置后端决定，与垂类算法开关无关；停用后端前先回收其数据。非销毁旧
revision 及历史分块贡献回收仍留后。

### 9.3 垂类扩展开发

P8 第二片提供 `pipeline_routes = providers.Singleton(dict)`，注入平台唯一 DocumentPublication 的可选 pipelines
参数。每个容器实例独立持有映射，构造 publisher 前后注册均生效；正常在消费者启动前注册，使用普通字典赋值/显式替换，不增加
Registry/冻结/在线热更新协议。业务包通过 Extension 交付 Pipeline、可选额外存储和工具 API，不覆盖 publisher、不接触
Kafka。Future DocumentReadyEvent 的 type/metadata 已支持，但本片不修改上游生产者。

ContentRevision 不包含 type/metadata/路由配置。影响摄入的 type/metadata 变化必须提高上游 version，相同已发布 revision
仍跳过；注册/替换 Pipeline
不自动重建历史事实，继续使用显式维护流程。实施与验证见 [27](27-document-type-routing-implementation.md)。

独立包保存业务本体、metadata、摄入组件、Pipeline、用例、工具适配与外层入口；以子容器或构造函数接收平台依赖，业务模块不导入全局
Container。平台负责共享连接、schema 初始化和 GC，垂类不管理连接和删除。纯输入映射复用通用用例，有业务准备时组合
Retriever/Common Pipeline/SnapshotReader，保留来源授权、完整 metadata、stage/apply 和普通向量确认。

业务 metadata 必须先进入权威 Chunk，再由垂类显式投影到后端并核对当前值；默认 Indexer 不自动索引年代等任意字段，GraphRetrieval
不自动接受时间条件。新增存储由平台接入清理目标。实际 type/Pipeline
注册、依赖替身测试和外层启动示例见 [extensions.md](../src/rag/extensions/extensions.md)；不预建插件 Runtime/Registry、动态
tag 路由、自动发现或运维入口。

### 9.4 Employment 高级扩展示例

`extensions/employment` 替代旧 demo。源码显式登记扩展构造器，EXTENSIONS 选择启用；容器提供 extension ()，返回真实装配产物
pipeline/vector_collections/api。main 在共享连接登记后的 lifespan 内构造、初始化、挂载，统一撤销就绪及关闭客户端；扩展不声明自己的连接生命周期。collection
与平台及其他扩展隔离，冲突启动失败。

场景为某公司内部合同，上游 metadata 只含 employee/start_date，原样保存到 Document 并由 Chunker 深拷贝；Transformer
只读校验，不回写规范化业务值。year 派生自日期，仅保存到专用向量 payload。正文图抽取只使用 raw_text/section_path，抽取
Person/Department/in_department。本包在平台默认必需索引和图索引之后增加专用 VectorIndexer，metadata_factory 只返回
employee/year 增量；通用 Indexer 合并 Chunk 的 resource_id/content_revision，身份不能被业务覆盖。全部阶段成功才 apply。

专用 Qdrant 集合使用相同 embedding 模型/维度，dense-only；VectorCollection 用 list[IndexableField] 声明 resource_id
KEYWORD、year INTEGER、employee KEYWORD，IndexKind 限定 KEYWORD/INTEGER/FLOAT/BOOLEAN，业务需要时间索引时转整数 Unix
时间戳。VectorStorage.ensure_collection 字段列表必填，每次启动显式核对集合并等待索引确认；后端独立翻译 SDK 类型。平台
vector_storages 为共享 GC writer 列表，登记配置时同步加入 storage，GC 不解释索引声明。普通集合默认只创建 resource_id
索引，通用图集合创建 resource_id/chunk_id/ontology_name，revision 仍保存在 payload 并核对；Employment 不按 revision
过滤，也不创建该字段索引。字段存储与索引声明分开，尊重当前配置而不隐式恢复索引。

EmploymentTools.search_contracts 仅把业务参数映射为 MetadataFilters，再调用专用集合绑定的通用 VectorRetrieval
(mode=DENSE)，返回完整 ChunkHit.metadata。VectorReader 执行 filters，通用准备只核对授权/版本/记录身份，不重复业务筛选或业务
schema 解析；业务 metadata 变更必须提高 version，不额外保证同 revision 非合同改写后的投影新鲜度。图三模式和确定性一跳工具传
ontology_name=employment 后直接返回结果，不额外读取 Chunk、校验业务 DTO 或转换证据。共享该本体的可见来源可提供事实，无关系实体仍保留；不解析
ID、自动多跳或补召回。API 复用 Common 可信网关 Header 中间件，按请求构造 snapshot，禁止业务参数声明身份；FROM_SOURCE_SECRET
缺失拒绝装配。只读 API 不代替平台 publication/Kafka 或定义写权限。

示例初版见 [28](28-employment-extension-implementation.md)
，当前协议与查询收敛见 [29](29-vector-index-contract-implementation.md)
，配置/样例/维护见 [Employment README](../src/rag/extensions/employment/README.md)。历史贡献全面回收、在线插件发现、通用
transport 与时间图查询继续留后。

## 10. 一致性、失败和可观测性

### 10.1 接受的工程权衡

- ACL 本地投影允许短暂最终一致；RAG 不补偿上游重算完成语义；
- 多后端不存在统一瞬时观察点；
- 请求内逐资源固定首次完成的版本与可见性决定，不追求请求结束时最新，也不增加输出前 ACL／destroyed 回查；
- 单个图谱投影失败不回滚已经完成的普通投影；
- 普通检索、图谱查询和直接读取分别决定局部失败和部分返回。

### 10.2 必须消除的错误

- 同一请求混用不同 content revision 的正文、Chunk、Entity 或 Relationship；
- 任何检索路径绕过统一 ACL；
- 未验证 source Chunk ID、resource 和 revision 的 node 进入图遍历；
- VectorIndexer、GraphIndexer、Transformer 各自解释 active revision 或 graph_enabled，导致语义漂移；
- GraphExtractor 产生的 metadata 被静默丢弃；
- Storage 直接理解业务检索 mode 或权限策略。

### 10.3 最小观测

只记录真实可解释的状态：

- ACL 刷新成功/失败、消费延迟和本地缺失；
- staged/applied 更新成功/冲突；
- Pipeline 阶段名称、成功/失败和耗时；
- Indexer 写入数量、失败和部分完成；
- graph fact 被 Ontology 拒绝的数量和原因；
- retrieval 候选被 revision/ACL 丢弃的数量。

不把这些指标命名为上游 ACL 版本、跨库 snapshot 或全局一致性证明。

## 11. 实现完成定义

RAG V4 核心实现完成，需要同时满足：

1. Chunk-only ingestion pipeline 可独立运行；
2. Contextual、VectorIndexer、普通 Chunk hybrid retrieval 可独立运行；
3. Chunk 中持久化的图谱 metadata 可以在不调用 LLM 的情况下重建 GraphStorage 和图向量投影；
4. GraphRetriever 的 local/global/hybrid golden cases 通过；
5. Chunk seed 到 bounded traversal 的 ACL/revision 校验通过；
6. ACL、ContentRevision、staged/applied 和删除语义通过故障矩阵；
7. 所有核心接口只依赖已确认的最小合同；
8. 无 HTTP/MCP route 也可以通过 application、fake adapter、真实 storage adapter 和离线重建测试。

## 12. 明确不做

- 通用 HTTP API、MCP tool schema 和 transport 注册（Employment 示例子 API 已实现）；
- 上游 ACL 完成事件、上游版本协议或实时权威判权；
- 跨后端事务、全局 Snapshot、Fence、ACL revision；
- Chunk metadata Pydantic wrapper、selector、codec 注册链；
- Pipeline rollback、continue-on-error、跨 Indexer 并行调度；
- LlamaIndex Node/NodeWithScore/Postprocessor 兼容层；
- LightRAG 完整 runtime、workspace 和 provider storage；
- 字符级精确回源、公共 Range、Evidence resolver 和第二套证据账本。
