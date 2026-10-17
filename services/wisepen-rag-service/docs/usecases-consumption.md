# 用例结果的轻量消费：投影与延迟读取

检索用例的返回面向“完整证据 + 授权原文”，所以字段偏重（[ChunkHit](usecases-vector.md)、[GraphRetrievalResult](usecases-graph.md)）。但消费方（模型工具、上层编排）通常只用到其中一部分信号。本页给出几种正交的轻量化模式，它们都在**已授权结果之上做纯投影**，不改变快照、不绕过正文回查、不产生新事实。

GraphTraversal 返回共享的 GraphTraversalResult(entities、relationships)，与图检索共用 GraphEntity/GraphRelationship 应用模型，不附带原文。逻辑对象已经收拢，各来源描述仍独立保存在 sources 中。下面按实体进行的投影也可以用于 traversal 结果；需要原文时沿 sources[].chunk_id 显式授权读取。

## 1. 四类可分离的消费信号

| 信号 | 图证据来源 | 正文命中来源 | 典型用途 |
|---|---|---|---|
| 展示 | GraphRetrievalResult.entities 中各 sources[].description | ChunkHit.text | 直接喂给模型或前端 |
| 来源数 | len(entity.sources) | — | 多来源支撑度、排序/置信 |
| 遍历 | GraphRetrievalResult.relationships；实体以 id 作为下一跳种子 | ChunkHit.seed_entities | 选择下一跳、显式图遍历 |
| 定位 | sources[].chunk_id | ChunkHit.chunk_id | 延迟读取原文 |

一个用例返回里同时包含这四类信息，因此“重”。按需只保留其中一两类，就能把返回降到模型工具真正需要的体积。

## 2. 模式 A：实体优先（热度 + 遍历，不要原文）

真实业务若只想“找到实体，再显式 traversal”，可以只暴露 id、描述、当前上下文中的来源数，不消费 chunks 原文。source_count 只统计返回事实的独立来源，不表示全量关系数或模型置信度。

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class EntityOption:
    id: str
    descriptions: list[str]
    source_count: int  # 热度信号：当前可见来源数


def project_entities(result) -> list[EntityOption]:
    return [
        EntityOption(
            entity.id,
            [source.description for source in entity.sources if source.description],
            len(entity.sources),
        )
        for entity in result.entities
    ]
```

同一逻辑实体只返回一条 GraphEntity，直接统计 sources 的长度，描述保持独立。投影不需要新的授权读取，直接用合法结果。

## 3. 模式 B：仅遍历信号（连描述也不要）

如果下一步只喂给 [GraphTraversal](usecases-graph.md)，可以进一步只保留 id。图实体结果不再暴露核心模型的本体允许提示；真实连接可从返回的 relationships 或最终 ChunkHit.seed_entities.relations 获取（后者 null 为图未启用，[] 为无确认到的连接）。traversal 仍按本次快照确认来源。

```python
seed_ids = [entity.id for entity in result.entities]
topology = await traversal.traverse(seed_ids, snapshot=snapshot, direction=GraphDirection.OUTGOING)
```

正文命中一侧同理：ChunkHit.seed_entities 只携带 `GraphNodeReference(id, relations)`，可据此显式调用图 traversal，无需消费 text。

## 4. 模式 C：延迟读取原文（只留定位）

模型先拿到实体/命中，再按需取原文，往往比一次返回全部原文更省。只保留 chunk 定位，需要正文时用 [DocumentReading.read_chunks](usecases-reading.md) 回查；与首次检索**共享同一快照**，仍经资源授权与 revision 核对。

```python
texts = await reading.read_chunks(
    list(dict.fromkeys(
        source.chunk_id for entity in result.entities for source in entity.sources
    )),
    snapshot=snapshot,
)
```

ChunkText 直接透传 chunk_id/section_id/section_path/text，不重建标题、不拼接上下文。定位来自图或向量结果，原文权威始终以 Mongo 当前授权正文为准。

## 5. 边界与不变量

- 投影只做字段裁剪或分组，不新增事实；不要把多条 description 拼成一段“权威正文”。
- 出处若需要正文，必须经 read_chunks / read_sections 回查，投影对象中的描述不等于原文。
- 一次上层编排共享同一 snapshot；跨调用重新观察版本与 ACL。
- source_count 是“当前授权可见来源数”，不表示全量历史来源；隐藏/旧 revision 来源不参与计数。
- 各文档的授权、版本与失败合同不变，投影不改变它们。

相关：[向量检索](usecases-vector.md)、[图用例](usecases-graph.md)、[结构化阅读](usecases-reading.md)。
