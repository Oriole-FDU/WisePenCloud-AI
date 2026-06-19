# Tool 返回值与缓存规范

本文约束工具返回值、`ToolReturn`、输出渲染、运行时内容缓存和 `tool_content_read` 的职责边界。

跨工具统一切面总览见 [Tool 统一切面与流程规范](06-tool-cross-cutting-flow.md)。本文聚焦返回值、模型可见输出和内容托管。

## 返回值原则

普通工具可以直接返回：

- `dict`
- `list` / `tuple`
- dataclass
- Pydantic model
- `str` / `int` / `float` / `bool`
- `None`

普通返回值只会由 `ToolOutputRenderer` 渲染为模型可见 XML，不进入 `ToolContentStore`。

不得为普通返回值增加工具级缓存开关。

中文强调：普通返回值由统一工具渲染器递归渲染，工具不要手动把 dataclass / dict / list 转成专用 result payload。

English emphasis: normal tool outputs are rendered by the unified recursive renderer. Do not manually convert dataclasses, dicts, or lists into private result payloads just for rendering.

UPPERCASE EMPHASIS: UNIFIED RENDERING IS THE RETURN BOUNDARY. DO NOT MANUALLY CONVERT RESULTS FOR RENDERING.

## ToolReturn 使用边界

只有同时满足以下条件时才使用 `ToolReturn`：

- 模型需要看到结构化摘要、ID、状态、长度、窗口信息等可见结果。
- 工具产生了可能很长、后续需要按窗口读取的文本。

示例：

```python
return ToolReturn(
    tag="document_parse_result",
    visible_result={
        "items": [
            {
                "file_ref": file_ref,
                "status": "success",
                "file_name": file_name,
                "content_ref": 0,
                "text_length": len(markdown),
            }
        ]
    },
    cacheable_texts=(markdown,),
)
```

字段规则：

- `tag` 是 XML 根标签，必须是合法 XML tag。
- `visible_result` 放模型马上需要理解的结构化信息。
- `cacheable_texts` 只放运行时托管的大文本。
- `content_ref` 必须是本次 `ToolReturn.cacheable_texts` 的全局索引。
- 通用 ToolReturn 包装能表达清楚意图时应优先使用，例如 `SuggestedAction` / `SuggestedActions`。

## SuggestedAction

`SuggestedAction` 用于给模型提示一个后续可选工具和 route-level mode。

规则：

- 可以放进 `ToolReturn.visible_result`。
- 只有一个推荐动作时直接使用 `SuggestedAction`；多个动作时才使用 `SuggestedActions`。
- 只放工具名、mode、原因、优先级和轻量 metadata。
- 不放具体工具调用参数，例如 `content_ids`、`file_refs`、`query`、`offset`、`limit`。
- 它是提示，不是强制计划，模型仍应按当前任务目标决定是否调用。

## 分批缓存规则

`ToolOutputCache` 只处理 `RenderedToolOutput.cacheable_texts`：

1. 过滤空文本。
2. 总长度不超过 `settings.TOOL_RESULT_MAX_CHARS` 时内联为 `<contents>`。
3. 超过阈值时，每段 `cacheable_texts[i]` 单独写入 `ToolContentStore`。
4. 每段成功写入后生成一个独立 `cnt_*`。
5. 渲染多个 `<content_receipt>` 给模型。
6. 错误输出不进入 `ToolContentStore`。

不得：

- 把多文件、多来源、多窗口文本提前拼成一个大 `cacheable_texts`。
- 在工具内部手写 `<content_receipt>`。
- 在工具内部另建 Redis 缓存协议来替代 `ToolOutputCache`。
- 把普通 `dict/list` 的大字段当作自动缓存入口。

## ToolContentStore 规则

`ToolContentStore` 当前职责：

- 接收已归一化文本。
- 根据 `content_type` 选择 chunking pipeline。
- 生成 `cnt_*` content id。
- 按 `session_id` 隔离读取。
- 返回包含 `selectors` 的 receipt。

当前默认：

