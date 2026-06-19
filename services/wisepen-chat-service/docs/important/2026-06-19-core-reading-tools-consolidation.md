# 2026-06-19 核心读取工具收敛

## 背景

在本次调整前，会话内容读取链分成三把工具：

- `tool_content_read`
- `evidence_rank`
- `tool_content_batch_read`

这三把工具的边界在设计上有区分，但在实际使用上存在明显重叠：

1. `tool_content_read(ranked_expand)` 已经承担了语义检索能力。
2. `evidence_rank` 只是在已有 `cnt_*` 上再做一次更窄查询的跨文档排序。
3. `tool_content_batch_read` 只负责按精确 `chunk_index` 展开窗口。

随着工具链演进，真正稳定且高频的需求只有两类：

- 在一个或多个 `cnt_*` 上做跨文档检索。
- 在已经确认目标为单个 `cnt_*` 时顺序继续阅读。

因此继续保留三把工具，会让模型和开发者都在“检索、再精排、再展开”之间反复切换，增加误路由和提示词负担。

## 调整内容

本次收敛后，核心读取能力固定为两把工具：

### 1. `tool_content_read`

保留并强化为跨文档检索工具：

- 只支持 `ranked_expand`
- 只支持 `regex_match`
- 支持多个 `content_ids`
- 对多个 `content_ids` 执行全局匹配与全局排序
- 返回全局有序的 `matches`

这意味着：

- 不再支持 `continuous`
- 不再按 `content_id -> windows[]` 分组返回
- 返回结果直接表达“跨文档检索命中”

### 2. `tool_content_sequential_read`

新增为单文档顺序阅读工具：

- 只接受单个 `content_id`
- 只支持 `offset + limit`
- 不做跨文档搜索
- 不做排序
- 只解决“继续读这一个内容”

## 删除项

以下工具已从运行时注册中移除：

- `evidence_rank`
- `tool_content_batch_read`

对应实现、测试入口和文档页也已同步收敛。

## 影响面

### Tool 注册面

`ToolRegistry` 当前 session 工具组变为：

- `tool_content_read`
- `tool_content_sequential_read`
- `get_historical_chat_messages`

### 返回值与 receipt 协议

`content_receipt` 对模型可见 payload 中不再暴露：

- `read_action`
- `read_modes`

原因是它们已经不再承担必要的路由职责，继续暴露只会制造误导。

receipt 现在只保留稳定识别和筛选相关信息，例如：

- `content_id`
- `content_type`
- `content_role`
- `original_length`
- `chunk_count`
- `selectors`

### document_parse 契约

`document_parse` 结果项继续以代码事实为准，使用：

- `content_ref`

它表示当前 `visible_result.items[*]` 对应的 `cacheable_texts` 全局索引。

### SuggestedAction

外部信息获取工具的建议动作也同步收敛：

- 检索：`tool_content_read`
- 顺序继续读单个内容：`tool_content_sequential_read`

不再向模型建议：

- `evidence_rank`
- `tool_content_batch_read`

## 预期效果

这次调整的目标不是减少功能，而是减少无效分流。

预期收益：

1. **模型路由更直接**
   - 搜索就用 `tool_content_read`
   - 继续读单个内容就用 `tool_content_sequential_read`

2. **会话读取链更稳定**
   - 外部信息工具只需要面向两种后续动作写提示

3. **返回契约更清楚**
   - `tool_content_read` 返回全局命中
   - `tool_content_sequential_read` 返回单内容窗口

4. **维护面更小**
   - 不再维护三套高度耦合的 session 读取心智模型

## 代码入口

本次调整的核心代码入口：

- `src/chat/application/tools/session_tools/tool_content_read_tool.py`
- `src/chat/application/tools/session_tools/tool_content_read/service.py`
- `src/chat/application/tools/session_tools/tool_content_sequential_read_tool.py`
- `src/chat/application/tools/tool_output_cache.py`
- `src/chat/application/tools/common/tool_content_store/store.py`
- `src/chat/container.py`

## 验证

本次调整后已运行的针对性验证包括：

- `compileall` 覆盖工具与容器相关代码
- session 读取工具返回结构测试
- 顺序读取工具测试
- receipt 输出缓存测试
- `web_fetch` / `document_parse` 相关回归测试

该文档是完成项记录；后续若再调整读取链，应新增新的 `important/` 文档，而不是覆盖本文件。
