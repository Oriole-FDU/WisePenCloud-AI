# Tool 统一切面与流程规范

本文约束所有 tool 在 WisePen Chat Service 中必须遵守的统一切面。新增、重构或 review 工具时，先确认这些切面是否已经能表达需求，再决定是否写业务代码。

## 总原则

Tool 不是孤立函数。一个工具调用实际由多层统一切面共同完成：

```text
ToolScope disclosure
  -> LLM tool call
  -> JsonSchemaCheck
  -> RequiredContextCheck
  -> custom preflight hooks
  -> tool.execute business logic
  -> ToolOutputRenderer
  -> ToolOutputCache
  -> ToolContentStore / ToolRunFileStore / web_content_cache
  -> RenderToolResult
  -> optional session read / refresh worker / GC
```

业务工具只负责业务核心：查询、抓取、解析、计算、读取或发布。认证、作用域、渲染、大文本托管、文件移交、URL 缓存、后台刷新和清理必须优先走统一切面。

## 统一切面清单

| 切面 | 入口 | 职责 | 工具内不得重复实现 |
| --- | --- | --- | --- |
| 工具可见性 | `ToolRegistry.derive()` / `ToolScope.schemas()` | 控制本轮模型能看到哪些工具。 | 不得绕过 scope 暴露全量 schema。 |
| Schema preflight | `JsonSchemaCheck` | 校验 JSON Schema 能表达的类型、required、枚举、范围。 | 不得在 service 层重复基础 schema 校验。 |
| Context preflight | `RequiredContextCheck` | 注入并校验 `user_id`、`session_id`、`search_config`、`allowed_skill_ids` 等可信上下文。 | 不得让模型通过参数传入安全上下文。 |
| 自定义 preflight | `ToolPreflightHook` | 做权限、白名单、manifest path 等跨字段/外部校验。 | 不得把权限校验散落在多个 service。 |
| 执行超时 | `ToolPolicy.timeout_seconds` | 对 tool call 施加统一超时。 | 不得在普通业务代码里另造不一致超时策略，外部 SDK 边界除外。 |
| 递归渲染 | `ToolOutputRenderer` | 渲染普通 Python 返回值或 `ToolReturn`。 | 不得手写 XML 或为渲染创建私有 result payload。 |
| 大文本输出缓存 | `ToolOutputCache` | 将 `ToolReturn.cacheable_texts` 内联或转成 `cnt_*`。 | 不得手写 `<content_receipt>` 或自建大文本读取协议。 |
| 内容存储 | `ToolContentStore` | 会话内短期文本存储、chunk/index、receipt。 | 不得把 `cnt_*` 当永久业务 ID。 |
| 文件移交 | `ToolRunFileStore` | 工具间短期文件引用 `tfile_*`，按用户和会话隔离。 | 不得传本地路径、OSS key、base64 作为工具间文件协议。 |
| URL 内容缓存 | `WebContentCacheService` / repository | URL 到 HTML/文件占位/解析 Markdown 的缓存路径。 | web/document 不得维护第二套 URL cache。 |
| 刷新队列 | `WebContentCacheRefreshTaskPublisher` + Arq worker | stale cache 后台刷新。 | 工具不得阻塞等待 stale refresh 完成。 |
| Mongo 缓存 GC | `WebContentCacheGcScheduler` | 删除 Mongo 中不再 active 的正文缓存。 | 不得删除 Redis active entry；Redis TTL 是 active 权威索引。 |
| Suggested actions | `SuggestedAction(s)` | 给模型提示下一步工具链。 | 不得把完整工具参数硬塞进建议动作。 |

## 标准开发流程

新增或改造工具按以下顺序做：

