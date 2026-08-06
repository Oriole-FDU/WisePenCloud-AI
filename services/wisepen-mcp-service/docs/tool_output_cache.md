# Tool 输出缓存机制与使用准则

本文说明 WisePen tool output cache 解决什么问题，以及 MCP tool 在什么情况下应
该使用 `cacheable_texts`。这里的“缓存”不是简单的截断开关，而是一条用于长正文
治理和后续读取的输出通道。

## 缓存的两层作用

### 1. 控制模型可见窗口

工具可能产生比当前对话上下文更大的正文，例如网页全文、文档片段或 RAG 命中的
阅读块。直接把这些正文全部塞进 visible result 会挤占 prompt，甚至让下一轮推理
失败。

`ToolReturn.cacheable_texts` 允许工具把完整正文交给 chat-service 的
`ToolOutputCache`。chat-service 会把正文写入 `ToolContentStore`，并只在本轮
输出中展示受预算保护的 preview 和后续读取凭证。

这一层回答的是：本轮模型是否应该直接看到完整正文。

### 2. 给缺少稳定锚点的正文提供后续读取入口

有些工具返回的是一次性正文，来源本身没有稳定的业务结构锚点。例如网页抓取结果
可能只有 URL，正文内部没有 page、section 或可复用的原文 offset 协议。此时仅靠
preview 不够，模型后续需要能按 range、page、section 或检索方式回读完整正文。

`ToolContentStore` 会保存完整文本、chunk 和 locator，并返回 `content_id`。后续
session tools 可以基于这个 `content_id` 调用 read range、read pages、read
sections 或 search。

这一层回答的是：这段正文是否需要由缓存系统补一个可续读的结构化入口。

## 使用判断

只要命中上面任意一层，就应该考虑走缓存；但命中不同层时，具体策略可以不同。

| 场景 | 是否使用 `cacheable_texts` | 原因 |
| --- | --- | --- |
| 小型结构化结果，例如状态、数量、短列表 | 否 | visible result 已足够，后续不需要正文续读。 |
| Web fetch / crawl / document link extraction 的正文 | 是 | 正文可能很长，且来源正文通常没有内部稳定读取锚点。 |
| RAG page/section/navigation 小窗口正文 | 否，直接放入 visible result | RAG 已有 `resource_id`、page label、section id、state id 等结构锚点，短正文不需要再绕缓存。 |
| RAG page/section/navigation 超预算正文 | 是 | 仍然需要保护模型窗口；缓存只承担溢出正文的 range 续读能力。 |
| 已有外部稳定读取 API 的第三方资源 | 视正文大小决定 | 如果小窗口可直接返回，就不要制造二级索引；超预算时再缓存。 |

## RAG 的分流策略

RAG 和普通网页正文不同：它天然有结构化定位入口。

- `rag_get_document_structure` 返回 page label 和 section id，不返回正文。
- `rag_get_page_content` 按 page label 读取。
- `rag_get_section_content` 按 section id 读取。
- knowledge navigation 使用 `state_id`、`section_id`、graph `node_id` 继续导航。

因此 RAG 的 cache 策略不是“所有正文都缓存”，而是“安全窗口内直接返回，超出才
缓存”。

默认预算与 chat-service 的 tool content read 预算保持一致：

```python
RAG_DIRECT_TEXT_WINDOW_CHAR_BUDGET = 24_000
RAG_DIRECT_TEXT_TOTAL_CHAR_BUDGET = 48_000
```

含义是：

- 单个 RAG 正文窗口不超过 `24_000` 字符，且本次工具返回的直接可见正文累计不
  超过 `48_000` 字符时，正文直接出现在 visible result 的 `text` 字段中。
- 只要单窗口或累计正文超过上述预算，该正文进入 `cacheable_texts`，visible
  result 只返回 `content_index` 和 `preview`。

这样 RAG 已有的 page/section/state 仍然是业务定位锚点，`content_index` 只在
超预算时作为缓存续读凭证出现，不会被误解成 RAG 的第二套结构锚点。

## 输出字段约定

### 直接可见正文

当正文未超过 RAG 直接可见预算时，payload 应只包含模型作答和继续导航所需字段：

```json
{
  "text": "...完整窗口正文...",
  "start_offset": 120,
  "end_offset": 912,
  "page_labels": ["5"],
  "section_paths": ["方法 > 实验设置"],
  "anchor_labels": []
}
```

不要在 direct path 中展示 `content_index`、缓存 metadata 或入库回执字段。它们
不是业务语义，展示出来会让 reviewer 和模型误以为 RAG 有另一套定位协议。

### 超预算正文

当正文超过 RAG 直接可见预算时，visible result 使用缓存凭证：

```json
{
  "content_index": 0,
  "preview": "...预算内预览...",
  "start_offset": 120,
  "end_offset": 50000,
  "page_labels": ["5", "6"],
  "section_paths": ["方法 > 实验设置"],
  "anchor_labels": []
}
```

同一个 `ToolReturn` 的 `cacheable_texts[0]` 保存完整正文和内部续读 metadata。
这些 metadata 只服务于 chat-service 入库和后续读取，不直接作为 RAG 的可见输出
字段。

## 工具作者检查清单

新增或迁移 MCP tool 时，按下面顺序判断：

1. 这次输出是否包含长正文，而不只是短结构化结果？
2. 这段正文是否可能撑爆本轮模型上下文？
3. 来源是否已经有稳定业务锚点，例如 page、section、resource id、state id 或
   外部原生 read API？
4. 如果没有稳定锚点，是否需要 `ToolContentStore` 生成 `content_id`、chunk 和
   locator 来支持后续读取？
5. 如果已有稳定锚点，是否只需要在正文超过安全窗口时才使用缓存？
6. visible result 中的字段是否都是模型决策所需，而不是缓存实现细节？

结论应当落到具体策略：

- 两层都不命中：直接返回普通结构化 JSON。
- 只命中“窗口安全”：小正文直接返回，超预算正文进入 `cacheable_texts`。
- 命中“缺少稳定锚点”：正文进入 `cacheable_texts`，让 ToolContentStore 提供
  后续读取入口。
- 两层都命中：进入 `cacheable_texts`，并确保 preview、`content_id`、总长度和
  metadata 能支持后续 read/search。

## 相关实现

- MCP RAG 正文分流：`wisepen_mcp.capabilities.rag.tools.common.RagTextRenderRouter`
- MCP ToolReturn 协议：`wisepen_mcp.capabilities.core.tools.ToolReturn`
- chat 输出治理：`chat.application.tools.core.output.cache.ToolOutputCache`
- chat 正文存储：`chat.application.tools.common.tool_content_store.ToolContentStore`
- chat 后续读取：`chat.application.tools.session_tools.tool_content.services.ToolContentService`
