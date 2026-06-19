# Tool 文档

本目录记录当前已注册工具的使用边界、内部运行机制、工具链协作方式、模型约束和后续优化方向。工具按业务域分组；每个具体工具一个文件，跨工具公共机制见 [toolchain_architecture](toolchain_architecture.md)。

本文档不是参数清单的重复抄写。阅读顺序建议：

1. 先读 [toolchain_architecture](toolchain_architecture.md)，理解 `ToolReturn`、`cnt_*`、`tfile_*`、URL 缓存、search_ref 和模型约束。
2. 再读目标工具页面，确认何时触发、何时禁止触发，以及输出如何交给下一个工具。
3. Review 或扩展工具时，同时检查单工具提示词和跨工具协议是否仍然一致。

## 分组

- `document/`：文件解析工具。
- `math/`：结构化数学计算工具。
- `session/`：会话历史与 `cnt_*` 内容读取工具。
- `skill/`：Skill 指令和文本资产懒加载工具。
- `web/`：搜索、抓取、站点爬取和显式 hydration 工具。

## 全局机制

- [toolchain_architecture](toolchain_architecture.md)

## 维护要求

每个工具页必须覆盖以下内容，缺一项时 review 应要求补齐：

- **实现入口**：tool 门面、内部 service、关键 repository/provider。
- **触发边界**：何时使用、何时禁止使用，尤其是与相邻工具的分工。
- **参数契约**：schema 能表达的规则，以及 execute/preflight 才能表达的互斥、权限、跨字段语义。
- **内部机制**：真实调用链、fallback、缓存、排序、解析或外部 provider。
- **统一切面**：是否经过 `ToolReturn`、`ToolOutputCache`、`ToolContentStore`、`ToolRunFileStore`、`web_content_cache`、refresh worker 或 GC。
- **协作链**：上游工具产物如何传入，下游工具如何继续消费。
- **模型约束**：模型不得伪造的引用、不得绕过的步骤、不得误用的预览/metadata。
- **可插拔点**：provider、parser、fetcher、cleaner、ranking、chunking、publisher 等可替换实验边界。
- **后续优化**：提示词、缓存策略、测试覆盖、切面收敛或可观测性。

新增或修改工具时，优先修改 [toolchain_architecture](toolchain_architecture.md) 中的统一切面或工具族流程，再修改单工具页。不要只改参数表。

## 统一切面速查

| 切面 | 文档入口 |
| --- | --- |
| Tool 注册、可见性、preflight、dispatcher | [team/01-tool-architecture](../team/01-tool-architecture.md) |
| `ToolReturn`、递归渲染、`cnt_*` | [team/02-tool-return-and-content](../team/02-tool-return-and-content.md) |
| 跨工具统一流程、URL cache、后台 worker/GC | [team/06-tool-cross-cutting-flow](../team/06-tool-cross-cutting-flow.md) |
| 共享引擎与开发流程 | [team/03-shared-engines-and-dev-flow](../team/03-shared-engines-and-dev-flow.md) |
| DI 与 settings | [team/04-container-and-settings](../team/04-container-and-settings.md) |

## 工具索引

### document

- [document_parse](document/document_parse.md)

### math

- [calculus_solver](math/calculus_solver.md)
- [linear_algebra_solver](math/linear_algebra_solver.md)
- [equation_solver](math/equation_solver.md)
- [stats_solver](math/stats_solver.md)
- [expression_solver](math/expression_solver.md)

### session

- [tool_content_read](session/tool_content_read.md)
- [tool_content_sequential_read](session/tool_content_sequential_read.md)
- [get_historical_chat_messages](session/get_historical_chat_messages.md)

### skill

- [load_skill](skill/load_skill.md)
- [load_skill_asset](skill/load_skill_asset.md)
- [create_skill](skill/create_skill.md)

### web

- [web_search](web/web_search.md)
- [web_fetch](web/web_fetch.md)
- [web_crawl](web/web_crawl.md)
- [paper_hydrate](web/paper_hydrate.md)
- [github_hydrate](web/github_hydrate.md)
