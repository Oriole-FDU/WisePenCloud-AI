# 动态工具 Schema 披露计划

## 当前缺口

当前 `ToolScope` 在构造时一次性渲染工具 schema：

- `ToolRegistry.derive()` 生成请求级 `ToolScope`。
- `ToolScope.schemas()` 返回固定 schema 列表。
- `QueryLoopRuntime` 在每轮 LLM request 前直接读取该列表。

这对简单工具足够，但复杂工具仍可能一次性暴露太多参数。例如当前读取工具已经拆成跨文档检索和单文档顺序读取两个入口，后续如果某个工具再次同时承载多组强互斥参数，仍可能需要动态 schema 披露。

## 目标

在每轮 LLM request 前，根据当前对话状态、工具执行结果和请求上下文，动态生成当前可见工具 schema。

第一阶段曾以 `tool_content_read` 为试点思路：

1. 初始 schema 只披露 `content_id` 和 `mode`。
2. 模型选择 mode 后，工具或运行时记录下一轮 schema 状态。
3. 下一轮只披露该 mode 需要的参数。
4. 完成读取后清理 schema 状态。

## 非目标

- 不在同一次 tool call arguments 流式生成过程中热替换 schema。
- 不优先依赖复杂 JSON Schema `oneOf/if/then` 表达所有条件。
- 不为了 schema 动态化而额外制造低价值工具名。

## 建议落地顺序

1. 为 `ToolScope` 增加 schema render context，而不是只缓存静态 schema。
2. 为工具定义可选的 schema state / schema renderer 扩展点。
3. 让工具执行结果可以返回下一轮 schema state。
4. 在 `QueryLoopRuntime` 每轮 request 前重新渲染 schema。
5. 为目标工具实现 mode-specific schema。
6. 补测试：schema 状态生命周期、mode 切换、失败后清理。

## 完成后处理

完成后，把稳定规则合并到 `docs/team/01-tool-architecture.md`，并删除本计划。
