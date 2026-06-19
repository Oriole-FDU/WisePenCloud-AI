# 团队规范索引

本目录只放需要团队共同遵守的工程规范。它不追求解释所有历史背景，而是约束当前仓库应如何演进。

## 规范文档

1. [Tool 架构规范](01-tool-architecture.md)
2. [Tool 返回值与缓存规范](02-tool-return-and-content.md)
3. [共享引擎与开发流程规范](03-shared-engines-and-dev-flow.md)
4. [Container 与 Settings 边界规范](04-container-and-settings.md)
5. [Utils 能力盘点](05-utils-inventory.md)
6. [Tool 统一切面与流程规范](06-tool-cross-cutting-flow.md)

## 总原则

- 运行入口保持单一：`src/chat/container.py` 是应用级依赖图入口。
- Tool 框架能力集中在 `src/chat/application/tools/core/`，业务工具不得回写框架规则。
- 普通工具返回普通 Python 值；只有需要运行时托管大文本时才返回 `ToolReturn`。
- 大文本读取统一走 `ToolContentStore` 和 `tool_content_read`，不得在工具内自建并行读取协议。
- 共享引擎只承担共享基础能力；规则见 [共享引擎与开发流程规范](03-shared-engines-and-dev-flow.md)。
- Tool 统一切面必须优先复用：preflight、渲染、输出缓存、`ToolContentStore`、`ToolRunFileStore`、`web_content_cache`、刷新队列、GC 和 suggested actions 都不是单个工具的私有逻辑。
- 新增工具或服务前先查 `Utils 能力盘点`，不得重复实现已有分块、排序、文件识别、Markdown 渲染或轻量 LLM helper。
- 文档、网页、搜索、文件解析等迁移方案只能作为设计参考，不能覆盖本目录规范。
