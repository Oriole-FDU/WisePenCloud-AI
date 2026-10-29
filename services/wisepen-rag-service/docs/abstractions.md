# 核心抽象与实现注意事项

先读[中立数据模型](models.md)。本文定义组件职责和装配合同；完整业务流程属于 application，见[用例导航](README.md)。

## 1. 六类抽象

| 抽象        | 输入/输出                               | 拥有的职责                                               |
|-------------|-----------------------------------------|----------------------------------------------------------|
| Chunker     | Document → list[Chunk]                  | 正文边界、顺序、结构归属；默认适配 Common Markdown       |
| Transformer | Chunk 列表 + 可选 Document → Chunk 列表 | 增强或校验；可调用模型，可原地修改或返回新列表           |
| Indexer     | 最终 Chunk 列表 → None                  | 保存内容事实或生成投影，等待必需写入确认                 |
| Storage     | 明确身份/过滤条件 → 原始事实或投影      | 数据库存取原语；不选择当前版本，不判权限                 |
| Retriever   | query: str → list[Candidate[T]]         | 构造查询、召回候选、融合；不接收请求快照，不回查授权正文 |
| MessageConsumer | handler + 可选 started event → None | 运行已绑定 topic/group 的消息处理；不解释业务 payload    |

Pipeline 是顺序编排器，其接口定义在 domain、实现与编排仍在 ingestion。application 才拥有资源发布、授权准备、章节读取、
证据组装和响应；消息业务 handler 留在 application，Container 注入 MessageConsumer；共享组件不保存用户请求状态。

### 摄入接口

定义见 [Chunker](../src/rag/domain/interfaces/chunker.py)、[Transformer](../src/rag/domain/interfaces/transformer.py)、[Indexer](../src/rag/domain/interfaces/indexer.py)。

```python
from typing import Protocol
from rag.domain.models.content import Chunk, Document


class Chunker(Protocol):
    async def split(self, document: Document) -> list[Chunk]: ...


class Transformer(Protocol):
    async def transform(
        self, chunks: list[Chunk], *, document: Document | None = None,
    ) -> list[Chunk]: ...


class Indexer(Protocol):
    async def index(self, chunks: list[Chunk]) -> None: ...
```

## 2. Pipeline 装配合同

[IngestionPipeline](../src/rag/ingestion/pipeline.py) 固定串行执行：

```text
Chunker → 全部 Transformers → ChunkFactsIndexer → 全部必需投影 Indexers
```

`run(document)` 不自动复制输入。每次采用 Transformer 的返回值，只有所有 Indexer 成功后才返回最终同一列表，供发布用例核对预期
Chunk 集合。Transformer 保持 Chunk 身份、正文、顺序及结构归属，Indexer 只读输入，不用 pop 消费 metadata。

开发者组合正确的完整链，不增加组件识别、依赖图或重复装配校验。组件内部可以批处理/并发，但 Pipeline 不并行
Indexer。任一未处理异常停止后续阶段，已经完成的写入保留，不回滚、不补偿、不 continue_on_error。传输重试在 Kafka 边界。

### 当前组件的特殊合同

| 组件              | 处理方式与注意事项                                                                                                                                 |
|-------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| MarkdownChunker   | 适配 Common 分块，目标 800 token、拆分阈值 1200 token；逐 Chunk 深复制 Document.metadata，嵌套对象互不共享                                         |
| Contextualizer    | 用完整提示词 token 预算选择全文/祖先/局部窗口；供应商及输出失败逐块清旧值并降级；metadata 序列化错误与取消传播；信任在线生成或已通过读取边界的结构 |
| GraphExtractor    | 仅以当前 raw_text 抽取，section_path 辅助消歧；本体约束事实；全部块成功后替换图 metadata，模型失败传播                                             |
| ChunkFactsIndexer | 仅保存最终 Chunk，不另造持久化模型                                                                                                                 |
| VectorIndexer     | retrieval_text → 分批 embedding → VectorWriter；数量/维度/数值合同由 Common 核对，失败转为可重试错误并停止后批                                     |
| GraphIndexer      | 同次解析图事实，先写可选拓扑、再写可选实体/关系来源向量；至少启用一种投影；没有独立 GraphVectorIndexer                                             |

Contextualizer 的默认值为输入 8000 token、目标 100 字、输出硬上限 150 token、before_ratio=0.5、大纲比例 0.15、实例模型并发
4。默认 TokenCounter 为 Common 离线 o200k_base。成功指纹匹配复用，failed/过期重试；全文模式不加入业务
metadata。详见 [contextualizer.py](../src/rag/ingestion/transformers/contextualizer.py) 和[运维手册](operations.md)。

GraphExtractor 默认输入 8000 token、输出 4096 token、并发
4；补漏默认关闭、最多一次，不能改写首轮合法事实。指纹包含本体、模型、策略、生成配置和实际消息；来源匹配且整体合法才复用。成功缓存不能证明数据库投影已经写完。

## 3. metadata：事实与投影分开

| 阶段                 | 合同                                                                                                                                                                   |
|----------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| DocumentReadyEvent   | 业务字典保存到 Document，不新增通用业务 schema                                                                                                                         |
| MarkdownChunker      | 为每个 Chunk 深复制业务字典，避免块间及 Document/Chunk 互相影响                                                                                                        |
| Transformer          | 按组件合同保留其他字段；Contextualizer 管理自己的 context 字段，GraphExtractor 管理自己的图字段                                                                        |
| ContentWriter upsert | 完整对象替换，不自动合并 metadata，也不删除批次外的 Chunk                                                                                                              |
| VectorIndexer        | factory 只返回业务增量，通用身份最后合并；不自动投影所有 Chunk.metadata                                                                                                |
| 查询结果             | ChunkHit.metadata 来自当前授权 Chunk，排除 `_CHUNK_HIT_EXCLUDED_METADATA_KEYS` 指定的内部字段（当前为 entities、relationships）；不从向量 payload 拼装，不修改存储事实 |

