# 图检索、来源精排与显式一跳探索

入口是 [GraphRetrieval](../src/rag/application/graph/retrieval.py)
和 [GraphTraversal](../src/rag/application/graph/traversal.py)。图身份及本体见[模型](models.md)
，种子召回与应用准备的职责见[抽象](abstractions.md)。

## 1. 依赖与最小调用

GraphRetrieval 注入 SnapshotReader、GraphReader、EmbeddingClient、所选路线 VectorReader，以及可选
Fusion/Reranker。GraphTraversal 只注入 SnapshotReader/GraphReader，不调用模型。平台图用例需要启用 Neo4j，但查询不注入
Ontology。

```python
from rag.core.filters import MetadataFilter, MetadataFilters
from rag.application.graph import GraphRetrievalMode
from rag.domain.models.graph import GraphDirection


async def search_and_follow(retrieval, traversal, snapshot):
    filters = MetadataFilters([MetadataFilter("ontology_name", "employment")])
    result = await retrieval.retrieve(
        "李明的任职关系", snapshot=snapshot,
        mode=GraphRetrievalMode.HYBRID, filters=filters,
    )
    ids = list(dict.fromkeys(node.id for node in result.entities))
    topology = await traversal.traverse(
        ids, snapshot=snapshot, direction=GraphDirection.OUTGOING,
        relation_types=["in_department"], filters=filters,
    )
    return result, topology
```

实际工具应由调用方根据返回事实选下一跳种子；上例仅演示同快照、同条件传递，不表示内部自动多跳。

## 2. GraphRetrieval 三模式与参数

| mode           | 种子与证据范围                                                               |
|----------------|------------------------------------------------------------------------------|
| local          | 实体来源向量种子；保留实体自身的可见来源，补充一跳关系，不追加邻居实体描述   |
| global         | 关系来源向量种子；保留关系可见来源及两端实体的可见来源，不追加端点的其他关系 |
| hybrid（默认） | 两路并发、默认 RoundRobin，合并去重后的共享图上下文和来源 Chunk              |

始终直接 embedding 原始 query 一次，不生成关键词。hybrid 不追加普通全库 Chunk 召回，也不是 mix。核心
GraphNode/GraphEdge、身份与索引文本均不变，无 weight 字段。

| 参数                              | 默认/约束                                                        |
|-----------------------------------|------------------------------------------------------------------|
| query / snapshot                  | 必填，query 非空白，snapshot keyword-only                        |
| top_k                             | 10，正数，只限制最终来源 Chunk 数                                |
| focus                             | 空字符串回退 query，用于 Chunk 精排                              |
| candidate_limit                   | 60，图来源向量候选数，与最终 top_k 单位不同，允许小于 top_k      |
| entity_top_k / relationship_top_k | None；hybrid 各路默认 30；单路拒绝路内预算，使用 candidate_limit |
| neighbor_direction                | BOTH（GraphDirection 枚举；校验时按枚举归一）                    |
| neighbor_relation_types           | None → 不限制谓词；空列表不扩关系；列表值不能空白                |
| neighbor_limit                    | 100，正数，全部 local 实体种子共享的一跳逻辑关系预算             |
| related_chunk_number              | 5，正整数，来源预选最高名额；各路事实名额线性递减到 1            |
| filters                           | None → 不限定本体/资源；支持范围见下节                           |

local 的关联关系沿用 Neo4j 按关系 ID 选择的数量上限，选中关系的匹配来源完整返回；它不是每个种子的配额，也不是来源数或文本长度上限。少数实体仍可能占据主要名额。LightRAG
会读完整一跳并按度数、权重排序，再施加 token 预算；本实现有意保留现有数量预算，不移植该排序或文本预算。

表中的预算与种子约束由调用方保证，HTTP 输入通过 Pydantic 校验；应用内部不重复校验，Common 组件及枚举转换仍执行自身的合同。

图上下文按逻辑 ID 收拢，每个对象的 sources 按来源 Chunk 去重，不拼接或摘要描述。实体来源先用 WEIGHT 策略选择：共同引用次数高的
Chunk 优先、同一来源归较早事实，非空事实名额从 related_chunk_number 递减到 1，剩余名额补给尚有来源的事实。关系路径排除已选实体来源，再以相同策略选择。两路交替合并、按
Chunk ID 去重后，用完整 raw_text 精排。来源预选不使用图事实权重字段，不扩展向量端口获取 Chunk 向量。

模型输入是一份份来源原文；图检索没有 diversity/MMR 参数。未配置精排器时保留来源预选顺序，分数为 0；配置后 score
为最终精排阶段的分数。图上下文不随最终 Chunk top_k 或精排顺序反向删改。不设置精排阈值，不裁剪原文或描述，不管理生成阶段
token 预算。

## 3. 当前 metadata 筛选合同

**图用例只支持三个字段，不支持任意业务 metadata 或数值范围。**

| 字段             | 值从哪里核对                    | 操作                          |
|------------------|---------------------------------|-------------------------------|
| resource_id      | 当前授权 Chunk.resource_id      | 字符串 EQ / 非空字符串列表 IN |
| content_revision | 当前授权 Chunk.content_revision | 字符串 EQ / 非空字符串列表 IN |
| ontology_name    | 当前实体事实的 ontology_name    | 字符串 EQ / 非空字符串列表 IN |

支持递归 AND/OR/NOT，条件组非空，NOT 恰好一个子条件。底层 adapter 可以表达更多条件，不代表图应用证据加载支持这些条件；使用上述合同构造参数，避免
year、employee 或范围运算。超出合同的条件不保证在用例入口提前拒绝，可能在后端或当前事实核对中报错。

