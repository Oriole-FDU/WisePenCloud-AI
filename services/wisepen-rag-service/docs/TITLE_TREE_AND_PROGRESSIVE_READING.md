# 标题树拓展与渐进阅读启发清单

## 1. 文档目的

本文研究 `references/PageIndex` 与 `references/knowhere` 对 WisePen RAG 标题树构建、树形导航和模型渐进阅读的启发，并对照 `services/wisepen-rag-service/demo` 当前行为给出优化建议。

本文是设计研究，不代表下述建议已经实现。当前产品主流程仍是：

```text
LOCATE -> READ -> EXPAND -> VERIFY
```

建议继续由上层对话模型主导阅读决策，RAG 服务提供确定性的原子能力。

## 2. 当前 WisePen 基线

当前 demo 已具备以下基础：

- Common parser 根据标题、页标和锚点构建 `Section` 树；`parent_section_id`、`section_path`、`own_span`、`subtree_span` 和 `content_spans` 保留在事实层。
- `OutlineAssembler` 将事实层投影为模型可见 outline，节点包含 `section_id`、`title`、`length`、`page_range`、`anchor_labels` 和 `children`。
- `DocumentContentReader` 通过 `readPages/readSections` 支持确定性读取；Section READ 返回直属正文和 `allowed_directions`，具体方向由 `SectionExpander` 展开。
- LOCATE 先召回并精排 `RetrievalChunk`，再提升为完整 `ReadingBlock`，并经过 ACL、发布 revision 和 source evidence 校验。
- EXPAND 目前是知识图谱关系扩展，不是标题树的子节点展开；图谱结果最终仍回源到当前发布 revision 的 `ReadingBlock`。
- `flat_text` 文档使用 synthetic Section 保持可读入口，但不伪造页标记，也不抽取图谱关系。

当前主要缺口不是缺少树，而是模型可见的“树导航投影”仍偏静态：没有按分支限制深度、没有明确的继续展开信号、没有表达已读/未读状态，也没有把路由摘要和正文读取预算结合起来。

## 3. 参考项目拆解

### 3.1 PageIndex：结构优先、页级定向读取

PageIndex 的核心产品模型是“先生成文档树，再让 agent 在树上推理检索”。其 MCP 工具形态很清晰：

1. `get_document`：确认处理状态、页数和文档元数据。
2. `get_document_structure`：获取标题、节点 ID、页范围、摘要和子节点；大结构支持分页。
3. `get_page_content`：根据结构选择紧凑页范围读取，不鼓励一次读完整文档。

值得借鉴的机制：

- 工具描述本身规定阅读顺序，而不是只暴露无上下文的 CRUD 工具。
- 结构响应和正文响应分离，结构响应不携带完整正文，避免第一次调用就耗尽上下文。
- 节点同时有路由字段和阅读字段：标题、页范围、`summary`/`prefix_summary` 用于选择，正文只在定向读取时返回。
- 大文档结构有显式 `part`、`has_more` 和 `next_steps`，模型知道如何继续，而不是面对静默截断。
- 树构建阶段会校验标题是否真实出现在对应页、修正页边界，并对超大节点递归细化。
- `tree_optimize.py` 用确定性搜索成本模型决定合并或展开，而不是盲目追求更深树：展开只有在降低最坏路由成本时才保留；合并后的标题以 `key_items` 保留路由信息。

不应直接照搬的部分：

- PageIndex 的页索引是其内部读取单位；WisePen 的权威读取单位是字符 span、Section 和 ReadingBlock，不能把 page number 当作唯一身份。
- LLM 直接生成标题树适合补足 PDF 布局信息，但不能覆盖 WisePen 已有的 parser 事实，也不能绕过 revision、ACL 和 source verification。
- PageIndex 的文档名寻址和 cloud MCP envelope 不是 WisePen resource/session 契约的替代品。

### 3.2 knowhere：分阶段建树、动作化导航和轨迹

knowhere 的 Page Memory 与 agentic retrieval 将复杂过程拆成多个阶段：粗粒度骨架、超大叶节点细化、页范围修正、摘要/标签生成、节点组装，然后在检索时形成折叠导航图。

值得借鉴的机制：

