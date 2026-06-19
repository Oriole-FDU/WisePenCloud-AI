# Toolchain Architecture

实现入口：`src/chat/application/tools`，注册入口：`src/chat/container.py`

当前注册工具不是一组彼此孤立的函数，而是一个面向模型的工具体系。核心目标是：外界信息获取、文件解析、大文本缓存、证据定位、数学计算和 Skill 懒加载分别保持边界清晰，同时通过 `search_ref`、`tfile_*`、`cnt_*` 和 URL 缓存形成稳定的协作链。

## 已注册工具

当前 `ToolRegistry` 注册 17 个工具：

| 分组 | 工具 |
| --- | --- |
| document | `document_parse` |
| math | `calculus_solver`、`linear_algebra_solver`、`equation_solver`、`stats_solver`、`expression_solver` |
| session | `tool_content_read`、`tool_content_sequential_read`、`get_historical_chat_messages` |
| web | `web_search`、`web_fetch`、`web_crawl`、`paper_hydrate`、`github_hydrate` |
| skill | `load_skill`、`load_skill_asset`、`create_skill` |

## Runtime 信封

工具执行统一经过：

```text
ToolInvocation
  -> JsonSchemaCheck / RequiredContextCheck / custom preflight hooks
  -> tool.execute(...)
  -> ToolOutputRenderer
  -> ToolOutputCache
  -> RenderToolResult
```

工具可以返回普通结构，也可以返回 `ToolReturn`。`ToolReturn` 的核心字段：

- `tag`：渲染成 XML 根节点，方便模型稳定读取。
- `visible_result`：直接给模型看的轻量结构化结果。
- `cacheable_texts`：大段正文、Markdown 或解析结果，不直接塞进 visible_result。

`ToolOutputCache` 会按总字符数决定是否把 `cacheable_texts` 内联；超出阈值时写入 `ToolContentStore`，返回 `cnt_*` receipt。后续读取必须使用 session 工具，而不是重新抓取或重新解析。

## 统一切面行为

所有 tool 都穿过同一组切面。工具文档和代码 review 必须按切面检查，而不是只看单个 `execute()`。

### 1. Disclosure 切面

`ToolRegistry.derive()` 根据默认暴露、allow/deny、显式 expose 生成本轮 `ToolScope`。模型只看到 `ToolScope.schemas()` 中的工具 schema。

约束：

- 默认隐藏工具不能靠描述词“诱导”模型调用，必须被显式 expose。
- 新增高风险工具优先默认隐藏。
- 不得绕过 scope 把全量工具 schema 直接交给模型。

### 2. Preflight 切面

`ToolExecutor` 固定执行：

```text
JsonSchemaCheck -> RequiredContextCheck -> custom preflight hooks
```

约束：

- 类型、枚举、required、min/max 放 JSON Schema。
- `user_id`、`session_id`、`search_config`、`allowed_skill_ids` 等可信上下文放 `required_context_keys`。
- 权限、白名单、manifest path、跨字段外部校验放 custom preflight。
- OpenAI function calling 不适合表达的互斥模式可在 execute 中校验，但提示词和 schema description 必须写清楚。

### 3. Execute 切面

`execute(context, **kwargs)` 只做业务入口适配：

- 参数归一化。
- mode 路由。
- 调用内部 service。
- 将可重试/不可重试错误映射为 `ToolExecutionError`。
- 单项失败转为 failed item，除非整个请求不可执行。

service 层不读取 LLM schema，不生成 XML，不写 `content_receipt`，不读取不可信模型上下文。

### 4. Render 切面

普通返回值由 `ToolOutputRenderer` 递归渲染。工具不得手写 XML 或为了渲染而构造私有 result layer。

`ToolReturn` 只在有大文本托管需求时使用：

- `visible_result` 给模型看状态、引用、warning、建议动作。
- `cacheable_texts` 放 Markdown、正文、解析文本。

### 5. Output Cache 切面

`ToolOutputCache` 统一决定大文本内联还是存仓：

- 小文本内联为 `<contents>`。
- 大文本写入 `ToolContentStore`，生成 `cnt_*`。
- 每段 `cacheable_texts[i]` 是独立内容单元，不能提前拼接多个文件或多个页面。

消费 `cnt_*` 的工具是 session 工具；生产工具不要自己实现窗口读取。

