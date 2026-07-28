# 以模型视角使用 RAG 导航

这份文档不描述 RAG 的内部实现，而是描述我作为调用工具的模型，如何使用三个导航工具完成一次复杂的私有资料阅读。

三个工具分别解决三个问题：

```text
knowledge_navigate_locate    我应该从哪些资料开始读？
knowledge_navigate_sections  这个章节的完整内容和相邻章节是什么？
knowledge_navigate_expand    这个概念还连接到哪些其他文档和概念？
```

我不会把它们当成三个互相独立的搜索接口。一次完整阅读通常是：

```text
问题
  -> locate 找到入口 Section 和概念节点
  -> sections 读取入口及标题树邻居
  -> expand 沿跨文档关系跳到新概念
  -> sections 读取新概念所在 Section
  -> 继续 expand 或停止
```

## 场景

用户问：

> 为什么学习产品中的间隔重复推荐会把“遗忘曲线”“检索练习”和“知识点依赖”放在一起？请沿着私有笔记和论文资料，说明这套设计的理论来源、实现约束，以及它和当前课程内容的关系。

这个问题不是一次普通的关键词搜索。我要同时完成四件事：

1. 找到解释推荐策略的课程笔记；
2. 读取笔记上下文，避免只拿到一个孤立 chunk；
3. 沿概念、依赖和论文引用关系找到理论来源；
4. 回到来源 Section，核对关系证据和具体实现约束。

## 第一步：我先定位阅读入口

我第一次调用：

```json
{
  "query": "为什么间隔重复推荐会同时使用遗忘曲线、检索练习和知识点依赖？请解释理论来源、实现约束，以及它们和课程内容的关系。",
  "max_results": 8
}
```

我会把 `query` 写成完整问题，而不是拆成一组关键词。这样初始召回可以同时考虑问题中的主题、关系和范围。

我重点观察返回结果中的三类信息：

```text
sources       当前命中的 Section 和直接证据
nodes         当前证据中已经识别出的概念、资源或外部来源节点
state_id      后续所有导航调用必须继续使用的导航状态
```

对每个 `source`，我先看：

- `title` 和 `section_path`：判断它在文档中的位置；
- `summary`：判断它是否值得继续读；
- `preview`：快速确认命中内容是否真的相关；
- `frontier`：查看可以向上、向前、向后或向下走到哪里；
- `reading_blocks`：确认当前命中的正文是否已经足够回答问题。

此时我不会因为看到一个相关 chunk 就直接下结论。`preview` 只是快速概览；如果需要事实，我要读取对应的正文内容。

## 第二步：我沿 Section 读取上下文

假设 locate 返回：

```text
Section: 学习推荐 / 推荐策略 / 间隔重复
parent:    学习推荐 / 推荐策略
previous:  学习推荐 / 记忆强度
next:      学习推荐 / 检索练习
children:  学习推荐 / 推荐策略 / 评分规则
```

我会先判断当前问题需要哪种方向：

```text
需要上位定义       -> 读取 parent
需要前置概念       -> 读取 previous
需要后续步骤       -> 读取 next
需要实现细节       -> 读取 children
```

如果要理解“间隔重复”如何和“检索练习”衔接，我会读取当前 Section 和 `next`：

```json
{
  "state_id": "kns_xxx",
  "resource_id": "resource_course_notes",
  "section_ids": [
    "section_spaced_repetition",
    "section_retrieval_practice"
  ]
}
```

`knowledge_navigate_sections` 返回的是所选 Section 的完整 ReadingBlock，而不是只返回一个检索 chunk。我会按 ReadingBlock 顺序阅读，并把新的 `parent`、`previous`、`next`、`children` 视为下一层可选路径。

重要的是：我不会自动把所有 frontier 都读取一遍。每次只选择能减少当前问题不确定性的路径，避免把标题树变成无目的的全文遍历。

例如：

```text
当前 Section 只说明推荐目标，没有评分公式
  -> 读取 children: 评分规则

评分规则提到“依赖未满足时延后复习”
  -> 当前文档内的 Section 阅读暂时完成
  -> 转向 expand 检查“知识点依赖”来源
```

## 第三步：我从已读内容中选择图节点展开

阅读完 Section 后，我可能已经看到这些概念节点：

