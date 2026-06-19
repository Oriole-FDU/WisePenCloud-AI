# load_skill_asset

实现入口：`src/chat/application/tools/skill_tools/load_skill_asset_tool.py`

`load_skill_asset` 按 `skill_id + path` 懒加载 Skill bundle 中的一个文本资产。它默认不暴露给模型，只能在 skill 已允许且 asset path 出现在 manifest 中时调用。

## 何时使用

- 已经通过 `load_skill` 读取了 `SKILL.md`。
- `SKILL.md` 或 assets manifest 明确要求查看某个 reference、template 或 example。
- 需要读取的是文本资产，而不是图片、PDF、WASM 等二进制资产。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `skill_id` | `string` | 必填，必须匹配本轮允许的 skill。 |
| `path` | `string` | 必填，必须和该 skill assets manifest 中的相对 POSIX path 完全一致。 |

执行上下文必须包含 `allowed_skill_ids`。

## 输出

返回 `ToolReturn(tag="loaded_skill_asset")`：

- `visible_result.skill_id`
- `visible_result.path`
- `visible_result.content`

由于策略 `persist_output=False`，历史持久化时使用占位符而不是完整 asset 内容。

## 边界

- `skill_id` 必须通过本轮白名单和权限校验。
- `path` 必须通过 manifest 白名单校验，不能发明路径，也不能使用相对路径穿越。
- preflight 会把 manifest path 映射为内部 object key，执行阶段不信任模型传入 object key。
- 资产必须能按 UTF-8 文本解码；二进制资产会被拒绝。
- 只加载单个资产，不枚举未声明文件，不读取本地路径或外部 URL。
