# document_parse

实现入口：`src/chat/application/tools/document_tools/document_parse_tool.py`

`document_parse` 将上游工具产出的 `tfile_*` 临时文件引用，或明显的文件直链 URL，批量解析为 Markdown。文件直链只支持完整 `http(s)` 非 HTML 文件 URL；普通网页仍交给 `web_fetch` / `web_crawl`。

## 何时使用

- 已经有一个或多个 `tfile_*`，需要把文件内容转为可检索 Markdown。
- 用户直接给出明显文件直链（PDF、图片、Office、表格等）并要求读取文件内容时，直接用 `mode="from_direct_urls"`，不要先走 `web_fetch` 生成中转 `tfile_*`。
- 同一任务有多个文件时，应一次性把同一来源模式下的所有文件传入同一调用。
- 解析结果较长时，后续优先通过 `tool_content_read` 检索相关窗口，或通过 `tool_content_sequential_read` 顺序继续阅读单个 `cnt_*`。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `mode` | `string` | 必填。`from_web_fetch` 或 `from_direct_urls`。 |
| `file_refs` | `string[]` | `mode="from_web_fetch"` 时必填，支持批量 `tfile_*`；超单批时工具内部自动分批。 |
| `direct_urls` | `string[]` | `mode="from_direct_urls"` 时必填，支持批量完整 `http(s)` 文件直链；超单批时工具内部自动分批。 |

`file_refs` 和 `direct_urls` 互斥。执行上下文必须包含 `user_id` 和 `session_id`。工具会用这两个值解析并校验 `tfile_*` 的作用域；直链会先下载到短期工具文件，再复用同一解析链路。

## 与 web_fetch 的关系

`web_fetch` 和 `document_parse` 共同构成核心外界信息获取工具体系：`web_fetch` 负责网页正文和不确定 URL 的抓取，`document_parse` 负责文件内容解析。两者共享 URL 内容缓存、HTTP fetcher 能力和 `source_*` metadata 是有意设计的正确行为，不视为需要拆除的错误耦合。

直链解析会先按 URL 读取 parse markdown 缓存；未命中时使用 web fetch 的 HTTP fetcher 下载文件，预创建同一 URL 缓存文档，再把解析出的 Markdown 回填到该 URL 缓存路径。

## 输出

返回 `ToolReturn(tag="document_parse_result")`：

- `visible_result.items`：按输入顺序返回每个文件的 `status`、`file_name`、`content_ref`、`source_scope` 和失败细节。`content_ref` 是对应 `cacheable_texts` 的全局索引。
- `visible_result.suggested_action`：建议后续用 `tool_content_read` 的 `ranked_expand` 模式读取解析结果。
- `cacheable_texts`：每个成功文件一段 Markdown；当总长度超过内联阈值时会被缓存为独立的 `cnt_*` receipt。

## 边界

- `from_web_fetch` 只解析 `tfile_*` 指向的短生命周期文件。
- `from_direct_urls` 只用于明显文件直链，不用于普通 HTML 页面。
- 不负责文件上传、资产持久化、知识库入库或文件展示。
- 单个文件解析失败会记录为 failed item，不阻断其它文件。
- 内部最多并发解析 3 个文件；大量输入会按内部批次切分后顺序聚合。
- 解析计划由 `DocumentParsePlanner` 决定：PDF 走 PDF 策略，DOCX/PPTX/HTML 走 Docling，XLSX 走 Pandas，图片走 OCR，最后使用 MarkItDown 兜底。
- 工具门面不手写 `cnt_*` receipt；大文本缓存由 `ToolOutputCache` 和 `ToolContentStore` 统一处理。
