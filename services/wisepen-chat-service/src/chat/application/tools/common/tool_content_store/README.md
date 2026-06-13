# ToolContentStore

`ToolContentStore` 是工具输出内容的 Redis 存储层，只负责入库、取回实体、更新 metadata 和解析 canonical content_id。

## 职责

- 通过 `ToolContentRepository` 写入 Redis，不再依赖通用 `ContentStore`。
- 使用 `session_id` 做会话隔离。
- 为每条工具内容生成 `cnt_*` content_id。
- 入库时调用 `ChunkingEngine` 生成 chunks 和 indexes。
- chunk 只保存索引元数据，不保存正文副本；读取时从 `StoredToolContent.text` 按 offset 切。
- 返回 `ToolContentReceipt`，不返回正文窗口。
- `ToolContentReceipt` 只暴露结构化读取提示：`read_modes` 和 `selectors`。

## 不负责

- 不实现 `tool_content_read`。
- 不实现 sequential/chunks 读取窗口。
- 不做模型可见内容格式化。
- 不做 ranking。
- 不做 chunking 算法本身。
- 不保留通用 content store 兼容层。

## 入库流程

```text
tool text
  -> strip / length check
  -> ChunkingEngine.chunk(...)
  -> StoredToolContent
  -> RedisToolContentRepository
  -> ToolContentReceipt
```

## Redis Key

```text
wisepen:tool_content:item:{content_id}
wisepen:tool_content:session:{session_id}
```

读取时必须校验 `stored.session_id == session_id`，避免跨会话访问。

`ToolContentStore` 不直接持有 Redis client；Redis 细节只在 `repository.RedisToolContentRepository` 内。

内容角色使用 `ToolContentRole` 枚举，定义在 `models.py`；包顶层只导出 `ToolContentStore` 和 `ToolContentRepository`。

## Chunk 元数据

`ToolContentChunk` 是 selector/read 的索引单元，不是正文副本。标准字段包括：

- `chunk_index`
- `start_offset` / `end_offset`
- `start_unit` / `end_unit`
- `unit_types`
- `section_path`
- `anchor_names`
- `page_name`

后续 `tool_content_read` 的 selector 应优先使用这些显式字段，而不是到处翻 `metadata`。

## 默认 Pipeline

- `content_type == "text/markdown"` 使用 `MARKDOWN_PIPELINE`
- 其它内容使用 `PLAIN_TEXT_PIPELINE`

调用方也可以在 `put(...)` 时显式传入 `chunking_pipeline`。

## 后续调用方式

`ToolContentStore` 已在 `chat.container.Container` 中注册为 singleton。业务代码如果在 DI wiring 范围内，可以直接注入容器 provider；普通代码也可以从全局容器取实例：

```python
from chat.container import container
from chat.application.tools.common.tool_content_store.models import ToolContentRole

store = container.tool_content_store()
receipt = store.put(
    session_id=session_id,
    producer="web_search_tool",
    source=url,
    text=markdown,
    content_type="text/markdown",
    content_role=ToolContentRole.TOOL_OUTPUT,
    metadata={"title": title},
)
```

`put(...)` 成功后返回 `ToolContentReceipt`。调用方通常把 `receipt.content_id` 返回给模型或后续工具；后续读取工具会基于 `content_id`、`read_modes` 和 `selectors` 再实现窗口读取。

当前阶段 `ToolContentStore.get(...)` 只用于内部取回完整实体和校验 `session_id`：

```python
stored = store.get(content_id=receipt.content_id, session_id=session_id)
```

读取窗口、格式化模型上下文、ranking 都不在本模块实现。
