# 用例结果的轻量消费：投影与延迟读取

检索用例的返回面向“完整证据 + 授权原文”，所以字段偏重（[ChunkHit](usecases-vector.md)、[GraphHit](usecases-graph.md)）。但消费方（模型工具、上层编排）通常只用到其中一部分信号。本页给出几种正交的轻量化模式，它们都在**已授权结果之上做纯投影**，不改变快照、不绕过正文回查、不产生新事实。

## 1. 四类可分离的消费信号

| 信号 | 图证据来源 | 正文命中来源 | 典型用途 |
|---|---|---|---|
| 展示 | GraphEntity.description | ChunkHit.text | 直接喂给模型或前端 |
| 热度 | len(GraphEvidence.sources) | — | 多来源支撑度、排序/置信 |
| 遍历 | GraphEntity.relations、GraphEvidence.relationships | ChunkHit.seed_entities | 选择下一跳、显式图遍历 |
| 定位 | GraphEntity.source_id | ChunkHit.chunk_id | 延迟读取原文 |

一个用例返回里同时包含这四类信息，因此“重”。按需只保留其中一两类，就能把返回降到模型工具真正需要的体积。

## 2. 模式 A：实体优先（热度 + 遍历，不要原文）

真实业务若只想“找到实体，再显式 traversal”，可以只暴露 id、描述、来源数、可遍历关系，丢弃 sources 原文。source_count 在分组时天然得到，作为**热度信号**（被越多可见来源支撑越可信）。

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class EntityOption:
    id: str
    descriptions: list[str]
    source_count: int  # 热度信号：当前可见来源数
    relations: list[tuple[str, str]]  # 遍历信号：(relation_type, direction)


def project_entities(hits) -> list[EntityOption]:
    grouped: dict[str, dict] = {}
    for hit in hits:
        for entity in hit.evidence.entities:
            acc = grouped.setdefault(
                entity.id, {"descriptions": [], "sources": set(), "relations": set()}
            )
            if entity.description:
                acc["descriptions"].append(entity.description)
            acc["sources"].add(entity.source_id)
            acc["relations"].update(
                (option.relation_type, option.direction) for option in entity.relations
            )
    return [
        EntityOption(id, acc["descriptions"], len(acc["sources"]), sorted(acc["relations"]))
        for id, acc in grouped.items()
    ]
```

同一逻辑实体在多个来源下会产生多条 GraphEntity（一条一个 source_id），按 id 分组的条数即 source_count，描述与关系提示也在此合并。投影不需要新的授权读取，直接用合法结果。

## 3. 模式 B：仅遍历信号（连描述也不要）

如果下一步只喂给 [GraphTraversal](usecases-graph.md)，可以进一步只保留 id；relations 仅作提示，不保证当前可见关系存在。neighbors 由 traversal 按同快照重新确认。

```python
seed_ids = [entity.id for hit in hits for entity in hit.evidence.entities]
evidence = await traversal.traverse(seed_ids, snapshot=snapshot, direction=GraphDirection.OUTGOING)
```

正文命中一侧同理：ChunkHit.seed_entities 只携带 `GraphNodeReference(id, relations)`，可据此显式调用图 traversal，无需消费 text。

## 4. 模式 C：延迟读取原文（只留定位）

模型先拿到实体/命中，再按需取原文，往往比一次返回全部原文更省。只保留 chunk 定位，需要正文时用 [DocumentReading.read_chunks](usecases-reading.md) 回查；与首次检索**共享同一快照**，仍经资源授权与 revision 核对。

```python
texts = await reading.read_chunks(
    [entity.source_id for hit in hits for entity in hit.evidence.entities],
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