### 6. Runtime File 切面

`ToolRunFileStore` 是工具间文件移交唯一协议：

- 生产 `tfile_*`。
- Redis 保存元数据，本地/共享文件系统保存对象。
- `resolve_ref` 按 `user_id/session_id` 校验作用域。

模型和工具都不能传本地路径、OSS object key 或 base64 作为跨工具文件协议。

### 7. URL Cache 切面

`WebContentCacheService` / repository 是 web/document 的 URL 缓存边界：

- HTML markdown：`web_fetch` 和 `web_crawl` 读写。
- 非 HTML 文件：`web_fetch` 写占位，`document_parse` 回填解析 Markdown。
- 文件直链：`document_parse(mode="from_direct_urls")` 直接读写同一 URL 缓存。
- stale 命中先返回旧内容，刷新走后台队列。

这不是普通工具间随意耦合，而是核心外界信息获取工具体系的统一切面。

### 8. Background 切面

后台行为分为：

- 主服务自动启动：`ToolRunFileStoreGcScheduler`、`WebContentCacheGcScheduler`。
- 独立 worker：Arq refresh worker 消费 `wisepen:web_content_cache:refresh`。

如果只启动 API 不启动 worker，stale refresh 任务会入队但不会执行。开发环境可用 `start-chat-service.ps1` 同时启动 API 和 worker。

### 9. Suggested Actions 切面

`SuggestedAction` / `SuggestedActions` 给模型提示下一步链路，不是强制计划。

规则：

- 可以写工具名、mode、原因、优先级和轻量 metadata。
- 不写完整调用参数。
- 不代替工具 schema 和提示词边界。
- 多工具链要通过 suggested actions 降低模型误路由概率。

## 三类引用

| 引用 | 生产者 | 消费者 | 语义 |
| --- | --- | --- | --- |
| `search_ref` | `web_search` | `web_fetch(mode="from_search_results")` | 搜索候选到真实 URL 的短期映射，模型不直接拿 URL。 |
| `tfile_*` | `web_fetch`、`document_parse` 直链下载等 | `document_parse(mode="from_web_fetch")` | 工具运行期临时文件引用，按 `user_id/session_id` 隔离。 |
| `cnt_*` | `ToolOutputCache` | `tool_content_read`、`tool_content_sequential_read` | 大文本缓存凭证，表示已有内容，不代表新外部抓取需求。 |

模型看到这些引用后应沿协议消费，不能猜测内部 URL、文件路径或缓存文档 ID。

## 外界信息获取链

标准网页信息链：

```text
web_search
  -> search_ref
  -> web_fetch(mode="from_search_results")
  -> cleaned markdown
  -> cnt_*
  -> tool_content_read / tool_content_sequential_read
```

直达网页链：

```text
web_fetch(mode="from_direct_urls")
  -> cleaned markdown
  -> cnt_*
  -> session read tools
```

文件直链链路：

```text
document_parse(mode="from_direct_urls")
  -> httpx direct fetcher
  -> ToolRunFileStore
  -> DocumentParseService
  -> parsed markdown
  -> URL content cache + cnt_*
  -> session read tools
```

搜索命中文件但模型不确定资源类型时：

```text
web_fetch(mode="from_search_results")
  -> non-HTML file_ref tfile_*
  -> document_parse(mode="from_web_fetch")
  -> parsed markdown
  -> URL content cache + cnt_*
```

`web_fetch` 和 `document_parse` 的强耦合是正确行为：它们共同构成核心外界信息获取工具体系。`web_fetch` 负责 HTML 抓取、未知 URL 探测和非 HTML 文件移交，`document_parse` 负责文件内容抽取；二者共享 URL 内容缓存、fetcher 和 `source_scope` metadata，保证同一 URL 的网页正文、文件占位和解析 Markdown 能走同一缓存路径。

## Web / Document 边界

- 明显的 PDF、图片、Office、表格等文件直链，直接调用 `document_parse(mode="from_direct_urls")`。
- 普通 HTML 页面或不确定类型 URL，调用 `web_fetch`。
- 多页站点采集，调用 `web_crawl`。
- `web_search` 只找候选，不能把 preview 或 supplier answer 当最终证据。
- `paper_hydrate` / `github_hydrate` 只补结构化元数据，不读取正文或文件。

