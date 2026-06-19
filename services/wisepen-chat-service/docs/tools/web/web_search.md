# web_search

实现入口：`src/chat/application/tools/web_tools/web_search_tool.py`

`web_search` 是当前项目里最重要的外部信息发现工具之一。它的职责非常明确：发现候选、做轻量归一化与排序、判断覆盖是否足够、必要时做内部多跳改写，再把“后续可继续处理的候选引用”交给模型。它不读取正文，不把 preview 当证据，不替代 `web_fetch`、`document_parse` 或 hydration 工具。

这套边界使它非常好用，也非常容易扩展：搜索、正文抓取、结构化补全、缓存、内容读取彼此解耦，各自可以独立演进而不互相污染协议。

## 何时使用

- 用户需要实时、外部、时效性或需要来源验证的信息。
- 需要先找候选页面，再决定抓正文。
- 需要对同一问题做多跳搜索覆盖，而不是让主模型自己手写一连串搜索调用。
- 需要把搜索结果继续转交给 `web_fetch`、`paper_hydrate` 或 `github_hydrate`。

## 不适合做什么

- 不适合直接回答最终事实问题而不抓正文。
- 不适合读取网页正文、PDF 或 Office 文档。
- 不适合替代 `paper_hydrate` / `github_hydrate` 这类显式结构化补全。
- 不适合处理已经在上下文中可直接回答的问题。

## 输入

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `question` | `string` | 必填。用户原始信息需求，保留用户语言。 |
| `first_query` | `string` | 必填。第一跳搜索短 query。 |
| `fallback_query` | `string` | 必填。备用 query，必须和 `first_query` 在角度或语言上明显不同。 |
| `max_results` | `integer` | 可选，默认 10，最大 20。 |

### 输入设计原则

- `question` 给内部小模型做 sufficiency 判断和 query rewrite。
- `first_query` / `fallback_query` 给真实搜索 provider 使用。
- 不要把 `question` 原文直接重复塞进两个 query。
- `fallback_query` 不是兜底同义改写，而是显式提供第二观察角度。

## 输出

返回 `ToolReturn(tag="web_search_result")`，不缓存正文内容。

`visible_result` 主要包含：

- `query`：最终对应的用户问题。
- `candidates`：主模型可见的轻量候选列表。
- `recommended_ids`：内部排序后推荐优先处理的候选编号。
- `suggested_actions`：后续工具建议。
- `warning`：多跳结束后若仍覆盖不足，会显式提示。

### candidate 可见字段

主模型当前只看到这些字段：

- `search_ref`
- `title`
- `overview`
- `highlights`
- `supplier_answer`

主模型看不到：

- `url`
- `search_run_id`
- `candidate_id`
- `source_id`

这是刻意设计的边界。模型只拿到“可继续处理的引用”，不能跳过 `web_fetch` 直接把 preview 当证据。

## 内部流程

内部流程可以概括为：

```text
question
  -> first_query
  -> route
  -> provider search
  -> sufficiency judge
  -> optional next_query rewrite
  -> optional fallback_query
  -> merge results
  -> candidate build
  -> internal ranking
  -> visible search_ref candidates
```

### 1. route 与 provider

`WebSearchService` 会先把 query 送到 router，再由 endpoint planner 决定 provider endpoint。当前 service 本身不查库、不解密凭证；custom 配置必须先固化到 runtime context。

### 2. 内部多跳

`WebSearchTool` 自己管理多跳，不要求主模型重复调用：

1. 执行 `first_query`
2. 用小模型判断当前结果是否足够回答
3. 若不足，优先使用内部改写出的 `next_query`
4. 如果没有 `next_query`，且 `fallback_query` 还没用过，则执行 `fallback_query`
5. 最多 3 跳

这个设计的价值很高：主模型只需要把问题和两个高质量 query 提供好，后续覆盖判断和跳数控制由工具内部完成。

### 3. 候选构建与推荐

多跳结束后，内部会统一生成稳定候选编号 `[1]`、`[2]` 等，并生成 `search_ref`。如果 sufficiency 判定为足够回答，则再次调用内部排序逻辑返回最多 5 个推荐编号；否则退回原始顺序前 3 个。

