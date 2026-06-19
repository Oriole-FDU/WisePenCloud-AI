# create_skill 实现状态

## 已完成

### 核心流程（纯函数层，无外部依赖）

| 模块 | 状态 | 说明 |
|------|------|------|
| `models.py` | done | `SkillSection` / `SkillFile` / `SkillScript` / `CreateSkillRequest` Pydantic 模型 |
| `serializer.py` | done | `serialize_skill_markdown`（YAML frontmatter + Markdown body）、`serialize_skill_file_markdown`（无 frontmatter）、`package_skill`（zip 打包） |
| `validator.py` | done | node_id 全树唯一、body 标题检查（markdown-it-py）、路径合法性 |
| `skill_publisher.py` | done | `SkillPublisher` Protocol + `SkillPublishResult` dataclass |
| `create_skill_tool.py` | done | `CreateSkillTool` 类，含完整 PARAMETERS_SCHEMA（含 $defs/SkillFile、SkillScript）、ToolPolicy、execute 流程 |

### Schema 扩展

- 新增 `$defs/SkillFile`：references/assets 中的文件，.md 文件复用 SkillSection 标题树
- 新增 `$defs/SkillScript`：scripts/ 中的可执行脚本
- 新增 `references` / `scripts` / `assets` 可选属性

### 打包结构

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

审计字段（user_id / session_id / version / created_at / updated_at）全部来自可信运行时上下文，模型无法注入。

### 测试

38 个测试全部通过，覆盖：
- Schema 声明与 strict mode
- node_id 全树唯一性（含跨文件）
- body 标题检查（ATX / Setext / code block 不误判）
- 路径合法性（traversal / absolute）
- Markdown 序列化（结构 / 顺序 / H6+降级 / 确定性）
- zip 打包（SKILL.md / references / scripts / assets）
- 工具执行（成功 / 校验失败 / context 检查）

---

## 待补充（需明确上传逻辑后）

### 1. SkillPublisher 实际实现

**位置**：`skill_publisher.py` 中新增实现类（如 `AIAssetServiceSkillPublisher`）

**待明确**：
- Java ai-asset-service 的 Skill 上传 API 端点、请求格式
- zip 包是直接上传还是先解压再逐文件上传
- 认证方式（token / 内部签名 / 服务间调用）
- 上传失败时的重试策略

**补充方式**：实现 `SkillPublisher` Protocol 的 `publish` 方法，调用 `AIAssetClient` 上传 zip 包

### 2. skill_id 冲突检查

**位置**：`create_skill_tool.py` 的 `execute` 方法中，步骤 2 和步骤 3 之间

**待明确**：
- 冲突检查通过 `AIAssetClient` 还是 `available_skills` 索引查询
- 平台内置 Skill 的保留字列表来源
- 并发创建同一 skill_id 的原子性保证机制

**补充方式**：调用 `AIAssetClient.check_skill_exists(skill_id)` 或等价接口

### 3. available_skills 索引写入

**位置**：`create_skill_tool.py` 的 `execute` 方法中，步骤 4 之后

**待明确**：
- 索引存储位置（MongoDB / Redis / 内存）
- 索引写入与 OSS 上传的事务/补偿机制
- 索引刷新方式（实时 / 定时 / 事件驱动）

**补充方式**：调用索引写入服务，写入 skill_id / title / trigger_description / version / 所有权字段

### 4. 工具注册到运行时

**位置**：`skill_tools/__init__.py` 或工具注册入口

**待明确**：
- `CreateSkillTool` 的依赖注入方式（SkillPublisher 实例从哪里来）
- 是否需要 `AllowedSkillIdCheck` 或其他权限检查

**补充方式**：在工具注册入口添加 `CreateSkillTool` 的实例化和注册

### 5. 集成测试

**位置**：`tests/create_skill/` 目录

**待明确**：
- SkillPublisher 实际实现后需要端到端测试
- available_skills 索引写入后的查询验证

**补充方式**：新增 `test_publish_integration.py`，使用真实或 mock 的 AIAssetClient
