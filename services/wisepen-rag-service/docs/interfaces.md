# 暴露接口

RAG 服务对外暴露两类入口：Kafka 消费入口用于维护派生索引，内部 HTTP 接口用于知识导航。Agent tool 不在本服务暴露，而在 `wisepen-mcp-service` 中封装。

## 服务入口

| 路径或名称 | 用途 |
| --- | --- |
| Nacos service `wisepen-rag-service` | 服务发现 |
| `GET /health` | 健康检查，返回 `{status, service}` |
| `GET /docs` | FastAPI OpenAPI 文档 |
| `/internal/rag/knowledge-navigation/*` | MCP 服务调用的知识导航接口 |

HTTP 接口经过 `SecurityHeaderMiddleware` 和登录校验。请求 body 不接收 `user_id` 或群组角色；权限身份来自服务端安全上下文。

## HTTP 知识导航

所有导航接口返回 `R[dict]` 包装。下面只描述 `data` 内的业务字段。

### `POST /internal/rag/knowledge-navigation/locate`

根据自然语言问题创建导航状态，并返回首批相关 Section 与可展开图节点。

请求：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `session_id` | string | 非空 |
| `query` | string | 非空，服务端会 trim |
| `max_results` | int | 1-20，默认 10 |

响应 data：

| 字段 | 含义 |
| --- | --- |
| `state_id` | 后续 `sections`、`cypher` 必须使用的导航状态 |
| `nodes` | 从命中 SourceRef 反查出的知识图谱节点 |
| `sources` | 命中的 SectionView，包含正文 evidence、reading blocks 和 frontier |

### `POST /internal/rag/knowledge-navigation/sections`

读取当前导航状态中已发现 Section 的完整正文，并返回下一层标题树 frontier。

请求：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `session_id` | string | 非空，必须与创建 state 的会话一致 |
| `state_id` | string | 非空 |
| `section_ids` | string[] | 1-12 个；必须已经出现在当前 state 的 known sections 中 |

响应 data：

| 字段 | 含义 |
| --- | --- |
| `state_id` | 当前导航状态 |
| `sections` | SectionView 列表，包含当前 Section 的完整 ReadingBlock |

### `POST /internal/rag/knowledge-navigation/cypher`

从当前导航状态中已发现的图节点继续做有界关系查询。

请求：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `session_id` | string | 非空，必须与创建 state 的会话一致 |
| `state_id` | string | 非空 |
| `node_ids` | string[] | 1-16 个；必须已经出现在当前 state 的 known graph nodes 中 |
| `query` | string \| null | 可选；用于排序路径，不改变图遍历语义 |
| `relation_types` | string[] | 可选，最多 16 个；空表示不限制 |
| `direction` | `"in"` \| `"out"` \| `"both"` | 默认 `"both"` |
| `max_depth` | int | 1-2，默认 1 |
| `max_results` | int | 1-20，默认 10 |

响应 data：

| 字段 | 含义 |
| --- | --- |
| `state_id` | 当前导航状态 |
| `nodes` | 本次结果涉及的去重节点 |
| `edges` | 本次结果涉及的去重关系边，包含 evidence quote 和 SourceRef ID |
| `paths` | 有序路径，只返回能发现新节点的路径 |
| `sources` | 关系 evidence 回源后的 SectionView |

`relation_types` 当前支持：

```text
MENTIONS, ABOUT, RELATED_TO, PART_OF, USES, PRODUCES, DEPENDS_ON,
DERIVED_FROM, IMPLEMENTS, APPLIES_TO, CAUSES, COMPARES_WITH,
CONTRADICTS, EXTENDS, SUPERSEDES, LOCATED_IN, AUTHORED_BY, DEFINES,
EXPLAINS, EXAMPLE_OF, REQUIRES, CITES, PUBLISHED_IN, USES_DATASET,
USES_METHOD, SUPPLEMENTS, RETRACTS
```

### SectionView 摘要

导航接口中的 `sources` 或 `sections` 都使用 SectionView 结构：

| 字段 | 含义 |
| --- | --- |
| `resource_id` | Section 所属资源 |
| `section_id` | Section ID |
| `title` | Section 标题 |
| `section_path` | 标题路径 |
| `preview` | 标题后的直属原文预览，不包含标题 |
| `reading_blocks` | 当前 Section 正文块；frontier 节点不携带邻接正文 |
| `evidence` | SourceRef 回源证据，包含 `content`、`ref_id`、`chunk_id`、`page_labels`、`anchor_labels` |
| `frontier` | `parent`、`previous`、`next`、`children` |

### 错误码

| code | 含义 |
| --- | --- |
| `42001` | 导航参数不合法 |
| `42002` | 导航状态不存在，常见于 state 过期、用户或会话不匹配 |
| `42003` | 导航状态已失效，常见于请求未发现过的 Section 或节点 |
| `52001` | 知识导航服务不可用 |

## Kafka 消费入口

### 文档完成事件

默认 topic：`wisepen-document-ready-topic`

用途：更新内容投影、Qdrant 检索索引和知识图谱投影。

payload：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `resourceId` | string | 非空 |
| `version` | int | >= 1 |
| `content` | string | Markdown 正文 |

### ACL 重算事件

默认 topic：`wisepen-resource-acl-recalc-topic`

用途：从上游读取权威 ACL，更新本地 ACL 投影，并同步到 Qdrant 和 Neo4j。

payload：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `resourceId` | string | 非空 |

### 资源物理删除事件

默认 topic：`wisepen-resource-physical-destroy-topic`

用途：清理该资源在 Mongo、Qdrant、Neo4j 中的 RAG 派生数据。

payload：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `typedResourceIds` | object<string, string[]> | 按资源类型分组的资源 ID；服务会提取并去重全部 ID |

## 不作为公开接口的内容

Mongo、Qdrant、Neo4j 和 Redis schema 是服务内部实现边界，不直接对其他服务承诺。其他服务应通过 Kafka 事实事件和内部 HTTP 导航接口与 RAG 交互；Agent 应通过 MCP tools 使用 RAG，而不是直接调用本服务。