这条边界应同时写进 `web_fetch` 和 `document_parse` 的提示词，最大限度减少明显文件直链被 `web_fetch -> tfile_* -> document_parse` 二次转发。

## URL 缓存路径

`web_content_cache` 是 web/document 共同的 URL 内容缓存边界：

- HTML 成功抓取后，`web_fetch` 写入清洗后的 Markdown。
- HTML crawl 页面同样通过 `WebContentCacheService` 读写同一 URL 缓存；缓存命中时仍保留 raw HTML 用于继续抽链。
- 非 HTML 抓取后，`web_fetch` 先写占位文档，并把 `source_cache_doc_id` 写进 `tfile_*` metadata。
- `document_parse` 解析来源为 `web_fetch` 的文件后，回填同一 URL 缓存文档的 Markdown，并标记 `parser=document_parse` 和 `parser_version`。
- `document_parse(mode="from_direct_urls")` 也先读同一 URL parse 缓存；未命中时下载文件、预创建占位、解析并回填。
- stale 命中返回旧内容，同时通过 refresh lock 和 refresh queue 安排后台刷新。

缓存访问域由 `source_scope` 决定：`web_public` 走公共缓存，`web_custom` 走用户私有缓存。document parse 读取时不能在 public/private 间串域回退。

### Mongo 正文清理

Redis entry 是 URL cache active 状态的权威索引，并按 `hard_expire_at` 设置 TTL 自动过期。MongoDB 中的 `wisepen_web_content_cache_values` 只保存正文文档，不依赖 Mongo TTL 自动删除。

主服务启动时会启动 `WebContentCacheGcScheduler`，定期清理 Mongo 中已经不 active 的缓存正文：

- 只扫描 `updated_at` 早于保留期的 Mongo 文档。
- 对每个候选文档，按 `user_id + canonical_url + cache_mode` 查询 Redis active entry。
- 如果 Redis entry 仍指向同一个 Mongo `doc_id`，保留。
- 如果 Redis entry 不存在，或已指向新文档，删除该 Mongo 文档。

默认扫描周期是 3 天，inactive 保留期是 7 天，单次最多处理 1000 条，可通过 `tool_settings` 调整。

## Session 内容链

当工具返回 `cnt_*` 后，模型应优先使用：

- `tool_content_read(mode="ranked_expand")`：跨一个或多个 `cnt_*` 做全局语义检索。
- `tool_content_read(mode="regex_match")`：跨一个或多个 `cnt_*` 做全局精确模式匹配。
- `tool_content_sequential_read`：按 offset 顺序读取单个 `cnt_*`。

不要因为已经拿到 `cnt_*` 又重新调用 `web_fetch`、`web_crawl` 或 `document_parse`。

## 工具族流程

### Web 工具

```text
web_search
  -> candidate repository(search_ref -> URL/source_scope)
  -> web_fetch or hydrator

web_fetch
  -> URL cache read
  -> httpx/scrapling
  -> HTML: cleaner -> URL cache -> cacheable_texts -> cnt_*
  -> non-HTML: URL cache stub -> tfile_* -> document_parse

web_crawl
  -> URL cache read per page
  -> httpx/scrapling
  -> cleaner -> URL cache -> cacheable_texts -> cnt_*
  -> raw_html link extraction for BFS

paper_hydrate / github_hydrate
  -> structured metadata only
  -> no body/PDF/source-code fetching
```

Web 工具的模型约束：search preview 不是证据；hydration metadata 不是正文；明显文件直链不绕 `web_fetch`。

### Document 工具

```text
document_parse(from_web_fetch)
  -> ToolRunFileStore.resolve_ref
  -> parsed URL cache read
  -> DocumentParseService
  -> URL cache writeback when source_kind=web_fetch
  -> cacheable_texts -> cnt_*

document_parse(from_direct_urls)
  -> parsed URL cache read
  -> direct fetcher
  -> ToolRunFileStore publish
  -> same parse path
```

Document 工具的模型约束：只解析文件，不读普通 HTML；不接本地路径、OSS key、`cnt_*`。

### Session 工具

```text
cnt_* receipt
  -> tool_content_read for cross-document retrieval
  -> tool_content_sequential_read for single-content continuation
```

