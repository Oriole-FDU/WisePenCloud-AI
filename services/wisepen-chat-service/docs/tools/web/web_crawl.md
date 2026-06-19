# web_crawl

实现入口：`src/chat/application/tools/web_tools/web_crawl_tool.py`  
内部服务：`src/chat/application/tools/web_tools/web_fetch/crawler.py`

`web_crawl` 从一个种子 URL 出发递归抓取 HTML 页面集合。它适合“读取站点的一组相关页面”，不是单 URL 抓取工具，也不是文件下载或 PDF 解析工具。

## 何时使用

- 用户明确要求 crawl、scrape site、收集某站点多个页面。
- 一个入口页下面有多篇相关内容，单次 `web_fetch` 不足。
- 用户要求读取某站点某栏目或文档站的一组页面。

不要在这些场景使用：

- 只需要一个页面：用 `web_fetch`。
- 只需要搜索候选：用 `web_search`。
- 目标是 PDF、图片或 Office 文件：用 `document_parse` 或先用 `web_fetch` 探测。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `seed_url` | `string` | 必填，完整 `http(s)` URL。 |
| `max_pages` | `integer` | 可选，默认由 `tool_settings.WEB_CRAWL_DEFAULT_MAX_PAGES` 控制。 |
| `max_depth` | `integer` | 可选，种子页深度为 0。 |
| `same_domain` | `boolean` | 可选，默认 `true`。除非用户明确要求跨域，否则保持默认。 |

## 内部运行机制

```text
seed_url
  -> BFS queue
  -> httpx fetch
  -> optional scrapling fallback
  -> skip non-HTML
  -> cleaner -> markdown
  -> lxml extract links
  -> same-domain filter
  -> max_pages / max_depth stop
  -> ToolReturn(cacheable_texts=page markdowns)
```

`WebCrawlService` 直接使用底层 fetcher，而不是调用 `FetchCoordinator.fetch_one`，因为 crawl 需要 raw HTML 来提取链接。非 HTML 文件会被跳过，不生成 `tfile_*`，因为 crawl 的目标是 HTML 页面集合。

crawler 已纳入统一 URL 内容缓存体系。每个页面抓取前先读 `web_content_cache`；命中时直接返回缓存 Markdown，并使用缓存中的 `raw_html` 继续抽取链接。未命中时执行物理抓取，清洗后的 Markdown 和 raw HTML 通过 `WebContentCacheService` 写回同一 URL 缓存路径。stale 命中会返回旧内容并排队刷新。

这与 `web_fetch` 共享缓存服务，是正确行为：两者都是 HTML 页面内容获取工具，差异只在 frontier 策略（单页/批量 URL vs BFS crawl），不应维护两套 URL 缓存协议。

## 输出

返回 `ToolReturn(tag="web_crawl_result")`：

- `visible_result.seed_url`：起点 URL。
- `visible_result.pages_crawled`：成功页面数。
- `visible_result.pages`：每页 `url`、`final_url`、`title`、`markdown_length`、`warnings`。
- `visible_result.suggested_actions`：建议用 `tool_content_read(mode="ranked_expand")` 在爬取内容中定位答案；若后续只需继续阅读单个命中内容，再用 `tool_content_sequential_read`。
- `cacheable_texts`：每个页面的 Markdown。

如果没有任何页面可抓取，工具抛出 `web_crawl_empty_result`。

## 工具链协作

- `web_search -> web_fetch` 更适合开放网络找候选再读几个页面。
- `web_crawl -> tool_content_read` 更适合同站多页采集后的跨页检索。
- `web_crawl` 不产生 `search_ref`、不产生 `tfile_*`、不解析文件。

## 可插拔组件

- fetcher 链：可实验 browser fetcher、站点限速、robots/域名策略。
- link extractor：当前用 lxml 解析 `<a href>`，可扩展 sitemap、canonical、文档站导航解析。
- cleaner：与 `web_fetch` 共用 cleaner，可替换正文抽取策略。
- URL 缓存：与 `web_fetch` 共用 `WebContentCacheService`，可实验 TTL、刷新队列、raw HTML 保留策略。
- frontier 策略：当前 BFS，可实验优先级队列、路径 allowlist、URL 去重规范化。

## 后续优化

- 增加 include/exclude path pattern，让模型能表达“只爬 docs/api 下”。
- 对跨域 crawl 增加更强上限和风险提示。
- 返回每页 `content_id` 映射，减少模型在多页 `cnt_*` 中定位成本。
