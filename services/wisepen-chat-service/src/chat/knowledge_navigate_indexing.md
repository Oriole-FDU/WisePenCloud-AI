# 文档导航索引

## 输入契约

正文唯一入口：

```text
DocumentReadyMessage(resourceId, version, content)
  -> RagDocumentReadyConsumer
  -> Markdown blocks -> Section/Page/Anchor spans
  -> paginated page leaf / flowing structure leaf
  -> Mongo Section/Chunk -> ES / Qdrant 内容索引
  -> Neo4j 跨文档概念关系投影
```

consumer 直接把 Kafka `content` 交给 Markdown chunker。

SectionTree 直接使用 parser 产生的 heading blocks。retrieval leaf 通过 source spans 关联一个或多个 Section/Page；标题树随内容
projection 一起 staged/applied。底层规则见 [chunking](./knowledge_navigate_chunking.md)，Section
读取见 [title tree](./knowledge_navigate_title_tree.md)。

页码标记由 RAG 消费方定义：

```text
<!-- page 1 -->
```

Python Markdown parser 已按该格式解析 page locator。Java producer 改为输出该 marker，双方用同一 Kafka content fixture
固化契约。

## 节点 ID

```text
kn_<base64url(sha256(namespace | resource | version | node_type | source_key))[:22]>
```

- Resource projection node 使用 resource ID；具体 evidence 仍绑定 document version。
- Concept mention ID 使用 resource、version、chunk 和 span。
- canonical Concept ID 使用 knowledge scope 和 canonical key。
- ConceptResolver 负责把 mention 绑定到 canonical Concept。

## 图中保存什么

Neo4j 只保存跨文档导航需要的节点和关系：

- `Resource`：Java Resource 的派生引用，用于连接来源和执行 ACL；
- `Concept`：由不同 Resource 中的概念 mention 解析得到；
- `ExternalSource`：正文明确引用的外部来源；
- Resource 到 Concept 的定义、解释、例子等有证据关系；
- Concept 之间的依赖、来源、扩展、对比和冲突关系；
- Resource 到 Resource/ExternalSource 的显式引用关系。

RAG hit 的 resource、heading path、page/offset 和 locator 直接写入 `SourceRef`，作为 Concept 和 relation evidence。

## Projection Checkpoint

chat-service 只建立自己的派生索引提交点：

```python
class RagProjectionCheckpoint:
    resource_id: str
    source_document_version: str
    content_hash: str
    staged_content_revision: str | None
    applied_content_revision: str | None
    applied_relation_revision: str | None
    relation_source_content_revision: str | None
    updated_at: datetime
```

content 字段控制 Mongo/ES/Qdrant/SectionTree 可见版本；relation 两个字段只在关系证据校验完成后同时切换。查询关系时要求
`relation_source_content_revision == applied_content_revision`。

写入顺序：

```text
1. 读取 resourceId/version/content，计算 content_hash
2. 生成 staged content revision
3. 解析 Markdown blocks，构建 Section/Page/Anchor source spans
4. paginated 正常页生成 page leaf；flowing 文档按 Section/block 生成 leaf
5. 写 Mongo sections/retrieval chunks/span mappings/SourceRefs
6. 写 Qdrant 和 ES
7. 校验 section/chunk offset、数量和 SourceRef
8. 切换 applied content revision
9. 异步抽取跨文档概念与学习关系，关系记录绑定 source content revision
10. 校验 edge endpoint/evidence 后切换该 Resource 的 applied relation revision
11. grace period 后清理旧派生 revision
```

Mongo Section/Chunk、Qdrant 和 ES 全部通过后切换 applied content revision。相同 `(resource_id, version, content_hash)`
重复消息为 no-op。关系投影异步生成，但查询只接受 `source_content_revision` 等于当前 applied content revision 的边。

## 高频更新

首次索引处理全文；后续版本按窗口增量更新：

```text
chunk_content_hash = hash(raw source spans + index serialization version)
context_hash = hash(section ids + section paths + bounded title context
                    + extractor version + relation schema version)
```

- 正文或 context hash 变化：重新抽取该窗口。
- 标题修改、章节移动或相邻上下文变化：重新抽取受影响窗口。
- 章节内部更新：重建与变化 source span 相交的 leaves 和 SourceRefs；祖先 subtree range 只更新结构投影。
- paginated 文档页面内容变化：重建该 page leaf；跨页 Section 的结构范围随 revision 更新。
- chunk 删除：新 revision 移除以该 chunk 为 evidence 的关系。
- 未变化窗口：复用已验证抽取结果，但重新绑定新 revision 的 node ID 和 SourceRef。
- 抽取缓存键包含 resource namespace。

内容 projection 先 applied，学习关系异步完成；新 relation revision ready 前该 Resource 使用普通 RAG，不继续暴露旧版本
evidence edges。

## Kafka 与清理

| 事件                       | chat-service 行为             |
|--------------------------|-----------------------------|
| `DocumentReadyMessage`   | 重建该 Resource 的内容与导航投影       |
| `AclRecalculateMessage`  | 按现有流程刷新各检索后端 ACL projection |
| `ResourceDeletedMessage` | 只清理消息中 Resource 的本地派生数据     |

chat-service 使用独立 consumer group，并按三个现有事件更新自己的派生投影。

## 必测场景

- 跨 Resource 相同 chunk 内容和位置产生不同 ID。
- 同消息重试幂等。
- 查询结果全部来自 applied revision。
- 新 revision applied 后查询只返回新版本。
- 单 chunk 修改只重抽受影响窗口。
- section 移动会使 context hash 失效。
- 正常 paginated 文档一页一个 leaf，异常超长页只在本页内切分。
- flowing 短 Section 可合箱但 source span 和 locator 不丢失。
- retrieval leaf 的 raw evidence 能从 Kafka `content` source spans 精确重建。
- heading 跳级、重复标题、preface 的 parent/ordinal/subtree range 正确。
- chunk 删除同步移除 evidence relation。
- Java producer 输出 `<!-- page N -->`，Python parser/locator 生成正确 page span。
- ACL projection 在 ES、Qdrant、Neo4j 使用完整 group role scope。