- TTL：30 分钟。
- 最大入库字符数：20,000,000。
- `text/markdown` 使用 Markdown pipeline。
- 其他文本使用 plain text pipeline。

不得把 `ToolContentStore` 当作永久资料库。`cnt_*` 是会话内短期读取凭证，不是长期业务 ID。

## URL 内容缓存与 ToolContentStore 的边界

`ToolContentStore` 和 `web_content_cache` 是两条不同切面：

| 切面 | 标识 | 生命周期 | 用途 |
| --- | --- | --- | --- |
| `ToolContentStore` | `cnt_*` | 会话内短期 TTL | 模型后续读取工具输出大文本。 |
| `web_content_cache` | URL entry + Mongo value | 按 HTTP TTL / inactive GC | 复用外部 URL 抓取、HTML 清洗和文件解析结果。 |

规则：

- 工具返回给模型的大文本读取凭证只用 `cnt_*`。
- 外部 URL 的复用只走 `web_content_cache`。
- 不得把 URL cache 的 Mongo `doc_id` 暴露给模型。
- 不得用 `cnt_*` 替代 URL cache；`cnt_*` 过期后不代表 URL 内容必须重新抓取。
- 不得用 URL cache 替代 `ToolContentStore`；模型读取窗口仍必须通过 session 工具。

`web_fetch`、`web_crawl`、`document_parse` 可以共享 URL cache，这是核心外界信息获取体系的正确耦合。

## tool_content_read 规则

`tool_content_read` 是按同一组读取参数读取 receipt-backed 内容的工具入口。

入参使用批量字段：

```json
{
  "content_ids": ["cnt_xxx", "cnt_yyy"],
  "mode": "ranked_expand",
  "query": "..."
}
```

规则：

- 不支持单值 `content_id` 入参。
- `content_ids` 最多 64 个；超单批上限时由工具门面自动分批。
- 一次调用内所有 `content_ids` 共用同一组 `mode / selector / window` 参数。
- 单个 `content_id` 不存在、过期或不可读时，该项进入 `failed`，不影响其它项。
- 请求级错误才抛工具错误，例如缺少 `content_ids`、缺少 `mode`、模式必要参数缺失。
- 返回普通结构化结果，窗口文本直接放在 `matches[*].window.text`。
- 禁止使用 `ToolReturn.cacheable_texts` 重新缓存读取窗口；`tool_content_read` 只消费已有 `cnt_*`，不生产新的 `cnt_*`。

支持模式：

| mode | 用途 | 必要参数 |
| --- | --- | --- |
| `ranked_expand` | 跨一个或多个 content 做全局语义排序后展开 | `query`, `top_k` |
| `regex_match` | 跨一个或多个 content 做全局正则匹配后展开 | `pattern`, `max_matches` |

Selector 是候选域过滤器，应先过滤再读取或排序。多个 selector 条件取交集。

## tool_content_sequential_read 规则

`tool_content_sequential_read` 专门负责按 offset 顺序读取单个 content。

入参按 content 绑定 chunk 序号：

```json
{
  "content_id": "cnt_xxx",
  "offset": 0,
  "limit": 4000
}
```

规则：

- 只接受单个 `content_id`。
- 适合继续阅读一个已经确认目标的内容，不做跨文档搜索。
- 返回普通结构化结果，窗口文本直接放在 `window.text`。
- 禁止使用 `ToolReturn.cacheable_texts` 重新缓存读取窗口；它只消费已有 `cnt_*`，不生产新的 `cnt_*`。
- 单个 `content_id` 不存在、过期或不可读时，返回 `status=failed`。

## document_parse 规则

`document_parse` 是 `tfile_*` 临时文件引用或明显文件直链 URL 到 Markdown 的工具入口。

入参：

```json
{
  "mode": "from_web_fetch",
  "file_refs": ["tfile_xxx", "tfile_yyy"]
}
```

或：

```json
{
  "mode": "from_direct_urls",
  "direct_urls": ["https://example.com/report.pdf"]
}
```

规则：