同一 filters 传入向量召回、拓扑来源读取及当前事实核对。后端在候选/逻辑关系预算前筛选，授权或过期丢弃后不补召回。ontology_name
从实体事实核对，不从业务 metadata 或 ID 字符串猜测；关系的同来源两端必须满足条件。

本体选择是事实命名空间，不证明来源属于某业务包。共享本体的其他可见资料也能贡献事实，需要缩小范围时组合
resource_id/content_revision。未传 filters 可读取多个本体，不加载查询侧本体 Registry。

## 4. GraphTraversal 参数

`traverse(node_ids, *, snapshot, direction=GraphDirection.BOTH, relation_types=None, relation_limit=100, filters=None)`
返回 GraphTraversalResult。node_ids 为逻辑实体 ID，去重保序；不能包含空白 ID，空输入或无可见种子时返回两个空列表的结果对象。方向默认
BOTH，关系预算为正数，谓词同检索约束。

至少一个可见的当前 Mongo 来源确认种子后才读一跳；隐藏或过期来源不能为种子背书。所有确认的实体种子共用一次 get_neighbors
调用，relation_limit 限制这次读取的逻辑关系总量，选中关系的匹配来源完整返回；不是逐种子独立预算。没有 depth
参数，不自动递归。GraphRelationOption 是写入时的类别允许提示；即使提示允许
in_department，当前可见关系仍可能为空。无关系的合法实体保留，下一跳由调用方重新选择。

## 5. 结果、来源核验与导航

GraphRetrieval 返回 GraphRetrievalResult，三个并列列表：

| 字段          | 内容                                                                                                                 |
|---------------|----------------------------------------------------------------------------------------------------------------------|
| entities      | list[GraphEntity]；每个逻辑实体仅有 id、sources，不返回名称、本体、类别和本体允许提示                                |
| relationships | list[GraphRelationship]；仅 id、sources，完整 ID 保持有向谓词身份                                                    |
| chunks        | list[ChunkHit]；最终精排来源原文、定位、rank/score、seed_entities，以及排除内部 entities/relationships 后的 metadata |

GraphEntity.sources 的每项为 chunk_id、description；GraphRelationship.sources 的每项为
chunk_id、description、keywords、metadata。后两项仍属于各自来源，不做跨来源并集或覆盖。description 可以为 null，相同描述来自不同
Chunk 时仍保留不同来源。实体与关系结果的外层都只有 id/sources，来源定位统一使用 sources[].chunk_id。逻辑对象及其来源均按首次出现保序，同逻辑
ID＋同 Chunk 只保留首份；不同谓词、反向边和自环保持原身份。内部图运算仍使用核心 GraphEdge 的端点与谓词，不从应用结果 ID
反向解析字段。

GraphTraversal 返回 GraphTraversalResult，仅有相同的 entities 和 relationships 应用模型。所有输入种子共享一份拓扑，不按种子包装，也不返回
kind、fact_id 或 Chunk 原文。种子实体先按请求顺序保留各可见来源，再加入实际选中关系两端的同来源实体，最后按逻辑 ID
收拢；不额外读取邻居在其他 Chunk 中的描述，不自动读取下一跳。多种子共享的关系只返回一次，各来源描述放在该关系的 sources 中。

traversal 内部仍读取 Chunk 来授权和确认当前事实；读取不意味着将完整原文附到响应。需要原文时使用 sources[]
.chunk_id，调用 [DocumentReading.read_chunks](usecases-reading.md) 并传入同一 snapshot。应用模型显式序列化 id；不解析 ID
来恢复核心字段，也不保留原始名称的大小写、空白形式。核心 GraphNode/GraphEdge、存储与索引文本均保持原样。

向量候选核对记录 ID、资源/revision、当前事实存在、fact_sha256 和 graph_version。GraphFactLoader 统一按同一 ResourceSnapshot
分批授权和解析 Mongo 当前来源；拓扑投影只负责定位。local 关系的同来源端点必须合法，但不会因此成为结果实体。global
可读取端点的其他可见来源；缺拓扑仍保留已由合法向量或关系来源确认的事实。

最终 Chunk 的 seed_entities 与普通检索使用相同的全图可见连接补充：GraphNodeReference.relations 为 null 表示图未启用，[]
表示没有确认到可见连接，非空列表只含真实关系支持的类型/方向。完整发现使用 get_neighbors (limit=None)
，不继承正文业务筛选或当前图上下文数量预算；只返回类型与方向，不将全图关系正文附到 Chunk。该读取不改变
entities/relationships 的模式边界。

可见连接以图投影能定位且当前 Mongo 来源能确认的事实为准；不扫描全库补齐未投影事实，不承诺跨库原子快照。核心
GraphNode.relations 与 metadata 中的写入事实保持不变。

## 6. 失败与维护

隐藏/缺失/旧 revision、错误定位、过期图向量省略，不补召回。两个图列表均缺失表示无图事实；部分缺失、结构损坏、来源不符等解析错误传播。重排失败沿
Common 原顺序降级，其他存储/模型错误传播。连接提示读取失败也传播，不能伪装为 []；最终 Chunk 自身图 metadata
损坏仍按普通用例记录警告并省略种子。

旧向量缺 fact_sha256 会被丢弃；旧 Neo4j 来源缺 ontology_name 不匹配本体筛选。用[图 repair](operations.md) projection-only
重建投影。此次仅应用契约与读取流程变化，无核心模型或存量数据迁移。

验证入口：[图检索](../tests/application/graph/test_graph_retrieval.py)、[来源预选](../tests/application/graph/test_source_selection.py)、[真实连接](../tests/application/graph/test_chunk_seeds.py)
和 [traversal](../tests/application/graph/test_traversal.py)。
