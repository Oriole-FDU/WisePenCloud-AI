# `knowledge_navigate` Tool Contract

## 公开接口

只暴露一个工具：

```python
knowledge_navigate(
    action: Literal["locate", "expand"] = "locate",
    query: str | None = None,
    state_id: str | None = None,
    node_ids: list[str] = [],
    relation_types: list[str] = [],
    direction: Literal["in", "out", "both"] = "both",
    max_depth: int = 1,
    max_results: int = 10,
)
```

实际 Pydantic 模型使用 `default_factory=list`。additional properties 一律拒绝。user、session、group roles、ACL、版本和后端参数由系统注入。

## `locate`

| 字段                        | 规则           |
|---------------------------|--------------|
| `action`                  | `locate` 或省略 |
| `query`                   | 必填、trim 后非空  |
| `state_id`                | 禁止           |
| `node_ids`                | 必须为空         |
| `relation_types`          | 必须为空         |
| `direction` / `max_depth` | 禁止显式传入       |
| `max_results`             | 1 到 20       |

执行：

1. 使用原始 query 调用共享 `RagCandidateRetriever`。
2. 将 hit 绑定 SectionNode，构造当前章节、祖先导语、前置同级和子章节入口。
3. 返回 RAG source preview、section context、locator 和 `content_ref`。
4. 将 hit 中的概念 mention 解析为可导航 Concept node。
5. 创建 Redis state，保存 root query 和已返回 node IDs。

一个 RAG hit 没有可靠 Concept 时仍可作为 source 返回，但不能伪造 concept node。零结果也返回有效 state，并标记
`exhausted=true`。

## `expand`

| 字段               | 规则                      |
|------------------|-------------------------|
| `action`         | 必须为 `expand`            |
| `state_id`       | 必填                      |
| `node_ids`       | 1 到 16 个，必须已由该 state 返回 |
| `query`          | 可选，只作为本轮 focus          |
| `relation_types` | 最多 16 个，必须来自注册枚举        |
| `direction`      | 默认 `both`               |
| `max_depth`      | 1 到 2                   |
| `max_results`    | 1 到 20                  |

执行：

1. 校验 state 的 user/session，并确认 node ID 已在 `known_nodes` 中。
2. 在 Neo4j 中按 relation、direction、depth、applied revision 和 ACL predicate 遍历。
3. 排除已访问 target，保留连接新 target 必需的旧中间节点。
4. 按 root query、local focus、relation prior、path coherence、source overlap 和 hub penalty 排序。
5. 将远端 SourceRef 绑定其 SectionNode，并恢复远端局部标题语境。
6. 返回新节点、边、路径及其 evidence sources。
7. 用 Redis `SADD` 把本次新 node IDs 加入 `known_nodes`。

`expand` 不自动发起新的全库 RAG。当前概念没有邻接时返回 `exhausted=true`。

## 返回结构

```json
{
  "state_id": "kns_xxx",
  "action": "expand",
  "root_query": "...",
  "focus": {
    "query": "...",
    "node_ids": ["kn_xxx"]
  },
  "nodes": [],
  "edges": [],
  "paths": [],
  "sources": [],
  "navigation": {
    "visited_nodes": 12,
    "frontier_nodes": 7,
    "truncated": false,
    "exhausted": false
  }
}
```

Node 使用判别联合：

```text
concept        -> node_id, label, available_relations
resource       -> node_id, resource_id, available_relations
external_source -> node_id, label, source_locator, available_relations
```

Edge：

```text
edge_id, source_node_id, target_node_id,
relation_type, direction, evidence_ref_ids, qualifiers
```

Source：

```text
ref_id, resource_id, document_version,
content_ref, content_start/end,
page_label, section_id, section_path, preview,
section_context
```

`section_context`：

```text
current_source,
ancestor_preambles[],
previous_sibling?,
children[],
truncated
```

其中每个携带正文的 item 都是可回读的 SourceRef；children 默认只返回标题，相关项才附短 preview。

`direction` 是相对本次 focus 的返回字段，不重复存成第二种 inverse relation。`available_relations` 只统计 ACL 过滤后的邻接。

## 内容与预算

- preview 直接截取原文，不用模型摘要替换。
- 长内容写入现有 `ToolContentStore`，返回 `cnt_*`。
- state TTL 不长于 content TTL。
- 预算按 source、node、edge、path 整项裁剪，禁止截断序列化后的 JSON。
- 裁剪后设置 `truncated=true`。

## 排序

使用现有 `RankingPipeline` 注册 navigation preset，不再实现第二套 RRF。

信号：

```text
root query relevance
local focus relevance
relation type prior
path coherence
evidence/source overlap
novelty
depth and hub penalty
```

tie-break 固定为 `(score desc, depth asc, node_id asc)`。

## 错误

| code                                       | retryable | 条件                                  |
|--------------------------------------------|----------:|-------------------------------------|
| `knowledge_navigation_invalid_request`     |     false | action 字段组合非法                       |
| `knowledge_navigation_state_not_found`     |     false | state 不存在或 binding 不匹配              |
| `knowledge_navigation_state_invalidated`   |     false | focus 已不在当前 applied graph 或被 ACL 过滤 |
| `knowledge_navigation_content_unavailable` |      true | `ToolContentStore` 写入失败             |
| `knowledge_navigation_backend_unavailable` |      true | 所需 ES/Qdrant/Neo4j/Redis 不可用        |

随机 state ID、错误 user/session 和不存在 state 返回相同外观，避免状态探测。

## Contract 测试

- action 组合和边界值。
- 不接受模型传入 ACL、user、scope 或 projection revision。
- node ID 必须来自当前 state。
- max depth 在 schema 和 repository 双重限制为 1 到 2。
- in/out/both 和 relation filter 返回正确路径。
- 重复 expand 不重复返回 target。
- 不可读中间节点使整条路径消失。
- 每个 source 的 `content_ref` 可读取且 offset 对齐。
- RAG hit 和图远端 SourceRef 生成同一形状的 section context。
- section context 中每个正文 item 都能回到原始 Resource/version/span。
- 输出预算永远保留完整 JSON item。
