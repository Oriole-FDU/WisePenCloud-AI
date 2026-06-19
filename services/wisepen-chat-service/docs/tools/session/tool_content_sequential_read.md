# tool_content_sequential_read

实现入口：`src/chat/application/tools/session_tools/tool_content_sequential_read_tool.py`

`tool_content_sequential_read` 按 `offset` 顺序读取单个 `cnt_*` 内容。它只解决“继续读这一个内容”的需求，不做跨文档搜索，也不做全局排序。

## 何时使用

- 已经知道目标就是某一个 `cnt_*`。
- 需要从开头顺序读，或从某个 `offset` 继续读。
- 需要补充某个窗口附近的连续上下文，而不是跨文档找证据。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `content_id` | `string` | 必填，单个 `cnt_*`。 |
| `offset` | `integer` | 可选，默认 0。 |
| `limit` | `integer` | 可选，默认 4000。 |

执行上下文必须包含 `session_id`。

## 输出

返回普通结构化结果：

- `content_id`
- `status`
- `window`

`window` 包含 `text`、offset 范围，以及稳定的页码 / 段落标题 / 小节路径 / 锚点名。

## 边界

- 只读取单个 `cnt_*`。
- 不做语义检索，不做正则匹配，不做跨文档排序。
- 不创建新的 content receipt。
- 不存在、过期或跨会话内容返回 failed 结果。