```text
遗忘曲线
检索练习
知识点依赖
间隔重复推荐器
```

我不会用 Section ID 调用 `expand`。`expand` 面向的是图节点，因此我选择返回结果中已经出现的 `node_ids`：

```json
{
  "state_id": "kns_xxx",
  "node_ids": ["entity_retrieval_practice", "entity_knowledge_dependency"],
  "relation_types": ["DEPENDS_ON", "CITES", "EXPLAINS"],
  "direction": "both",
  "max_depth": 1,
  "max_results": 10
}
```

第一次展开我通常使用 `max_depth=1`。我先确认直接关系和证据，再决定是否进行第二跳。这样可以区分：

```text
课程笔记 --CITES--> 某篇论文
课程笔记 --DEPENDS_ON--> 知识点依赖
论文     --EXPLAINS--> 检索练习
```

我不会把一条两跳路径直接当成事实。路径只是告诉我可以继续调查的方向；真正的依据在返回的 `sources` 中。

## 第四步：我回到关系证据所在的 Section

`expand` 返回的不只有节点和边，还会返回关系证据对应的 Section 来源。我会检查：

- 关系来自哪个资源；
- 证据位于哪个 `section_path`；
- 证据 Section 的 `summary` 是否和关系一致；
- `sources` 的具体正文是否明确表达了该关系。

如果关系证据位于论文笔记的“实验设计”章节，我会用返回的 `section_id` 调用：

```json
{
  "state_id": "kns_xxx",
  "resource_id": "resource_paper_notes",
  "section_ids": ["section_experiment_design"]
}
```

这样我可以看到该 Section 的完整 ReadingBlock，而不是只看图抽取时命中的一小段文本。

如果证据只说明“检索练习有助于长期保持”，但没有说明“推荐器必须结合知识点依赖”，我就不能把后一个结论写成论文事实。此时我会继续读取相关 Section，或者明确指出这是当前资料中的设计推论。

## 第五步：我按需要继续多跳

读取论文来源后，可能发现它又连接到一个外部来源节点，或者连接到课程实现中的“依赖图”。我会把新返回的 `node_ids` 作为下一次 `expand` 的输入：

```text
expand(检索练习)
  -> 论文来源
  -> expand(论文来源)
  -> 实验结论或相关理论
  -> sections(对应来源 Section)
```

每次 expand 都必须使用同一个 `state_id`，并且只提交当前状态已经返回过的节点。这样我的阅读路径是增量的：已经看过的节点不会反复作为新发现返回，新节点会加入下一轮可用范围。

我会在以下情况停止继续展开：

- 当前问题的每个结论都有正文证据；
- 新路径只重复已读节点；
- 新关系与问题无关；
- 继续读取只会增加背景，不会改变答案；
- 达到本次回答的阅读预算。

## 我对三个工具的心智模型

### `knowledge_navigate_locate`

我把它当作“建立阅读地图”：

```text
自然语言问题 -> 相关 Section + 初始概念节点 + state_id
```

它负责找入口，不负责完成全部阅读。

### `knowledge_navigate_sections`

我把它当作“打开章节并查看目录邻居”：

```text
Section ID -> 当前 Section 的完整正文 + parent/previous/next/children
```

它负责解决同一文档内部的语义连续性。

### `knowledge_navigate_expand`

我把它当作“沿概念和来源关系跳转”：

```text
知识节点 -> 有界关系路径 + 关系证据所在 Section
```

它负责解决跨文档多跳，不负责替代 Section 阅读。

## 最终阅读循环

我实际遵循的循环是：

```text
1. 用完整问题 locate，建立 state_id。
2. 查看命中 Section 的标题路径、summary、preview 和 frontier。
3. 用 sections 读取最相关的完整 Section。
4. 从正文中识别需要验证的概念、依赖或引用节点。
5. 用 expand 做一跳关系探索，优先查看直接证据。
6. 用 sections 打开关系证据所在的 Section。
7. 根据证据决定继续下一跳，还是整理答案。
```

因此，Section 导航不是另一个关键词检索系统，而是我在检索命中后恢复文档结构、控制阅读范围和组织多跳路径的方式。`locate` 给我入口，`sections` 让我沿原文标题树阅读，`expand` 让我跨文档追踪概念、依赖和来源。