Session 工具的模型约束：只消费已有内容，不抓取、不解析、不再生产 `cnt_*`。

### Skill 工具

```text
SkillMatcher
  -> allowed_skill_ids
  -> load_skill
  -> manifest
  -> load_skill_asset
```

Skill 工具的模型约束：只加载本轮允许 skill；asset path 必须来自 manifest；`create_skill` 当前注册但发布后端未接通时会失败。

### Math 工具

```text
LLM selects narrow solver
  -> MathSolverTool common wrapper
  -> specific solver service
  -> ordinary structured result
  -> recursive renderer
```

Math 工具的模型约束：不访问外部信息，不执行任意 Python，不使用 `cnt_*`/`tfile_*`。

## Tool 文档模板

每个 tool 文档至少包含：

- 实现入口和内部 service。
- 何时使用 / 何时禁止使用。
- 参数契约和无法由 schema 表达的 execute/preflight 校验。
- 内部运行机制。
- 输出结构和是否使用 `ToolReturn.cacheable_texts`。
- 与其它工具的协作链。
- 对模型的硬约束。
- 可插拔组件。
- 后续优化方向。
- 相关测试。

## 模型约束

- 不要伪造 `search_ref`、`tfile_*`、`cnt_*`、`skill_id`、asset path 或内部 URL。
- 不要把 search preview、supplier answer、hydration metadata 当作正文证据。
- 不要把 obvious file URL 包一层 `web_fetch`，直接给 `document_parse` 的 direct URL 模式。
- 不要把 `file_refs` 和 `direct_urls` 混在一次 document parse 调用里；不要把 `urls` 和 `search_refs` 混在一次 web fetch 调用里。
- 已有缓存凭证时先读缓存；只有用户明确要求刷新或现有证据不足时才重新获取。
- 不能从模型参数传入 `user_id`、`session_id`、API key、object key 或本地路径；这些只能来自可信 context 或 preflight metadata。

## 可插拔组件

| 层 | 当前组件 | 可替换实验方向 |
| --- | --- | --- |
| 搜索 provider | 4get/DDG、Exa、自定义 Exa/Tavily/AnySearch/Serper | provider 排序、endpoint routing、custom provider 扩展、query rewrite prompt。 |
| 搜索内部小模型 | route classifier、sufficiency judge、candidate ranker | 提示词调优、JSON 解析容错、候选重排模型替换。 |
| Web fetcher | `HttpxFetcher` -> `ScraplingFetcher` fallback | 新增 Playwright/browser fetcher、反爬策略、质量判断阈值实验。 |
| Web cleaner | `TrafilaturaCleaner` + Markdown renderer | cleaner 替换、正文保真度评估、表格/代码块渲染策略。 |
| URL 缓存 | `WebContentCacheService` + `WebContentCacheRepository` + refresh queue | TTL 策略、ETag/Last-Modified 增量刷新、raw HTML 保留策略、hydrate 结果缓存。 |
| 文档 parser | PDF strategy、Docling、Pandas、Image OCR、MarkItDown | Parser 顺序实验、OCR provider 替换、PDF 页级策略优化。 |
| 内容分块 | Markdown/plain chunking pipeline | chunk 大小、结构索引、页面/锚点抽取策略。 |
| 排序 | `RankingEngine` | 本地 embedding、cross-encoder、provider reranker、混合 BM25。 |
| Skill 发布 | `SkillPublisher` protocol | 接入真实 ai-asset publisher、冲突检查、版本管理。 |

## 后续优化方向

- 持续调优 `web_fetch` / `document_parse` 提示词中的模式边界，减少文件直链误路由。
- 给直链文件类型判断增加可配置扩展名和 MIME allowlist，辅助模型和工具双层路由。
- 继续收敛 web/document 中非 HTML 占位、parse 回填等缓存逻辑，减少业务类里重复的缓存细节。
- 给 `paper_hydrate`、`github_hydrate` 增加结果缓存和更多实体 hydrator。
- 为 `ToolParametersSchema` 增加可表达互斥模式的 preflight DSL，减少工具 execute 内重复校验。
- 在工具返回中统一暴露更清晰的 `suggested_actions` 数组，减少单数/复数字段差异。
- 给跨工具链加端到端回归用例：search->fetch->read、direct file->parse->read、fetch file->parse cache hit。
