# ContextBundle

`context_bundle` 是工具输出进入模型上下文前的结构化中间层。它负责组织内容和渲染模型可见文本，不负责存储、读取窗口、ranking 或工具调度。

## 迁移关系

旧 `ToolTextBuilder` 的思路是对的：不要把不同语义的引用混在一段 Markdown 里。新模块保留这点，但不再让工具手拼最终文本。

- `add_text_block` -> `ContextContent`
- `add_assets` -> `ContextAsset`
- `add_content_refs` -> `ContextContent(role="content_reference")` 或 content metadata 中的 `content_id`
- `add_ranked_hits` -> `ContextEvidence`
- `add_content` -> `ContextContent(role="window")`
- `add_required_next_actions` -> `ContextAction`
- `add_warnings` -> `ContextBundle.warnings`

## 基本用法

```python
from chat.application.tools.common.context_bundle import ContextAdapter, ModelContextRenderer

adapter = ContextAdapter()
renderer = ModelContextRenderer()

bundle = adapter.from_text(
    "# Search Result\n\n这里是工具结果。",
    content_id="cnt_123",
    title="Search Result",
)

model_text = renderer.render_bundle(bundle)
```

输出是 XML-like 外壳，正文放在 CDATA 中，Markdown/code/table 会原样保留。

## 和 ToolContentStore 的边界

- `ToolContentStore`：负责 Redis 入库、chunk/index 元数据和 receipt。
- `ContextBundle`：负责把工具输出组织成模型上下文对象。
- `ModelContextRenderer`：负责把 bundle 渲染成模型可读文本。

大文本应先写入 `ToolContentStore`，模型上下文里只放 `content_id`、摘要和后续读取提示；不要把全文塞进 `ContextEvidence.excerpt`。

## 统一切面

工具实现只需要返回：

```python
str | ContextBundle
```

运行时由 `ToolOutputAspect` 统一处理：

```text
Tool.execute()
  -> str | ContextBundle
  -> str 静默转换为 ContextBundle
  -> ModelContextRenderer.render_bundle(...)
  -> ToolContentStore.put(...)
  -> inline XML 或 tool_content_receipt
```

工具内部不应该自己调用 renderer，也不应该自己判断是否缓存。超过 inline budget 时，切面会把渲染后的模型上下文写入 `ToolContentStore`，并给模型返回 receipt；未超过阈值时返回 XML-like rendered text，同时也会写入缓存，便于 trace 和后续读取。

## 当前不做

- 不实现 `tool_content_read`。
- 不实现读取窗口。
- 不实现 formatter 兼容层。
- 不实现 adapter registry。
- 不接 ranking pipeline。
