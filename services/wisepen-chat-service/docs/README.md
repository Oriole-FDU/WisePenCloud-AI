# WisePen Chat 文档入口

本文档目录重新分为8个主入口：

- `team/`：团队规范文档，面向代码审查、架构约束和长期维护。语气保持工程化，强调边界、规则和 review 标准。
- `important/`：已经落地、影响核心行为、需要长期追溯的关键实现说明。
- `developer/quickstart-review.html`：个人开发者快速上手与 review 入口，面向第一次接手、临时排查、加工具前的代码导览。语气更直白，先让人跑起来、看懂链路。
- `tools/`：当前已注册工具的用法和边界。按业务域分子目录，每个具体工具一个文件。
- `todo/`：即将实行、已进入近期执行范围的事项。这里不放已经完成项。
- `plans/`：有价值但短期不实行的规划。已经实现、已被规范吸收、或只记录历史背景的计划应删除。
- `ai_assist/`：单次任务的 AI 接手文档。文件名必须直接反映具体任务，不能使用笼统 `README`。
- `daily works/`：每日完成记录，面向个人回看当天做了什么。

## 重新分类结论

| 内容 | 归类 | 处理方式 |
| --- | --- | --- |
| Tool 注册、可见性、执行、输出渲染 | 团队和个人都需要 | 团队侧沉淀为规范；个人侧改写成流程图式上手说明 |
| `ToolReturn`、`cacheable_texts`、`ToolContentStore`、`tool_content_read` | 团队和个人都需要 | 团队侧定义不可绕过的缓存切面；个人侧解释什么时候用 receipt |
| Container 与 settings 边界 | 团队需要，个人只需知道入口 | 团队侧保留为规范；个人侧只提示不要乱注册 |
| Ranking / Chunking Engine | 团队和个人都需要 | 团队侧规定职责边界；个人侧解释怎么选 pipeline / scorer |
| 已完成的重要工具链收敛或协议变更 | 团队和个人都需要 | 放进 `important/`，明确背景、影响面和预期效果 |
| Web/Search/Math/Browser 迁移方案 | 团队参考，个人一般不需要 | 保留为历史迁移材料，不作为主入口 |
| 旧 HTML 设计稿 | 个人可读，团队规范不依赖 | 新入口不照搬旧 HTML 分类，只保留有助于理解的概念 |

## 主文档

- [团队规范索引](team/00-index.md)
- [Tool 架构规范](team/01-tool-architecture.md)
- [Tool 返回值与缓存规范](team/02-tool-return-and-content.md)
- [共享引擎与开发流程规范](team/03-shared-engines-and-dev-flow.md)
- [Container 与 Settings 边界规范](team/04-container-and-settings.md)
- [Utils 能力盘点](team/05-utils-inventory.md)
- [Tool 统一切面与流程规范](team/06-tool-cross-cutting-flow.md)
- [重要变更索引](important/00-index.md)
- [个人开发者快速上手与 Review](developer/quickstart-review.html)
- [Tool 用法文档](tools/)
- [计划书索引](plans/00-index.md)
- [每日工作日志索引](<daily works/00-index.md>)
- `todo/` 仅保留即将实行的事项
- `ai_assist/` 仅保留按任务命名的单次接手文档

## 清理规则

根目录不再保留散落的历史报告、旧 HTML 设计稿或已实现迁移方案。

若后续计划已经完成，应把稳定规则沉淀到 `team/` 或模块 README，然后删除对应 `plans/` 文件。
