# Tool 架构规范

本文约束 WisePen Chat Service 当前工具体系的注册、可见性、执行和审查边界。

统一切面、后台队列、URL 缓存和工具开发流程见 [Tool 统一切面与流程规范](06-tool-cross-cutting-flow.md)。本文只约束 Tool 框架主链路。

## 真实链路

当前工具链路以这些文件为准：

- `src/chat/container.py`
- `src/chat/application/tools/core/definition.py`
- `src/chat/application/tools/core/registry.py`
- `src/chat/application/tools/core/execution/dispatcher.py`
- `src/chat/application/tools/core/execution/executor.py`
- `src/chat/application/tools/tool_output_renderer.py`
- `src/chat/application/tools/tool_output_cache.py`

执行顺序：

```text
container provider
  -> ToolRegistry.register()
  -> ChatTurnCoordinator 派生 ToolScope
  -> ToolScope.schemas()
  -> LLM tool call
  -> ToolDispatcher
  -> ToolExecutor preflight
  -> tool.execute()
  -> ToolOutputRenderer
  -> ToolOutputCache
  -> RenderToolResult
```

## Tool 基本契约

业务工具实现以下协议即可，不需要继承框架基类：

```python
@property
def definition(self) -> ToolDefinition:
    ...

async def execute(self, context: dict[str, Any], **kwargs: Any) -> Any:
    ...
```

`definition` 描述工具如何暴露、校验和执行；`execute` 只写业务逻辑。

## 统一递归渲染

中文强调：普通工具直接返回普通 Python 值，例如 `dict`、`list`、dataclass、Pydantic model、scalar 或 `None`。统一工具渲染器 `ToolOutputRenderer` 会递归标准化并渲染结果。工具不要为了“结构化”手动转 result，不要手写 XML，不要为普通返回值增加私有 result_builder。

English emphasis: ordinary tools return ordinary Python values. `ToolOutputRenderer` performs unified recursive rendering. Tool implementations should not manually convert results only for rendering or structure.

UPPERCASE EMPHASIS: UNIFIED RENDERING IS THE RETURN BOUNDARY. DO NOT MANUALLY CONVERT RESULTS FOR RENDERING. DO NOT BUILD PRIVATE RESULT PAYLOADS JUST FOR STRUCTURE.

## 注册规则

新增工具必须：

- 放在 `src/chat/application/tools/<domain>_tools/` 或已有业务域目录下。
- 通过 `container.py` 创建 provider。
- 加入 `tool_providers` 后由 `_build_registry()` 注册。
- 使用全局唯一的 `definition.llm_spec.name`。

不得：

- 把业务工具放进 `tools/core/`。
- 在工具内部创建第二套 registry、dispatcher 或 executor。
- 绕过 `ToolRegistry.derive()` 直接把全量工具 schema 交给 LLM。

## 可见性规则

`ToolPolicy.expose_by_default=True` 表示普通请求默认可见。适用于低风险、通用、经常需要的工具。

`ToolPolicy.expose_by_default=False` 表示默认隐藏。适用于 skill 工具、场景工具、成本高或能力边界窄的工具。隐藏工具只有进入 `expose_tool_name_set` 后才会出现在本轮 `ToolScope`。

当前 `ToolRegistry.derive()` 的行为要点：

- 默认隐藏工具只检查 `expose_tool_name_set`。
- 默认暴露工具受 `allow_tool_name_set` 和 `deny_tool_name_set` 过滤。
- `ToolScope.schemas()` 是本轮稳定快照，运行期 LLM 调用必须使用它。

如果将来要让 deny 也能压制隐藏工具，需要先修改 `derive()`，不能只改业务调用方。

## Preflight 规则

`ToolExecutor` 固定执行：

1. `JsonSchemaCheck`
2. `RequiredContextCheck`
3. 工具自定义 `preflight_hooks`

安全上下文必须来自 `context`，不能让模型通过参数传入。例如 `session_id`、`user_id`、权限范围、业务租户信息都应进入 `required_context_keys` 或可信上下文。

## 并发与副作用

当前 `ToolDispatcher` 使用 `asyncio.gather()` 并发执行同轮所有 tool call。`ToolPolicy.allow_parallel` 已存在，但当前 dispatcher 尚未按该字段调度。

因此新增有副作用工具时必须特别审查：

- 是否写外部系统。
- 是否依赖同一资源顺序。
- 是否可能并发创建重复数据。
- 是否需要在 dispatcher 层补串行策略后才能上线。

## 新增工具 Review 清单

- `name` 是否全局唯一且语义清楚。
- `description` 是否说明何时使用，而不是堆实现细节。
- JSON Schema 是否是 object，`required` 是否只引用已定义字段。
- 是否声明 `timeout_seconds`。
- 安全上下文是否走 `required_context_keys`。
- 普通结构化结果是否交给统一递归渲染，而不是工具内手动转 result。
- 大文本是否按 `ToolReturn.cacheable_texts` 交给统一切面。
- 是否误把工具内部 helper 注册成 container provider。
- 是否需要默认暴露；如果不是，谁负责加入 `expose_tool_name_set`。

## 当前稳定工具约定

`tool_content_read`

- 默认暴露。
- 只通过 `content_ids` 批量读取 `cnt_*`。
- 一次调用内所有 `content_ids` 共用同一组读取参数。
- 单项读取失败返回 failed item，不拖垮整次工具调用。

`document_parse`

- 默认暴露。
- 通过 `mode` 明确区分 `from_web_fetch` 与 `from_direct_urls`。
- `from_web_fetch` 只接受 `file_refs: tfile_*[]`，通过 `ToolRunFileStore.resolve_ref(...)` 解析真实文件路径。
- `from_direct_urls` 只接受明显非 HTML 文件直链 URL，下载后复用同一文件解析链。
- `file_refs` 与 `direct_urls` 互斥；普通 HTML 页面仍使用 `web_fetch` / `web_crawl`。
- web 来源的解析结果必须回写统一 URL 缓存路径。
- 内部并发解析，单项失败返回 failed item。
- 成功文件的 Markdown 进入 `ToolReturn.cacheable_texts`，由输出缓存切面分批生成多个 `cnt_*`。

`math_tools`

- 默认暴露。
- 拆成 `calculus_solver`、`linear_algebra_solver`、`equation_solver`、`stats_solver`、`expression_solver` 5 个窄工具。
- 工具门面只负责 schema、description、policy 和 service 调度。
- 无状态 service 负责 SymPy / NumPy / SciPy 调用。
- 固定 task 集合使用 `StrEnum`，schema 和 service 从同一枚举来源读取。
- 同类 helper 聚合为命名空间类，例如 task registry、expression parser、payload reader、result formatter。
- 普通结果返回 dataclass，由统一递归渲染处理。
- UNIFIED RENDERING IS THE RETURN BOUNDARY.

工具之间的文件传递必须使用 `tfile_*`；工具之间的大文本传递必须使用 `cnt_*`。不得把本地路径、base64、OSS key 或工具私有缓存 ID 混入这两个协议。
