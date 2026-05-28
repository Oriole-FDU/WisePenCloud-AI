## Wisepen-Sandbox-Service

### 1. 目的

实现可安全执行命令与脚本的沙箱，及其的生命周期的动态管理。

### 2. 文件架构

核心代码位于 `src/sandbox/`，当前已形成“抽象接口 + 可插拔实现”的分层结构：

- `src/sandbox/LifeSpan/`：沙箱生命周期管理（Provider/Factory 模式，默认 DockerProvider）
  - 抽象与门面：[sandboxLifespan.py](src/sandbox/LifeSpan/sandboxLifespan.py)
  - 默认工厂（注册 docker provider）：[sandbox_factory.py](src/sandbox/LifeSpan/factory/sandbox_factory.py)
  - DockerProvider 实现（通过 docker CLI）：[docker_provider.py](src/sandbox/LifeSpan/providers/docker_provider.py)
- `src/sandbox/ScriptExecutor/`：结构化请求解析、脚本包解析与执行编排（当前以数据结构/接口为主）
  - 脚本包与解析抽象：[scriptReader.py](src/sandbox/ScriptExecutor/scriptReader.py)
  - 执行请求/结果数据结构与执行接口：[scriptExecutor.py](src/sandbox/ScriptExecutor/scriptExecutor.py)
  - 解析器骨架：`parsers/`（python/bat 等）
  - 脚本包仓库骨架：`package_repo/`（本地/对象存储等）
  - 执行编排骨架：`execution/`（request_parser/executor_impl/runners 等）
- `src/sandbox/ResultReturn/`：结果存储 + 面向 Chat 的文本化输出（Tool 返回）
  - 结果聚合与回传：[returnResult.py](src/sandbox/ResultReturn/returnResult.py)
  - Tool 文本格式化：[tool_text_formatter.py](src/sandbox/ResultReturn/formatters/tool_text_formatter.py)
- `src/sandbox/api/`：配置中心/注册发现抽象（Nacos）
  - 抽象接口：[nacosClient.py](src/sandbox/api/nacosClient.py)
- `src/sandbox/web/`：日志抽象
  - 抽象接口：[logger.py](src/sandbox/web/logger.py)
- `src/sandbox/transport/http/`：HTTP DTO 骨架（用于对外 API 的请求/响应格式约定）
  - DTO 定义：[schemas.py](src/sandbox/transport/http/schemas.py)

### 3. 功能接口

* 脚本包/多文件解析抽象：[scriptReader.py](src/sandbox/ScriptExecutor/scriptReader.py)
* 结构化执行请求与结果模型：[scriptExecutor.py](src/sandbox/ScriptExecutor/scriptExecutor.py)
* 沙箱生命周期（Provider/Factory + 默认 DockerProvider）：[sandboxLifespan.py](src/sandbox/LifeSpan/sandboxLifespan.py)
* 结果存储与 Tool 文本回显：[returnResult.py](src/sandbox/ResultReturn/returnResult.py)

### 4. 沙箱调用请求格式（建议的对外契约）

对外建议提供一个“结构化执行”入口（例如 `POST /v1/sandbox/execute`）。本仓库目前提供了 HTTP DTO 的骨架定义（见 [schemas.py](src/sandbox/transport/http/schemas.py)），推荐请求体使用 JSON，字段如下。

#### 4.1 ExecuteRequest（JSON）

```json
{
  "package_id": "pkg_xxx",
  "entry": "main.py",
  "args": ["--foo", "bar"],
  "env": {"MODE": "test"},
  "timeout_ms": 60000,
  "limits": {
    "cpu_cores": 1,
    "memory_mb": 256,
    "pids_limit": 64,
    "disk_mb": 512,
    "network_enabled": false
  }
}
```

字段说明：

- `package_id`：脚本包引用（推荐）。脚本文件不建议经由 tool args 直接传输；上层应先把文件落盘/入对象存储生成 `package_id`。
- `entry`：入口文件路径（多文件包时建议必填，例如 `main.py`）。
- `args`：命令参数数组（禁止拼接成字符串命令）。
- `env`：环境变量键值对（实际生效应按白名单过滤）。
- `timeout_ms`：超时时间（毫秒）。
- `limits`：资源限制（字段名建议与 `SandboxLimits` 对齐，便于映射）。

安全约束：

- `session_id/user_id/trace_id` 等安全上下文应由网关/Chat 强注入，不应由 LLM/用户在请求体中传入。

### 5. 沙箱返回结果格式

沙箱执行结果存在两种消费形态：

1) 面向服务/接口层的结构化 JSON（便于存储与可观测）
2) 面向 Chat Tool Calling 的字符串（便于 LLM 继续推理）

#### 5.1 ExecuteResponse（JSON）

对应 [ExecuteResponseDTO](src/sandbox/transport/http/schemas.py) 与核心模型 `ExecutionResult`（见 [scriptExecutor.py](src/sandbox/ScriptExecutor/scriptExecutor.py)）。示例：

```json
{
  "request_id": "req_xxx",
  "status": "succeeded",
  "sandbox_id": "container_id_or_sandbox_id",
  "exit_code": 0,
  "stdout": "ok\n",
  "stderr": "",
  "duration_ms": 1234,
  "artifacts": [
    {"name": "stdout_full", "uri": "file:///.../stdout.txt"}
  ]
}
```

字段说明：

- `status`：`succeeded|failed|timeout|cancelled`（与 `ExecutionStatus` 对齐）。
- `artifacts`：产物引用列表（可选，用于 stdout/stderr 超长落盘、输出文件打包等）。

#### 5.2 Tool 返回文本（chatReturn 输出）

`Result.chatReturn(result)` 会返回稳定字段的文本（见 [tool_text_formatter.py](src/sandbox/ResultReturn/formatters/tool_text_formatter.py)），示例：

```text
[Sandbox Execution]
status: succeeded
sandbox_id: ...
exit_code: 0
duration_ms: 1234
stdout:
<stdout...>
stderr:
<stderr...>
artifacts:
- name=stdout_full uri=...
```