- 粗建树和细化分开：只有“fat leaf/超大叶节点”才进入更昂贵的标题检测和 LLM 细化。
- 对边界页、同页兄弟、单子链和无层级文档有明确规则，不把所有页面平均切块。
- 导航投影不等于完整树：按字符预算折叠节点，保留命中节点及祖先，未显示分支通过 `truncated` 或继续动作表达。
- 模型看到的是合法动作，而不是自由拼接节点 ID：`COLLECT` 读取当前分支，`DISPATCH` 进入子范围，`FINISH` 结束当前范围。
- 导航状态记录已收集节点、已调查分支、动作历史、预算和停止原因；决策轨迹可用于诊断“为什么没有继续读”。
- planner、navigation、harvest、control 等阶段有显式 typed outcome，检索策略不藏在 transport adapter 内。

不应直接照搬的部分：

- knowhere 的多代理、checklist、subgoal、recursive dispatch 会显著扩大 WisePen 的状态和测试面；第一阶段不应替换现有三类 RAG 边界。
- `COLLECT/DISPATCH` 是内部 agent loop 的动作词，若直接作为公开 MCP API，会把编排实现泄漏给上层调用者。
- Page Memory 的页级 VLM 资产流程只在 PDF/多模态场景有价值，不应成为普通 Markdown/纯文本标题树的前置依赖。

## 4. 启发清单

### A. 标题树应有两个层次

保留当前 `Section`/span 作为不可变事实层；新增可重算的导航投影层。事实层回答“正文在哪里、属于哪个 Section、如何回源”，投影层回答“模型现在应该看到哪些节点、下一步能做什么”。

这样可以安全地尝试折叠、摘要、排序和分支展开，而不改变生产导入、权限或证据身份。

### B. 树深度应由收益决定

不要把“更深”当作质量目标。建议为每个候选节点估算：

```text
collapsed_cost = 节点覆盖正文的读取成本
expanded_cost = 路由摘要成本 + 最大子分支读取成本 + 未归属正文成本
```

只有 `expanded_cost < collapsed_cost` 且收益超过最小比例时才展开。对于短节点、连续单子链、同页且正文不可区分的兄弟节点，应折叠或保留为路由提示。

### C. 超大叶节点采用按需细化

索引时先使用已有 Markdown 标题树；只有超出阅读长度、检索命中但缺少可导航子节点的 Section 才进入细化。细化候选必须满足：标题原文可定位、起始位置单调、范围不越过父节点、重复标题可区分、失败时保留原叶节点。

### D. 结构响应要帮助模型做决策

每个模型可见节点至少应能回答：

- 这是什么：`title`、`section_path` 或等价路径。
- 有多大：`length`，必要时是估算 token 数。
- 是否值得进入：标题、路径和稳定 `section_id` 已足够，正文由后续 read 调用获取。
- 下一步是什么：是否有子节点、是否已被读取、是否因预算隐藏。
- 读完能否验证：稳定 `section_id`、revision 和 source reference 由内部响应保证。

### E. 渐进阅读要显式表达预算和截断

首次 locate 或 outline 返回时就告知正文总长度；后续 read 返回 `budget_exhausted`、每项 `truncated`/`reason` 和仍未读取的节点。模型应能区分“没有内容”“节点不存在”“权限不可读”和“预算不足”。

### F. 标题树与图谱是两种不同的扩展

标题树扩展回答“沿文档结构继续读哪里”；图谱扩展回答“沿显式实体关系寻找哪类证据”。两者都可以回到 ReadingBlock，但不能混用节点身份、状态或遍历规则。

## 5. 优化建议

### P0：先做导航投影，不改权威事实

1. `getDocumentOutline` 增加可选 `root_section_id`、`depth` 和 `char_budget`，默认保持现有整树行为。
2. outline 支持 `root_section_id` 和 `depth`；达到深度上限时用可选 `children_truncated` 标记仍有子节点。
3. 增加模型提示：大文档先 outline，命中后按 section 读取；父 Section 只读直属正文，子 Section 单独读取。
4. demo 增加一条多轮轨迹：LOCATE 命中 -> scoped outline -> 读取两个子 Section -> 再决定是否 graph expand。

建议的最小响应形态：

```json
{
  "resource_id": "doc-1",
  "content_revision": "rrev-1",
  "root_section_id": "sec-2",
  "total_length": 24000,
  "outline": [
    {
      "section_id": "sec-2-1",
      "title": "二、图谱导航",
      "children_truncated": true,
      "length": 8200,
      "page_range": "4 - 8"
    }
  ],
  "truncated": false,
  "next": null
}
```

### P1：加入路由摘要和树质量指标

