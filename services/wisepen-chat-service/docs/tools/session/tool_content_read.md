# tool_content_read

实现入口：`src/chat/application/tools/session_tools/tool_content_read_tool.py`

`tool_content_read` 从 `ToolContentStore` 中读取已有 `cnt_*` 内容。它用于上一轮工具返回 `<content_receipt>` 后，对一个或多个内容做跨文档语义检索或正则匹配。

## 何时使用

- 工具输出太长，模型只拿到了 `cnt_*` receipt。
- 需要在一个或多个缓存内容中按自然语言问题找相关窗口。
- 需要在一个或多个缓存内容中用正则找精确模式，例如 ID、URL、标题、姓名或引用片段。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `content_ids` | `string[]` | 必填，1 到 64 个 `cnt_*`。同一次调用共用同一组读取参数，超单批时工具内部自动分批。 |
| `mode` | `string` | 必填，`ranked_expand` 或 `regex_match`。 |
| `selector` | `object` | 可选，按结构元数据预筛选 chunk。 |
| `query` | `string` | `ranked_expand` 必填。 |
| `top_k` | `integer` | `ranked_expand` 返回全局排序后的 match 数，默认 5。 |
| `pattern` | `string` | `regex_match` 必填，Python 正则，最长 500 字符。 |
| `max_matches` | `integer` | `regex_match` 跨全部 `content_ids` 的最大匹配窗口数，默认 10。 |
| `merge_before` / `merge_after` | `integer` | 对中心 chunk 前后扩展的 chunk 数。 |

`selector` 支持 `unit_types`、`sections`、`pages`、`anchors`、`chunk_indices` 和 `include_unknown`。多个 selector 组之间取交集。

执行上下文必须包含 `session_id`。

## 输出

返回 `ToolContentReadResult`：

- `mode`: 本次读取模式。
- `matches`: 跨全部 `content_ids` 的全局有序命中列表。
- `matches[*].content_id`: 该命中来自哪个 `cnt_*`。
- `matches[*].window`: 命中的窗口文本与定位信息。
- `failed`: 单项不可读、过期或不存在的 `content_id` 列表。

## 边界

- 只读取已有 `cnt_*`，不会创建新的 content receipt。
- 只能读取同一 `session_id` 下的内容；不存在、过期或跨会话内容会按单项失败返回。
- `ranked_expand` 用 ranking engine 对全部候选 chunk 做跨文档全局排序，不访问外部搜索源。
- `regex_match` 是跨文档精确模式匹配，不适合模糊语义搜索。
- 单个窗口最多 20,000 字符，超过会截断。
- 需要按 offset 顺序继续读单个内容时，使用 `tool_content_sequential_read`。
