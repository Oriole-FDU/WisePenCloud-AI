# RAG HTTP 查询接口

HTTP 是 application 用例的薄传输适配，统一在 `/rag` 前缀下使用登录上下文、`R` 响应包装和 Common 全局异常处理。请求不允许传入用户、群组角色或 ACL；服务从网关写入的 `SecurityContextHolder` 构造 `PermissionScope`。

## 当前接口

| 路径 | 用途 | 请求核心字段 |
| --- | --- | --- |
| `POST /rag/retrieval/searchHybrid` | 文档 Dense/BM25 混合检索 | `semantic_query`、`lexical_query`、`top_k` |
| `POST /rag/reading/readPages` | 按真实 Page label 读取 | `resource_id`、`page_labels` |
| `POST /rag/reading/readSections` | 按全局 Section ID 读取 | `section_ids`、`mode`、`max_depth` |
| `POST /rag/reading/getNeighborhood` | 批量 Section 邻域目录 | `section_ids`、`sibling_steps` |
| `POST /rag/reading/getGlobalOutline` | 文档全局目录 | `resource_id`、`max_level` |

Page、Section 和 Neighborhood 每次最多请求 20 项；`sibling_steps` 限制为 0 到 5。`max_level=0` 表示展开所有标题层级。所有正文保持 application 返回的完整内容，不在 HTTP 层截断。

## 响应与可见性

`searchHybrid` 返回相关性判定和动态父块。HTTP 响应只保留模型消费所需的 `resource_id`、单个 `section_id`、`text` 和 `score`；命中 Chunk 指针、父块 ID、`content_revision`、`matched_chunk_ids`、`source_spans` 和 Python 字符 offset 均属于服务内部事实，不对外暴露。

`getNeighborhood` 的每个 item 只返回 `resource_id`、`section_id`、`section_path` 和 `outline`。当前标题已包含在 `section_path` 最后一段及 outline 的 `[C]` 行中，不再重复返回 `title`；长度、页码和锚点只保留在 outline 文本中。

读取和目录接口将资源不存在、旧 revision ID、缺少 ACL、无权限和未知页/Section 统一视为“资源不存在或不可访问”。这既避免泄露资源状态，也不为无页码文档创建虚构 Page。

## 图谱边界

本轮不暴露任何 Graph Search HTTP 接口。`general` 文档没有图谱检索能力，而 `metadata_filter` 是垂域插件的强类型输入，不能以通用 JSON 透传。未来只有在真实垂域插件需要外部图检索时，才单独定义插件所属的 HTTP schema、过滤语义和结果契约。
