# Tool 并发调度策略计划

## 当前缺口

`ToolPolicy.allow_parallel` 已存在，但 `ToolDispatcher` 当前对同轮 tool calls 直接使用 `asyncio.gather()` 并发执行，没有读取该策略。

这对只读工具通常没问题，但对写操作、外部副作用、共享资源互斥工具存在风险。

## 目标

让 dispatcher 尊重工具级并发策略：

- 可并发工具可以继续同轮并发。
- 不可并发工具必须按稳定顺序串行执行。
- 未来可支持资源锁或 group key。

## 非目标

- 不在工具内部自行加全局锁替代调度策略。
- 不把所有工具都改成串行。
- 不改变 LLM tool call 协议。

## 建议落地顺序

1. 在 `ToolScope` 中允许 dispatcher 查询 tool definition。
2. `ToolDispatcher` 将 invocations 分成 parallel-safe 和 serial-required。
3. 串行部分按模型输出顺序执行。
4. 并发部分可在安全阶段使用 `asyncio.gather()`。
5. 定义错误传播和输出顺序规则：最终 `ToolBatchResult` 应保持 invocation 原顺序。
6. 补测试：全部并发、全部串行、混合调度、未知工具、超时。

## 完成后处理

完成后，把规则合并到 `docs/team/01-tool-architecture.md`，并删除本计划。
