# load_skill

实现入口：`src/chat/application/tools/skill_tools/load_skill_tool.py`

`load_skill` 按 `skill_id` 懒加载已发布 Skill 的 `SKILL.md` 正文和 assets manifest 摘要。它默认不暴露给模型，只能在本轮工具上下文允许该 skill 时调用。

## 何时使用

- 用户请求直接命中了系统上下文中的 Available Skills。
- 需要读取完整 `SKILL.md` 指令来执行后续任务。
- 只有 `SKILL.md` 或 manifest 明确要求时，才继续使用 `load_skill_asset` 打开具体资产。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `skill_id` | `string` | 必填，必须匹配本轮 Available Skills 中允许的 id。 |

执行上下文必须包含 `allowed_skill_ids`。

## 输出

返回字符串：

- `[Loaded Skill]` 头部包含 `skill_id`、version 和名称。
- `<skill>` 包含 UTF-8 解码后的 `SKILL.md` 正文。
- 若存在 assets manifest，返回每个 asset 的 path、kind、size 和 description，并提示使用 `load_skill_asset` 打开资产。

由于策略 `persist_output=False`，历史持久化时使用占位符而不是完整 skill 内容。

## 边界

- `skill_id` 必须通过 `AllowedSkillIdCheck`，不能由模型臆造。
- 必须通过 `SkillPermissionCheck`，当前用户需要对该 skill 有 `VIEW` 权限。
- 只加载 `SKILL.md` 和 manifest 摘要，不加载资产正文。
- `SKILL.md` 必须是 UTF-8 文本；缺失、加载失败或无法解码会返回工具错误。
- 默认超时 8 秒，风险级别为 medium。
