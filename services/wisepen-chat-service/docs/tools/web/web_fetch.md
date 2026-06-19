# web_fetch

实现入口：`src/chat/application/tools/web_tools/web_fetch_tool.py`  
内部服务：`src/chat/application/tools/web_tools/web_fetch/fetch_coordinator.py`

`web_fetch` 批量抓取一组独立 URL，HTML 返回清洗后的 Markdown，非 HTML 文件发布为 `tfile_*` 供 `document_parse` 继续解析。它是外界信息获取链中的正文抓取工具，不负责搜索候选、不递归爬站、不直接解析 PDF/Office 内容。

## 何时使用

- 用户给出普通网页 URL，并要求读取页面内容。
- `web_search` 返回了 `search_ref`，需要把候选变成可验证正文。
- URL 类型不确定，需要先探测是 HTML 还是文件。

不要在这些场景使用：

- 用户给出明显文件直链并要求读文件内容：直接用 `document_parse(mode="from_direct_urls")`。
- 用户需要多页站点采集：用 `web_crawl`。
- 用户只需要搜索候选：用 `web_search`。
- 已有 `cnt_*`：用 session 读取工具。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `mode` | `string` | 必填。`from_search_results` 或 `from_direct_urls`。 |
| `search_refs` | `string[]` | `mode="from_search_results"` 时必填，来自本用户之前的 `web_search`。 |
| `urls` | `string[]` | `mode="from_direct_urls"` 时必填，支持批量完整 `http(s)` URL；超单批时工具内部自动分批。 |

两种模式互斥。`search_refs` 会先解析为真实 URL，并校验同一批次的 `source_scope` 一致，避免 public/custom 缓存串域。

## 内部运行机制

```text
input mode
  -> direct urls or search_ref resolution
  -> FetchCoordinator.fetch_many
  -> per URL: read URL cache
  -> httpx fetch
  -> optional scrapling fallback
  -> HTML: trafilatura clean + quality check + cache write
  -> non-HTML: ToolRunFileStore publish + cache stub write
  -> ToolReturn(cacheable_texts=markdowns)
```

抓取链路优先使用 `HttpxFetcher`；网络失败或 HTML 质量不足时降级到 `ScraplingFetcher`。HTML 由 `TrafilaturaCleaner` 清洗为 Markdown，并按 HTTP cache-control 计算 URL 缓存 TTL。

非 HTML 文件不会被 `web_fetch` 解析。它会：

1. 下载到临时文件。
2. 为 URL 写入缓存占位文档。
3. 发布为 `tfile_*`，metadata 包含 `source_kind=web_fetch`、`source_scope`、`source_url`、`final_url`、`source_cache_doc_id`。
4. 返回 `file_ref` 和 `file_label`，建议下一步调用 `document_parse`。

## 输出

返回 `ToolReturn(tag="web_fetch_result")`：

- `visible_result.items`：每个成功 URL 的轻量元数据，包含 `source_url`、`final_url`、`status_code`、`content_type`、`title`、`warnings`、`file_ref`、`file_label`、`source_scope`。
- `visible_result.failed`：单 URL 失败列表；批量中单项失败不阻断其它 URL。
- `visible_result.warnings`：批量级 warning。
- `visible_result.suggested_actions`：通常建议 `tool_content_read`；若有 `file_ref`，同时建议 `document_parse`。在检索结果收敛到单个内容后，再使用 `tool_content_sequential_read` 继续顺序阅读。
- `cacheable_texts`：HTML Markdown。超出内联阈值后变成 `cnt_*`。

visible result 不直接携带 Markdown，避免大正文污染模型上下文。

## 工具链协作

- `web_search -> web_fetch(mode="from_search_results")`：标准搜索证据链。
- `web_fetch -> document_parse(mode="from_web_fetch")`：不确定 URL 命中非 HTML 文件后的解析链。
- `web_fetch -> tool_content_read`：HTML 正文进入 `cnt_*` 后的跨文档内容检索链；命中某个具体内容后，可继续调用 `tool_content_sequential_read` 顺序阅读该内容。
- 与 `document_parse(mode="from_direct_urls")` 共享 URL 缓存和 fetcher 能力，这是正确的强耦合。

## 可插拔组件

- `HttpxFetcher`：常规 HTTP 下载，可实验 header、重试、MIME 探测和最大响应字节。
- `ScraplingFetcher`：动态/反爬 fallback，可替换为 Playwright 类 browser fetcher。
- `TrafilaturaCleaner`：HTML 正文抽取，可替换 cleaner 或 renderer。
- `judge_quality`：降级判断阈值，可按站点类型或内容长度实验。
- `WebContentCacheRepository`：URL 缓存实现，可扩展 ETag、Last-Modified、缓存分层。

## 后续优化

- 在提示词中持续强调 obvious file URL 直接走 `document_parse`。
- 给非 HTML handoff 增加更明确的 MIME/扩展名 reason，帮助模型选择下一步。
- 对 HTML 缓存命中结果暴露 `cache_status`，减少重复抓取。
- 支持条件刷新和用户显式强制刷新参数。
