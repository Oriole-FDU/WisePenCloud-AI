# Knowledge Navigation Tool Contract

## 公开接口

三个工具各自只有一套语义，使用扁平 JSON Schema，禁止 additional properties：

```python
knowledge_navigate_locate(query: str, max_results: int = 10)

knowledge_navigate_sections(
    state_id: str,
    resource_id: str,
    section_ids: list[str],
)

knowledge_navigate_expand(
    state_id: str,
    node_ids: list[str],
    query: str | None = None,
    relation_types: list[str] = [],
    direction: Literal["in", "out", "both"] = "both",
    max_depth: int = 1,
    max_results: int = 10,
)
```

user、session、group roles、ACL、版本和后端参数由系统注入。

## `locate`

1. 使用 query 调用 Qdrant dense/BM25 hybrid retrieval。
2. 过滤 applied revision 和本地 ACL。
3. 同一 Section 的多个 RetrievalChunk 只保留排名最高的命中。
4. 返回 SectionView：当前 Section 摘要、命中 ReadingBlock、SourceRef evidence 和轻量标题树 frontier。
5. 解析命中 chunk 中已有的跨文档 Entity mention。
6. 创建 Redis state，保存当前节点和已返回的 Section frontier IDs。

Section frontier 只包含 `section_id/title/path/summary/has_content`，不包含邻接正文。

## `sections`

| 字段 | 规则 |
|---|---|
| `state_id` | 必填，必须绑定当前 user/session |
| `resource_id` | 必填，作为 ACL 资源边界 |
| `section_ids` | 1 到 12 个，必须来自同一 state 返回的 Section ID |

执行：

1. 校验 state binding 和 Section ID 是否已暴露。
2. 读取当前 Resource 的 applied SectionNode 和全部 ReadingBlock。
3. 重新执行本地 ACL 最终授权。
4. 返回完整 Section 正文块和 parent/previous/next/children frontier。
5. 将新发现的 frontier Section ID 原子加入 state。

这个工具只解决文档内结构多跳，不访问 Neo4j，也不接受关系类型、方向或深度参数。

## `expand`

`expand` 只解决跨文档实体关系：

1. 校验 state binding 和 Entity node IDs。
2. 在 Neo4j 按 relation、direction、depth、applied revision 和 ACL 遍历。
3. 排除已访问 target，保留连接新 target 所需的路径。
4. 远端 SourceRef 物化为 SectionView。
5. 用 Redis 原子加入新 Entity node IDs。

`expand` 不读取标题树 frontier，也不发起新的全库 RAG。

## SectionView 返回结构

```json
{
  "resource_id": "resource-1",
  "document_version": 3,
  "section_id": "rsec_xxx",
  "title": "核心概念",
  "section_path": ["课程", "核心概念"],
  "summary": "该节说明核心概念及其使用方式",
  "reading_blocks": [
    {
      "reading_block_id": "rsb_xxx",
      "ordinal": 0,
      "content_index": 0,
      "content_start": 120,
      "content_end": 620
    }
  ],
  "evidence": [
    {
      "ref_id": "rsrc_xxx",
      "content_index": 1,
      "content_start": 220,
      "content_end": 280
    }
  ],
  "frontier": {
    "parent": {"section_id": "rsec_parent", "title": "课程"},
    "previous": null,
    "next": {"section_id": "rsec_next", "title": "例子"},
    "children": []
  }
}
```

所有 `content_index` 继续遵守 ToolReturn / ToolContentStore 契约。frontier 节点不生成正文 receipt。

## 错误

| code | retryable | 条件 |
|---|---:|---|
| `knowledge_navigation_invalid_request` | false | 参数为空、重复或超出 schema |
| `knowledge_navigation_state_not_found` | false | state 不存在或 binding 不匹配 |
| `knowledge_navigation_state_invalidated` | false | Section/Entity 不在当前 state |
| `knowledge_navigation_backend_unavailable` | true | Mongo/Qdrant/Neo4j/Redis 不可用 |

## Contract 测试

- 三个 schema 互不携带对方专属参数，不使用 `oneOf`/`anyOf`。
- locate 命中多个子块时只返回一个 SectionView。
- sections 能读取长 Section 的多个有序 ReadingBlock。
- frontier 只返回结构元数据，不泄漏邻接正文。
- Section ID 必须来自 state，错误 user/session 一律拒绝。
- 图 expand 仍只返回跨文档关系和其证据 SectionView。
- 每个正文 `content_index` 能定位 ToolReturn 的 `contents` 或 `content_receipts`。
