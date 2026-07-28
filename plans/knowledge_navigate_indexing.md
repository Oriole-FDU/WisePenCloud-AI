# 文档导航索引

## 输入契约

正文唯一入口：

```text
DocumentReadyMessage(resourceId, version, content)
  -> RagDocumentReadyConsumer
  -> Markdown blocks -> Section/Page/Anchor spans
  -> paginated page leaf / flowing structure leaf
  -> Mongo Section/Chunk -> Qdrant dense/sparse 内容索引
  -> Neo4j 跨文档实体关系投影
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
- Entity mention ID 使用 resource、version、chunk 和 span。
- canonical Entity ID 使用 knowledge scope、entity type 和 canonical key。
- EntityResolver 负责把 mention 绑定到 canonical KnowledgeEntity。

## 图中保存什么

Neo4j 只保存跨文档导航需要的节点和关系：

- `Resource`：Java Resource 的派生引用，用于连接来源和执行 ACL；
- `KnowledgeEntity`：由不同 Resource 中的人物、组织、产品、技术、方法、数据集、事件、地点、文档或概念 mention 解析得到；
- `ExternalSource`：正文明确引用的外部来源；
- core profile 的组成、使用、依赖、来源、因果、比较等通用关系；
- learning profile 的定义、解释、例子和前置知识关系；
- scholarly profile 的论文、作者、方法、数据集和引用关系。

RAG hit 的 resource、heading path、page/offset 和 locator 直接写入 `SourceRef`，作为 Entity 和 relation evidence。

## Projection Checkpoint

chat-service 只建立自己的派生索引提交点：

```python
class RagProjectionCheckpoint:
    resource_id: str
    staged_content_revision: str | None
    staged_document_version: int | None
    staged_content_hash: str | None
    applied_content_revision: str | None
    applied_document_version: int | None
    applied_content_hash: str | None
```

Mongo checkpoint 是正文查询的可见性门。Neo4j ResourceNode 单独保存
`content_projection_revision` 和 `applied_relation_revision`；查询关系时两者必须与 evidence edge 的 revision 一致。

写入顺序：

```text
1. 读取 resourceId/version/content，解析 Markdown 并计算 content_hash
2. 构建 Section/Page/Anchor、retrieval child 和 SourceRef
3. 读取 checkpoint，直接跳过陈旧或已 applied 的重复事件
4. 读取 Resource ACL 投影
5. 按 child 的局部文档语境生成 Contextual Indexing；缓存命中时跳过 LLM
6. 写 Mongo staged sections/retrieval chunks/span mappings/SourceRefs
7. 按 contextual index_text 复用或生成 dense vector，写 Qdrant dense/BM25 point
8. 切换 Mongo applied content revision，清理旧 Qdrant revision
9. 抽取跨文档通用实体与启用 profiles 的关系，关系记录绑定 source content revision
10. 校验 edge endpoint/evidence 后切换该 Resource 的 applied relation revision
```

Mongo Section/Chunk、Qdrant 全部通过后切换 applied content revision。相同 `(resource_id, version, content_hash)`
重复消息为 no-op。关系投影异步生成，但查询只接受 `source_content_revision` 等于当前 applied content revision 的边。

## 高频更新

Mongo 结构投影按 revision 完整重建。Contextual Indexing、Embedding 和关系抽取按各自输入指纹增量复用：

```text
context_key = hash(prompt version + query model + thinking mode
                   + child content hash + local document context hash)
embedding_key = hash(embedding model + contextual index_text)
extraction_key = hash(query model + extraction contract
                      + relation schema + rendered extraction window)
```

- Contextual Indexing cache 命中：直接把缓存上下文加入 `index_text`，不调用 LLM。
- Qdrant 中存在相同 `embedding_key`：直接复用 dense vector。
- extraction cache 命中：替换为当前 chunk ID，并重新定位 evidence quote 和 SourceRef。
- 标题修改、章节移动或相邻上下文变化：对应 key 变化，只重算受影响窗口。
- 章节内部更新：Mongo 重建 revision；未变化 leaf 的 embedding/LLM 结果继续复用。
- paginated 文档页面内容变化：重建该 page leaf；跨页 Section 的结构范围随 revision 更新。
- chunk 删除：新 revision 移除以该 chunk 为 evidence 的关系。
- Contextual Indexing 或关系抽取失败：保留 Kafka offset，不创建或切换不完整的正文 revision。
- 两类 LLM cache 都使用 Redis TTL；关系抽取 key 的 rendered window 已包含 resource ID。

内容 projection 先 applied，实体关系异步完成；新 relation revision ready 前该 Resource 使用普通 RAG，不继续暴露旧版本
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
- ACL projection 在 Qdrant、Neo4j 使用完整 group role scope；正文物化前再以 Mongo ACL 投影授权。

## 运行依赖

Qdrant sparse vector 使用服务端内置的 `qdrant/bm25`。Python client 以 `cloud_inference=True` 发送 `Document`，该参数在 SDK 中表示把文本推理交给远端 Qdrant；Chat Service 不安装 FastEmbed，也不下载 BM25 模型。

查询使用一个 Qdrant Query API 请求：dense 与 BM25 分别进入 `Prefetch`，两个 prefetch 使用相同 ACL filter，主查询通过 `FusionQuery(RRF)` 生成候选顺序。融合结果继续进入现有 `RankingPipeline` 的 reranker 和 MMR。