- `mode="from_web_fetch"` 只接受批量 `file_refs`。
- `mode="from_direct_urls"` 只接受批量 `direct_urls`，用于明显的非 HTML 文件直链。
- `file_refs` 和 `direct_urls` 互斥，单次总文件数最多 8 个。
- `file_refs` 只接受 `ToolRunFileStore` 产生的 `tfile_*`，不接受本地绝对路径、上传对象 key 或 `cnt_*`。
- 普通 HTML 页面 URL 不走 `document_parse`，应使用 `web_fetch` / `web_crawl`；明显文件直链不要先包一层 `web_fetch`。
- 工具内部并发解析，单项失败不影响其它文件。
- 每个成功文件对应一段 `cacheable_texts`，由 `ToolOutputCache` 分批生成多个 `cnt_*`。
- `visible_result.items[*].content_ref` 指向对应 Markdown 在本次 `cacheable_texts` 中的全局索引。
- 返回中应包含单个 `SuggestedAction`，推荐后续用 `tool_content_read` 的 `ranked_expand` 读取解析后的 Markdown。

`web_fetch` 和 `document_parse` 是同一核心外界信息获取工具体系里的两个阶段化入口。二者共享 URL 内容缓存、HTTP 下载能力和 `source_kind/source_scope/source_url/source_cache_doc_id` metadata 是正确行为：它保证网页正文、文件直链和 web_fetch 产出的非 HTML 文件都能落在统一 URL 缓存路径上。

这类耦合只允许存在于核心外界信息获取体系内，不应扩散成普通工具之间的任意互调。`document_parse` 仍不负责上传、资产持久化、知识库入库或文件展示。

## 后台切面

返回值切面之后可能触发后台行为：

- `ToolOutputCache` 写 `cnt_*` 后，由 Redis TTL 自然过期。
- `ToolRunFileStore` 生成 `tfile_*` 后，由主服务中的 `ToolRunFileStoreGcScheduler` 清理本地对象。
- `web_content_cache` stale 命中后，刷新任务进入 Arq 队列，必须由 refresh worker 消费。
- `web_content_cache` 的 Mongo 正文由主服务中的 `WebContentCacheGcScheduler` 定期清理；Redis entry 是 active 状态权威来源，GC 只删除不再 active 的 Mongo value。

工具实现不得同步等待后台 refresh 或 GC 完成。

## ToolPolicy.cache_chunked

`cache_chunked` 只表示 `cacheable_texts` 入库后是否生成 chunks/index。

它不是：

- 普通返回值缓存开关。
- receipt 生成开关。
- 持久化开关。
- 工具是否可读取的权限开关。

如果后续需要 `tool_content_read` 的 `ranked_expand`、`regex_match` 或 `tool_content_sequential_read` 的稳定定位信息，应保持 `cache_chunked=True`。

如果内容只需要按 offset 顺序读，不需要索引，可以设置 `cache_chunked=False`。

## Review 清单

- 普通结构化结果是否直接返回普通 Python 值。
- 是否依赖统一递归渲染，而不是工具内手写 result 转换层。
- 大文本是否只通过 `ToolReturn.cacheable_texts` 进入缓存切面。
- 多段文本是否保持原始内容单元边界，而不是提前拼接。
- `visible_result` 是否用全局 `content_ref` 指向 `cacheable_texts`。
- 是否存在工具内自定义缓存、手写 receipt 或重复读取协议。
- 是否把 URL 复用放进 `web_content_cache`，而不是混入 `ToolContentStore`。
- 是否把 Mongo cache `doc_id`、Redis key、本地路径或 object key 暴露给模型。
- `tool_content_read` 是否只返回读取窗口，未把窗口文本再次放入 `ToolReturn.cacheable_texts`。
- `tool_content_sequential_read` 是否保持单 `content_id` 顺序读取边界，没有把它做成跨文档搜索工具。
- `cache_chunked` 是否只用于控制 chunk/index。
- `cnt_*` 是否经过 `session_id` 隔离读取。
- `tfile_*` 是否只通过 `ToolRunFileStore.resolve_ref(...)` 解析为真实路径。
