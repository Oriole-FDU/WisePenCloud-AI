# get_historical_chat_messages

实现入口：`src/chat/application/tools/session_tools/get_historical_chat_messages_tool.py`

`get_historical_chat_messages` 在当前会话的历史消息中做关键词全文检索。它默认不暴露给模型，只有请求级工具可见性显式开放时才可调用。

## 何时使用

- 当前上下文窗口缺少早先对话里的事实、事件或细节。
- 用户问题明确依赖历史对话内容。
- 关键词应尽量使用和用户问题相同的语言；无结果时可尝试切换关键词语言。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `keyword` | `string` | 必填，历史消息检索关键词。 |
| `start_time` | `string` | 可选，ISO 8601 起始时间。非法格式会被忽略。 |
| `end_time` | `string` | 可选，ISO 8601 结束时间。非法格式会被忽略。 |
| `limit` | `integer` | 可选，最大返回条数，默认 10。 |

`session_id` 不出现在 schema 中，必须由系统从执行上下文注入。

## 输出

返回字符串：

- 无结果时返回明确的 no historical chat message 提示。
- 有结果时按消息角色和创建时间列出命中的消息内容。
- 超过 `TOOL_RESULT_MAX_CHARS` 会被截断并追加 `[truncated]`。

## 边界

- 只查当前 `session_id` 的历史消息，不能由模型传入或伪造 session。
- 不做语义搜索，只做 repository 提供的文本检索。
- 不读取工具缓存 `cnt_*`，也不读取临时文件 `tfile_*`。
- 检索失败会包装为 retryable 的 `history_search_failed`。
- 默认超时 5 秒。