### metadata_factory 的增量合并

[VectorIndexer](../src/rag/ingestion/indexers/vector.py) 的参数是 `Callable[[Chunk], dict[str, object]] | None`
。其合并顺序等价于：

```python
def vector_metadata(chunk, metadata_factory=None):
    # 通用定位身份必须来自当前 Chunk，扩展返回的同名值不能覆盖它。
    return {
        **(metadata_factory(chunk) if metadata_factory else {}),
        "resource_id": chunk.resource_id,
        "content_revision": chunk.content_revision,
    }
```

factory 的“增量”指向量 payload 增量，不是把业务字段补写回 Chunk。Employment 仅返回 employee/year；原始 start_date 仍留在事实
metadata，见[示例](employment.md)。更改 metadata/type 的摄入效果要求提高 version。

## 4. Storage 与后端

| 端口                                                  | 关键方法                                                                 | 合同                                                                     |
|-------------------------------------------------------|--------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [ContentReader/Writer](../src/rag/domain/storage/content.py) | get_documents、get_chunks、get_revision_chunks、upsert_*、delete_*       | 读写完整事实；批量去重保序、缺失省略；revision Chunk 按 chunk_index 升序 |
| [VectorReader/Writer](../src/rag/domain/storage/vector.py)   | query、upsert、delete                                                    | 候选投影；写/删确认后返回 None，删除要求非空条件                         |
| VectorStorage                                         | collection、ensure_collection(metadata_index_fields)                     | 实例绑定集合；必填 list[IndexableField]，空列表仍初始化/核对向量配置     |
| [GraphReader/Writer](../src/rag/domain/storage/graph.py) | get_nodes、get_relations、get_neighbors、replace_sources、delete_sources | 来源描述独立；replace_sources 只替换传入来源，含空事实；写入返回 None    |
| GraphStorage                                          | ensure_schema()                                                          | 组合图读写端口；必须实现显式 schema 初始化，可重复调用，失败传播         |

Mongo、Qdrant、Neo4j 的 SDK 转换在 [core/persistence](../src/rag/core/persistence/__init__.py)。业务扩展使用 IndexKind，不导入数据库 SDK
索引类型。Qdrant 普通集合 named vectors 为 dense/sparse，sparse 使用服务端 qdrant/bm25 与 cloud_inference；全部必需向量成功才返回，不降级
dense-only。

Mongo 实体位于 `domain/entities`，由 Beanie 管理日常实体操作：普通读取使用 `find`/`find_one`，删除使用 query `delete`，新建 publication
状态通过实体实例的 `insert()` 创建。需要保留批量与条件原子语义的写入使用对应实体的 `get_pymongo_collection()`：内容批量
ReplaceOne、ACL revision 条件 upsert、publication CAS。存储端口不暴露 Beanie 或 PyMongo 类型。

Kafka adapter 位于 [core/messaging/kafka.py](../src/rag/core/kafka.py)，实现 [MessageConsumer](../src/rag/domain/interfaces/consumer.py)。
协议签名为 `async run(handler: Callable[[dict[str, object]], Awaitable[object]], *, started: asyncio.Event | None = None) -> None`。
Container 注入已配置连接、topic、group 和 retry policy 的 adapter；application 在 `run(handler=..., started=...)` 传入业务 handler。

GraphStorage.ensure_schema 是存储协议的一部分，由 main 在启用图存储时、图 repair
在启用拓扑投影时显式调用。后端负责所需约束/索引的具体初始化；只读或只写消费方仍依赖 GraphReader/GraphWriter。

Qdrant 每批 wait=True 并核对 completed；Neo4j 来源批使用显式事务并等待提交，前批成功保留。提交异常可能结果未知，传播后幂等重跑，不假定回滚成功。没有跨库事务、Receipt
或完整性完成清单。

## 5. Retriever 与授权准备

Common [Candidate/检索 Pipeline](../../wisepen-common/src/common/utils/retrieval/__init__.py) 提供
candidate_id/payload/rank/score、融合、重排及多样化。RAG 的 [VectorRetriever](../src/rag/retrievers/vector.py)
和 [GraphRetriever](../src/rag/retrievers/graph.py) 只做种子召回。

`from_storage(...)` 固定本次 mode、预算、filters、include_embedding 和共享依赖；`retrieve(query: str)` 只接收字符串。application
每次直接构造组件，把 ResourceSnapshot 留在异步 prepare_candidates 中；不使用 retriever_factory、请求缓存或全局 context。

prepare 在重排/MMR 前完成来源授权和当前事实核验。普通和图候选最终都转成 PreparedChunk；图上下文与 Chunk 精排结果并列返回，内部
payload 不进入结果。Common 重排失败保持召回顺序降级，其他阶段异常传播；授权过滤后不补召回。MMR 仅用于普通向量检索并使用已存向量；图检索移除
MMR，采用来源名额预选。文本预算和响应缓存属于上层调用者。

图检索与 traversal 共用 [GraphEntity/GraphRelationship](../src/rag/application/graph/models.py)
应用模型：一个逻辑对象只返回一次，实体与关系都只保留 id/sources，逐来源字段放入 sources；关系关键词及 metadata
归各来源。收拢发生在授权之后，不改写核心事实、不拼接描述，不改变 Chunk 来源池和精排单位。内部图运算仍使用核心 GraphEdge
的结构化端点与谓词，不解析返回 ID。