## search_ref 协议

`search_ref` 是当前 `web_search` 最重要的协议产物。

它的作用不是给模型展示“内部 ID”，而是把搜索发现与后续动作解耦：

- `web_search` 负责找到候选
- `web_fetch` 用 `search_ref` 解析到真实 URL 再抓正文
- hydration 工具未来可基于明确目标再做结构化补全

当前 Redis 映射里保留：

- `user_id`
- `search_ref`
- `search_run_id`
- `candidate_id`
- `source_id`
- `url`
- `source_scope`

但这些都是内部细节，主模型只消费 `search_ref`。

## Suggested Actions

当前返回里包含三类建议动作：

- `web_fetch`：高优先级，真正把候选变成可读证据。
- `paper_hydrate`：低优先级，仅当候选明确是论文且需要细粒度元数据时才用。
- `github_hydrate`：低优先级，仅当候选明确是 GitHub 仓库且需要细粒度元数据时才用。

这组建议非常符合当前架构：搜索先发现，再显式抓正文，再按需要做结构化补全。

## custom / platform 边界

当前工具上下文里只注入：

```text
search_config
```

不要再回退到把 `search_mode`、`provider`、`api_key` 拆成多个顶层字段。`web_search` 工具只消费固化后的 runtime config。

当前 custom / platform 的关键边界：

- platform 默认走平台 provider。
- custom 必须先在 runtime context 中验证并固化。
- custom 搜索源由 `WebSearchCustomSourceFactory` 显式构造。
- service 不负责读取用户凭证。

## 为什么它具有很强的可扩展性

这是 `web_search` 现在最值得强调的地方。

### 1. 搜索与正文读取解耦

`web_search` 只做发现和排序，`web_fetch` 只做正文抓取。这意味着：

- 可以独立升级 provider 层
- 可以独立升级抓取、清洗、缓存
- 不需要在 search 阶段携带大文本

### 2. 搜索与 hydration 解耦

论文、GitHub、包、视频等结构化补全不塞进搜索主流程，只在“目标明确且确实需要更细信息”时显式触发。这样扩展新 hydrator 时，不会污染通用搜索协议。

### 3. 主模型协议稳定

模型侧始终只需要理解：

- `question`
- 两个 query
- `search_ref`
- 推荐候选

即便以后新增 provider、缓存层、更多 route、更多 hydration 类型，模型协议都不需要大改。

### 4. provider 层易扩展

provider 已经走统一 searcher / provider model / endpoint plan 边界。后续接更多搜索源时，扩展点很清晰：

- 新 provider mapper
- 新 provider searcher
- route 到 endpoint 的映射
- custom source factory 支持

### 5. 后续缓存与内容链路可独立增强

当前 `search_ref`、candidate repository、runtime context、fetch handoff 已经把未来扩展点留好：

- recall cache
- URL 内容缓存
- stale-while-revalidate
- hydrate 结果缓存

这些都可以后续增加，而不破坏主工具的使用方式。

## Review 时重点看什么

- 是否仍然坚持 `search_ref` 而不是把 URL 暴露回主模型。
- 是否把 preview 错当成证据。
- `first_query` / `fallback_query` 是否真的提供不同覆盖角度。
- 多跳停止条件是否清晰。
- provider 失败是否保持平台链路的容错。
- custom source 是否仍然只从 runtime context 构造，而不是回退到 service 查库。
- 新扩展是否污染了 search / fetch / hydrate 之间的边界。

## 相关文件

建议先读：

- `src/chat/application/tools/web_tools/web_search_tool.py`
- `src/chat/application/tools/web_tools/web_search/service.py`
- `src/chat/application/tools/web_tools/web_search/result_builder.py`
- `src/chat/application/tools/web_tools/web_search/runtime_context.py`
- `src/chat/application/tools/web_tools/web_search/multi_hop.py`
- `src/chat/application/tools/web_tools/web_fetch_tool.py`
- `src/chat/application/chat_turn_coordinator.py`
