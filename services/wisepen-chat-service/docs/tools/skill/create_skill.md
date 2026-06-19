# create_skill

实现入口：`src/chat/application/tools/skill_tools/create_skill_tool.py`

`create_skill` 从结构化标题树创建并发布新的 Skill 文档，打包为符合 Agent Skills 开放规范的 zip 包后通过 `SkillPublisher` 发布。它默认不暴露给模型，风险级别为 HIGH。

## 何时使用

- 用户明确要求创建一个新的 Skill。
- 用户希望将某个工作流、指令集或最佳实践保存为可复用的 Skill。
- 用户要求将一组操作步骤整理成可被其他会话发现和加载的 Skill。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `skill_id` | `string` | 必填，全局唯一英文 kebab-case slug，创建后不可变更，用作 OSS 对象 key 和索引键。 |
| `title` | `string` | 必填，Skill 文档标题，渲染为唯一 H1，也作为人类可读展示名。 |
| `trigger_description` | `string` | 必填，完整描述该 Skill 应在什么场景被触发使用，是 available_skills 目录中唯一的触发判断依据，必须自包含且足够具体。 |
| `body` | `string` | 必填，H1 与第一个 H2 之间的正文，原生 Markdown 语法；若直接进入分节可留空字符串。 |
| `children` | `SkillSection[]` | 必填，H2 及更深层的标题树，递归结构。 |
| `references` | `SkillFile[]` | 可选，按需加载的参考文档。 |
| `scripts` | `SkillScript[]` | 可选，可执行脚本文件。 |
| `assets` | `SkillFile[]` | 可选，模板与资源文件。 |

执行上下文必须包含 `user_id` 和 `session_id`。

### SkillSection（递归）

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `node_id` | `string` | 必填，英文 kebab-case slug，全树唯一，用于增量定位和局部更新。 |
| `heading` | `string` | 必填，标题文本（不含 `#`，层级由树位置自动推算）。 |
| `body` | `string` | 必填，该标题下的正文，原生 Markdown；不允许包含 Markdown 标题语法。 |
| `children` | `SkillSection[]` | 必填，子标题树，空数组表示叶子节点。 |

### SkillFile

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `path` | `string` | 必填，相对路径（如 `api-guide.md`），不允许 `..` 或绝对路径。 |
| `title` | `string` | 可选，.md 文件的 H1 标题；省略则从文件名推导。 |
| `body` | `string` | 必填，文件内容。 |
| `children` | `SkillSection[]` | 必填，.md 文件的标题树结构；非 .md 文件留空数组。 |

### SkillScript

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `path` | `string` | 必填，scripts/ 下的文件名，不允许 `..` 或绝对路径。 |
| `body` | `string` | 必填，脚本源代码。 |

## 输出

返回 dict：

```json
{
  "skill_id": "my-skill",
  "version": 1,
  "status": "published",
  "published_at": "2026-06-18T17:49:42.187574+00:00"
}
```

不返回 OSS 内部凭据、未签名地址、完整可信 metadata 或用户身份信息。

## 打包结构

遵循 Agent Skills 开放规范 (https://agentskills.io/specification)：

```
{skill_id}/
├── SKILL.md          # YAML frontmatter (name/description/metadata) + Markdown body
├── references/       # 按需加载的参考文档
│   └── *.md          # 复用标题树序列化
├── scripts/          # 可执行脚本
│   └── *.py / ...
└── assets/           # 模板与资源
    └── *.md          # 复用标题树序列化
```

### SKILL.md YAML Frontmatter

```yaml
---
name: {skill_id}
description: |-
  {trigger_description}
metadata:
  version: "1"
  user_id: "..."
  session_id: "..."
  created_at: "2026-06-18T17:49:42.187574+00:00"
  updated_at: "2026-06-18T17:49:42.187574+00:00"
---
```

审计字段全部来自可信运行时上下文，模型无法注入或覆盖。

## 业务校验

Tool 层在委托 Service 前执行以下 JSON Schema 无法表达的语义校验：

1. **node_id 全树唯一**：整棵 children 树（含 SKILL.md 和所有 references/assets 中的 .md 文件）的 node_id 必须全局唯一；重复时错误信息包含首次路径和重复路径。
2. **body 不含标题**：根 body 和每个 section 的 body 不得包含 Markdown 标题语法（ATX / Setext）；使用 markdown-it-py 解析 token，正确区分代码块中的 `#`。
3. **路径合法性**：references / assets / scripts 的 path 不允许包含 `..` 或以 `/` 开头。

校验失败时不写 OSS、不更新索引、不产生半发布状态。

## 内部架构

```
CreateSkillTool (校验 + 适配层)
  ├── 参数解析 → CreateSkillRequest
  ├── 业务校验 → validate_create_skill()
  └── 委托 → CreateSkillService
               ├── package_skill() → zip bytes
               └── SkillPublisher.publish() → SkillPublishResult
```

- **Tool**：只做参数解析、业务校验和结果适配，不包含编排逻辑。
- **Service**：编排打包和发布，不关心校验和存储细节。
- **Serializer**：纯函数，确定性序列化，不鉴权、不写存储。
- **Publisher**：Protocol 接口，实现委托给 Java ai-asset-service。

## 边界

- `user_id` / `session_id` 由 `required_context_keys` 保证存在，不从模型参数获取。
- metadata 中的审计字段（version / user_id / session_id / created_at / updated_at）全部来自可信上下文或服务端生成，模型可控字段不能覆盖。
- `skill_id` 冲突检查待 Publisher 实现后补充（见 IMPLEMENTATION_STATUS.md）。
- available_skills 索引写入待上传逻辑明确后补充。
- 默认超时 15 秒，风险级别为 HIGH。