1. **定位业务域**：放入已有 `<domain>_tools` 目录，或确认确实需要新域。
2. **定义模型可见契约**：写 `ToolLLMSpec.description` 和 `ToolParametersSchema`，先表达模式边界、禁止场景和输出规则。
3. **拆分 schema 与 execute 校验**：类型、枚举、required、范围放 schema；互斥模式、权限、manifest、跨字段语义放 preflight 或 execute。
4. **声明 policy**：设置 `expose_by_default`、`timeout_seconds`、`risk_level`、`required_context_keys`、`persist_output`、`cache_chunked`。
5. **复用统一切面**：大文本用 `ToolReturn.cacheable_texts`；文件用 `ToolRunFileStore`；URL 内容用 `web_content_cache`；排序用 ranking engine；分块用 chunking engine。
6. **实现业务 service**：service 不读取模型上下文，不关心 XML 渲染，不直接构造 receipt。
7. **注册 DI**：只有有生命周期、连接池、共享状态或 tool 实例才进 `container.py`。
8. **补工具文档**：不仅写参数，还写内部机制、工具链协作、模型约束、可插拔点和优化方向。
9. **补测试**：至少覆盖 schema/模式校验、成功路径、单项失败、跨工具引用、缓存命中/未命中或权限边界。
10. **验证启动与后台任务**：涉及 refresh queue 或 GC 时，说明是否由主进程自动启动，还是需要额外 worker。

## 输出设计流程

先判断返回内容类型：

- 小型结构化结果：直接返回 `dict`、dataclass、Pydantic model、list 或 scalar。
- 大文本结果：返回 `ToolReturn`，把正文放入 `cacheable_texts`，摘要放入 `visible_result`。
- 读取已有 `cnt_*` 的工具：返回普通结构，不再把窗口文本放回 `cacheable_texts`。
- 错误：抛 `ToolExecutionError` 或返回单项 failed item；不要把异常伪装成成功正文。

`visible_result` 只放模型决策所需的信息：状态、来源、引用、长度、warning、下一步建议。正文、Markdown、PDF 解析文本和长页面内容都应走 `cacheable_texts`。

## 外界信息工具流程

外界信息获取工具形成核心体系，允许存在明确耦合：

```text
web_search -> search_ref -> web_fetch -> cnt_* -> session read
direct HTML URL -> web_fetch -> cnt_* -> session read
site crawl -> web_crawl -> URL cache + cnt_* -> session read
direct file URL -> document_parse -> URL cache + cnt_* -> session read
unknown/search file URL -> web_fetch -> tfile_* -> document_parse -> URL cache + cnt_*
```

这类耦合必须通过稳定引用和统一缓存表达：

- 搜索候选用 `search_ref`，不把 URL 暴露给模型。
- 文件移交用 `tfile_*`，不传本地路径。
- 大文本用 `cnt_*`，不反复抓取或解析。
- URL 内容用 `web_content_cache`，不在工具内部另写缓存表。

## Background 行为

后台行为分两类：

- **主服务自动启动**：`ToolRunFileStoreGcScheduler`、`WebContentCacheGcScheduler` 随 `chat.main` lifespan 启动和停止。
- **独立 worker**：web content stale refresh 使用 Arq worker，需要单独进程消费队列。

启动脚本 `services/wisepen-chat-service/start-chat-service.ps1` 会同时启动主服务和刷新队列 worker。生产部署也必须保证这两个进程都存在，否则 stale refresh 任务会入队但不执行。

## Review 清单

- 是否把可以由 schema 表达的校验留给了 `JsonSchemaCheck`。
- 是否把安全上下文放在 `required_context_keys` 或 preflight metadata。
- 是否绕过 `ToolOutputRenderer` 手写 XML。
- 是否把大文本放进普通 dict/list，而不是 `ToolReturn.cacheable_texts`。
- 是否把读取窗口再次缓存成新 `cnt_*`。
- 是否使用 `ToolRunFileStore` 传文件，而不是本地路径。
- web/document 是否复用 `web_content_cache`。
- stale refresh 是否异步入队，未阻塞工具返回。
- Mongo cache GC 是否只删除不再 active 的 Mongo value，未删除 Redis entry。
- 文档是否写明 tool 链路、模型约束和可插拔点。
