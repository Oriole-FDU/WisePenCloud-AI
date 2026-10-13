# 图检索、一跳探索与授权证据

入口是 [GraphRetrieval](../src/rag/application/graph/retrieval.py) 和 [GraphTraversal](../src/rag/application/graph/traversal.py)。图身份及本体见[模型](models.md)，种子召回与应用准备的职责见[抽象](abstractions.md)。

## 1. 依赖与最小调用

GraphRetrieval 注入 SnapshotReader、GraphReader、EmbeddingClient、所选路线 VectorReader，以及可选 Fusion/Reranker。GraphTraversal 只注入 SnapshotReader/GraphReader，不调用模型。平台图用例需要启用 Neo4j，但查询不注入 Ontology。

```python
from rag.core.schema.filters import MetadataFilter, MetadataFilters
from rag.retrievers.graph import GraphRetrievalMode
from rag.storage.graph import GraphDirection


async def search_and_follow(retrieval, traversal, snapshot):
    filters = MetadataFilters([MetadataFilter("ontology_name", "employment")])
    hits = await retrieval.retrieve(
        "李明的任职关系", snapshot=snapshot,
        mode=GraphRetrievalMode.HYBRID, filters=filters,
    )
    ids = [node.id for hit in hits for node in hit.evidence.entities]
    evidence = await traversal.traverse(
        ids, snapshot=snapshot, direction=GraphDirection.OUTGOING,
        relation_types=["in_department"], filters=filters,
    )
    return hits, evidence
```

实际工具应由调用方根据返回事实选下一跳种子；上例仅演示同快照、同条件传递，不表示内部自动多跳。

## 2. GraphRetrieval 三模式与参数

| mode | 种子与证据范围 |
|---|---|
| local | 实体来源向量种子；授权确认实体，再按指定方向扩展一跳 |
| global | 关系来源向量种子；保留该关系和支持它的同一 Chunk 中两端描述，不收集组外端点来源 |
| hybrid（默认） | 两路并发、默认 RoundRobin；相同逻辑种子合并合法来源，不扩大单组范围 |

图 hybrid 只融合实体与关系，不包含普通 Chunk 召回，也不是尚未实现的 mix。query 直接 embedding 一次，不生成关键词。

| 参数 | 默认/约束 |
|---|---|
| query / snapshot | 必填，query 非空白，snapshot keyword-only |
| top_k | 10，正数 |
| focus / diversity | 空字符串回退 query；off/moderate/high，默认 off |
| candidate_limit | 60，必须 ≥ top_k |
| entity_top_k / relationship_top_k | None；hybrid 各路默认 30；单路拒绝路内预算，使用 candidate_limit |
| neighbor_direction | None → BOTH |
| neighbor_relation_types | None → 不限制谓词；空列表表示无谓词可匹配；列表值不能空白 |
| neighbor_limit | 100，正数，本次全部实体种子共享的一跳逻辑关系预算 |
| filters | None → 不限定本体/资源；支持范围见下节 |

单路只要求所选路线 reader。来源候选以种类和来源记录 ID 融合；授权后按 kind/fact_id 形成逻辑证据组，首个合法候选提供组的向量和初始分数。

## 3. 当前 metadata 筛选合同

**图用例只支持三个字段，不支持任意业务 metadata 或数值范围。**

| 字段 | 值从哪里核对 | 操作 |
|---|---|---|
| resource_id | 当前授权 Chunk.resource_id | 字符串 EQ / 非空字符串列表 IN |
| content_revision | 当前授权 Chunk.content_revision | 字符串 EQ / 非空字符串列表 IN |
| ontology_name | 当前实体事实的 ontology_name | 字符串 EQ / 非空字符串列表 IN |

支持递归 AND/OR/NOT，条件组非空，NOT 恰好一个子条件。底层 adapter 可以表达更多条件，不代表图应用证据加载支持这些条件；使用上述合同构造参数，避免 year、employee 或范围运算。超出合同的条件不保证在用例入口提前拒绝，可能在后端或当前事实核对中报错。

同一 filters 传入向量召回、拓扑来源读取及当前事实核对。后端在候选/逻辑关系预算前筛选，授权或过期丢弃后不补召回。ontology_name 从实体事实核对，不从业务 metadata 或 ID 字符串猜测；关系的同来源两端必须满足条件。

本体选择是事实命名空间，不证明来源属于某业务包。共享本体的其他可见资料也能贡献事实，需要缩小范围时组合 resource_id/content_revision。未传 filters 可读取多个本体，不加载查询侧本体 Registry。

## 4. GraphTraversal 参数

`traverse(node_ids, *, snapshot, direction=None, relation_types=None, relation_limit=100, filters=None)` 返回 list[GraphEvidence]。node_ids 为逻辑实体 ID，去重保序；不能包含空白 ID，空列表直接返回空。方向 None 默认 BOTH，关系预算为正数，谓词同检索约束。

至少一个可见的当前 Mongo 来源确认种子后才读一跳；隐藏或过期来源不能为种子背书。所有确认的实体种子共用一次 get_neighbors 调用，relation_limit 限制这次读取的逻辑关系总量，选中关系的匹配来源完整返回；不是逐种子独立预算。没有 depth 参数，不自动递归。GraphRelationOption 是写入时的类别允许提示；即使提示允许 in_department，当前可见关系仍可能为空。无关系的合法实体保留，下一跳由调用方重新选择。

## 5. 证据与身份核验

公开结果及共享构建类见 [evidence.py](../src/rag/application/graph/evidence.py)。

| 对象 | 内容 |
|---|---|
| GraphHit | evidence、rank、score；沿最终排序语义 |
| GraphEvidence | kind、fact_id、entities、relationships、sources；一组逻辑种子的独立来源证据 |
| GraphEntity | id、source_id（来源 Chunk）、description、relations；来源描述不合并 |
| GraphSourceChunk | chunk/resource/revision、section_id/path、完整 raw_text；不携带任意业务 metadata |
| relationships | GraphEdge；保持原方向、来源列表、谓词和同来源端点 |

向量候选核对 record_id、来源资源/revision、当前事实存在、fact_sha256 和 graph_version。来源经 SnapshotReader 授权，拓扑投影也回查当前事实，投影 JSON/聚合描述不能代替正文权威。EvidenceBuilder/GraphEvidenceLoader 在单次调用中共用来源读取，但组内范围独立。返回同时包含描述、热度（来源数）与遍历提示，字段偏重；按需投影见[结果消费模式](usecases-consumption.md)。

## 6. 失败与维护

隐藏/缺失/旧 revision、错误定位、过期图向量省略，不补召回。无拓扑不能否定已确认的合法 Mongo 向量实体种子。两个图列表均缺失表示无图事实；部分缺失、结构损坏、来源不符等解析错误传播。重排失败沿原顺序降级，其他存储/模型错误传播。

旧向量缺 fact_sha256 会被丢弃；旧 Neo4j 来源缺 ontology_name 不匹配本体 EQ/IN。用[图 repair](operations.md) projection-only 重建相应投影。旧 Mongo 实体缺关系提示时读为 []；仅投影不能补提示，需要正常抽取 repair 重算旧缓存。

结果不裁剪文本、不缓存材料、不实现父块。真实模型/后端尚需单独联调，替身验收入口：[图检索 tests](../tests/application/graph/test_graph_retrieval.py) 和 [traversal tests](../tests/application/graph/test_traversal.py)。