- 为长 Section 生成短摘要，摘要只用于导航，不进入 embedding 正文；摘要失败不阻断索引。
- 统计标题覆盖率、父子范围合法率、空直属正文比例、重复标题比例、超大叶节点比例和平均树深度。
- 统计导航收益：outline 字符量、平均 read 次数、每次 read 正文量、重复读取比例、证据覆盖率和最坏路由成本。
- 将质量指标写入 demo/离线评估，不直接作为外部 API 的自由 metadata。

### P1：对超大叶节点做确定性边界修正和按需细化

- 先用已有 heading/page marker/anchor 规则筛选候选。
- 仅对候选不足且节点超过阈值的 Section 调用模型。
- 模型只返回“原文标题 + 起始位置”，服务端负责计算结束位置和验证。
- 任何失败、空结果或边界冲突都保留父叶节点，并记录可观测 reason。

### P2：实验性动作化导航

当 P0/P1 的原子工具稳定后，可增加一个服务端内部导航协调器，但不替换公开工具。内部投影可以使用：

- `collect(section_id)`：读取直属正文或该分支的已验证 ReadingBlock。
- `dispatch(section_id)`：返回该节点的下一层 outline。
- `finish(reason)`：结束当前范围并打包证据。

公开接口仍建议使用语义化的 `outline/read`，避免把 knowhere 的动作协议变成 WisePen 的长期外部契约。

## 6. 工具形态建议

### `knowledge_locate`

输入：`session_id`、`semantic_query`、可选 `lexical_query`、`max_results`。

输出：现有 `state_id`、检索状态、已核验 `reading_blocks`、图谱 seed nodes。建议额外返回每个 block 的 `total_length`/`section_ids` 规划信息，但不要把窗口长度冒充源文本总长度。

### `getDocumentOutline`

输入：`resource_id`、可选 `root_section_id`、`depth`。

输出：带稳定 `section_id` 的有限深度树、节点长度/页范围、`content_revision`；权限和 revision 校验沿用当前 `DocumentOutlineReader`。

### `readSections`

输入：`resource_id`、`section_ids`。正文读取不再携带导航黑名单；需要导航时调用 `expandSection`。

### `expandSection`

输入：`resource_id`、`section_id`、`direction`（`parent/children/previous/next`）。`children` 方向额外接受 `char_budget`（默认 12000）和 `after_section_id`，返回 `has_more`、`next_after_section_id` 与 `budget_exhausted`。

输出：指定方向的 Section 视图，包含与 `readSections` 一致的直属正文和 `allowed_directions`，并以 `from_section_id` 标记导航来源；children 方向额外返回分页游标和预算状态。

### `expandGraph`

保持现有 `state_id`、seed node、relation type、direction、depth 和 max results；明确它是关系扩展，不承担标题树子节点展开。

## 7. 不建议现在做的事

- 不要用向量召回结果直接重写标题树；召回只能标亮节点或选择 outline 根。
- 不要让 LLM 直接返回可持久化的 `start/end` offset；offset 必须由原文标题和服务端边界规则核验。
- 不要把 page、section、reading block、graph node 合并成一个通用 node 类型。
- 不要在第一版引入 per-subgoal 多代理、递归子代理、复杂 checklist 或跨文档 planner。
- 不要为了 demo 增加只在测试中使用的私有 API；保持稳定的 Common/RAG 公共导入边界。

## 8. 验收场景

1. **正常深树**：outline 只返回根和一层子节点；读取一个子节点后可继续展开其子树。
2. **宽树预算不足**：命中节点及其祖先保留，其他分支被隐藏，并返回 `truncated/next`。
3. **超大叶节点**：细化成功时子节点能回源；模型返回空或非法标题时父节点仍可读取。
4. **同页兄弟**：边界不重复归属；读取任一节点不会错误带入兄弟直属正文。
5. **flat text**：保留 synthetic Section；无页标记时不伪造 page range；不出现图谱扩展结果。
6. **权限/revision 变化**：outline 或 read 期间失权、revision 变化时返回现有明确错误，不泄露旧正文。
7. **渐进阅读轨迹**：模型能依据总长度和 `budget_exhausted` 决定继续读取、切换分支或结束，而不是重复同一调用。

## 9. 推荐落地顺序

```text
P0 scoped outline + 截断/继续信号
  -> P0 多轮 demo 与工具提示
  -> P1 路由摘要、树质量指标、阅读收益评估
  -> P1 超大叶节点按需细化
  -> P2 内部动作化导航实验
```

最终目标不是建一棵尽可能深的树，而是让模型像读代码一样：先看目录和局部摘要，选择一个明确分支，读取直属正文，沿结构或关系继续定位，并始终能回到当前发布 revision 的可验证原文。
