# 正文向量检索

入口 [VectorRetrieval](../src/rag/application/chunk/retrieval.py)
返回授权正文命中；[ChunkRetriever](../src/rag/application/chunk/retriever.py)
仅返回候选。身份、派生文本及组件区别见[模型](models.md)、[抽象](abstractions.md)。

## 1. 依赖与最小调用

用例注入 ResourceSnapshotReader、固定集合 VectorReader、EmbeddingClient，以及可选 Fusion/Reranker/GraphReader。平台提供固定
Qwen 重排；手工组合可以不注入 reranker。每次调用创建本次 Retriever/Pipeline，连接共享，快照由调用方显式传入。

```python
from rag.application.chunk import ChunkRetrievalMode


async def search_chunks(retrieval, snapshot):
    return await retrieval.retrieve(
        "内部合同中的保密责任", snapshot=snapshot,
        mode=ChunkRetrievalMode.HYBRID, top_k=5,
    )
```

快照创建见[授权读取](usecases-access.md)。本用例是 Python application 接口，通用 HTTP transport 尚未装配。

## 2. 参数与模式

| 参数                     | 默认               | 注意事项                                            |
|--------------------------|--------------------|-----------------------------------------------------|
| query                    | 必填               | 非空白字符串                                        |
| snapshot                 | 必填，keyword-only | 本次可信请求快照                                    |
| mode                     | HYBRID             | DENSE/SPARSE/HYBRID                                 |
| top_k                    | 5                  | 正数；授权后可能不足数量                            |
| focus                    | `""`               | 空白回退 query，用于排序关注点                      |
| diversity                | off                | off/moderate/high；非 off 请求已存 dense 向量做 MMR |
| filters                  | None               | 交由 VectorReader 执行；不是 ACL                    |
| dense_top_k/sparse_top_k | None               | 仅 hybrid；各路未指定时 30                          |
| candidate_limit          | 60                 | 单路召回预算或 hybrid 融合输出上限                  |

| 模式   | 查询与排序                                                          |
|--------|---------------------------------------------------------------------|
| dense  | embedding query 一次后 dense 查询；candidate_limit 作仓库 top_k     |
| sparse | text 查询，Retriever 不调用 embedding；用例仍绑定平台客户端         |
| hybrid | dense/sparse 并发、默认 Common RoundRobin；可注入 RRF；不是分数平均 |

单路拒绝 dense_top_k/sparse_top_k。普通用例允许 candidate_limit 小于 top_k，只会返回较少结果；不要套用图用例必须
candidate_limit≥top_k 的约束。dense-only 扩展集合必须选择 DENSE。

## 3. 授权准备与结果

候选只有定位和 provider 分数。用例先筛掉无有效 resource_id/content_revision 的投影，再批量 read_chunks，核对 record_id 定位的
Chunk 与候选资源/revision；当前授权 raw_text 才进入重排和响应。

ChunkHit 字段：chunk_id、resource_id、content_revision、section_id、section_path、text、rank、score、seed_entities、metadata。text
是完整 raw_text，metadata 来自当前授权 Chunk，响应组装时排除模块常量 `_CHUNK_HIT_EXCLUDED_METADATA_KEYS` 中的内部字段（当前为
entities、relationships），其余字段原样保留；不从 payload 拼装，不解析垂类 schema。此投影不修改内部 Chunk，不影响种子解析与连接查询。rank
从 1 开始，score 沿用最终排序阶段。

seed_entities 为 GraphNodeReference (id、relations)。只对最终返回 Chunk 中的实体批量补充全图真实可见连接；relations 为 null
表示未配置图库，[] 表示没有确认到连接，非空列表包含当前已授权关系支持的谓词/方向，同类型入/出兼有及自环为 both。不继承正文业务
filters，不修改核心 GraphNode.relations 或 metadata 中的本体提示。完整定位使用 get_neighbors (limit=None)
，关系来源分批授权；不承诺发现尚未投影的 Mongo 关系。可以据此显式调用[图 traversal](usecases-graph.md)。向量、PreparedChunk
和生成上下文拼接文本不进入正文结果。

## 4. 过滤与失败合同

过滤使用[通用条件模型](models.md)，Qdrant 在召回预算前执行平面字段条件。业务工具映射参数即可，不在授权后再实现过滤解释器。普通用例不额外保证同
revision 被直接改写业务字段后的投影新鲜度；业务字段变更提高 version。

隐藏、destroyed、旧 revision、缺失 Chunk 和错误定位省略，过滤后不补召回。空候选不调用重排。重排异常保持原顺序降级，召回、embedding、授权读取或
MMR 缺向量等异常传播。

Chunk 图事实损坏会记录警告并返回空 seed_entities，不撤销正文命中；已启用的全图连接读取或关系来源解析错误直接传播，不能当作空连接；图用例自身的事实解析错误也传播。MMR
使用已存向量，不重新 embedding 正文。结果不裁剪 token，不做父块扩张或结果缓存；调用方根据 section_id
继续[目录与章节阅读](usecases-reading.md)。

验证入口：[test_retrieval.py](../tests/application/test_retrieval.py)。
