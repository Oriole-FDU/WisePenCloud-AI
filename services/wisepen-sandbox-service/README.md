# WisePen 抽象沙箱管理服务

## 1. 文档说明

本文是 WisePen 沙箱系统的实现文档，合并了原 `docs/sandbox-design.md` 的设计、代码实现说明、提交演进、问题修复和真实 AIO 试跑结果。

系统由两个层次组成：

```text
wisepen-sandbox-service
    -> application services
        -> domain interfaces
            -> core.providers.aio_adapter
            -> AIO Sandbox Container
```

`wisepen-sandbox-service` 通过 `domain.interfaces` 暴露平台无关的 `SandboxProvider`、`WorkspaceStore` 和 `LeaderLease` 端口；AIO 协议、Docker CLI 和平台路径差异封装在 `core.providers.aio_adapter` 中。

当前实现采用进程内 Repository、WorkspaceStore 和 LeaderLease，便于开发和测试；后续可以替换为 Redis、Mongo 或对象存储实现，而不改变调度核心规则。

## 2. 设计目标与边界

系统通过预先启动一组空闲沙箱降低用户请求延迟。用户请求到来后，Scheduler 原子地取得 READY 沙箱，复制工作区、激活实例并创建短期租约。任务结束后，工作区提交回存储，沙箱默认销毁；Watcher 观察 READY 数量并补充新的预热实例。

设计目标：

- 预热实例只在健康检查完成后进入 READY Pool；
- 调度逻辑与 AIO、Docker 等具体平台解耦；
- 通过 request_id 幂等、租约和 fencing token 防止重复分配及旧请求写入；
- 用户实例不携带用户数据回 READY，避免跨租户状态泄漏；
- 通过 Watcher 自动恢复过期租约、清理超时实例并补充容量；
- 为后端协议和前端/VNC 协作者提供稳定的 lease、endpoint、错误码和状态边界。

本文不定义前端界面、VNC 编码/转发协议，也不实现完整 AIO SDK。Chat 端只应调用 Sandbox Service，不感知 AIO endpoint 的生成方式、Docker container ID 或 Adapter 内部 DTO。

## 3. 服务架构

```mermaid
flowchart LR
    User[用户或后端] --> API[Sandbox API]
    API --> Scheduler[Sandbox Scheduler]
    Scheduler --> Pool[Sandbox Pool]
    Scheduler --> Workspace[WorkspaceStore]
    Scheduler --> Provider[SandboxProvider]
    Watcher[Watcher] --> Pool
    Watcher --> Scheduler
    Watcher --> Provider
    Pool --> Repository[Sandbox Repository]
    Scheduler --> Repository
    Provider --> Adapter[AIO Adapter]
    Adapter --> Runtime[DockerRuntime]
    Runtime --> AIO[AIO Sandbox Container]
```

### 3.1 `wisepen-sandbox-service`

- `application/services`：维护 Pool、Scheduler 和 Watcher 的生命周期用例。
- `domain`：保存领域实体、错误码、端口和 Repository 协议。
- `core/storage/memory`：提供内存 Repository 和 LeaderLease 实现。
- `core/storage/local` / `core/observability`：提供本地 Workspace Store 和 Metrics 实现。
- `apis` / `main.py`：提供内部 API、路由和进程启动时的 Watcher 后台任务。

### 3.2 `core.providers.aio_adapter`

- `DockerRuntime`：使用 Docker CLI 创建、inspect、获取动态端口和删除容器。
- `AioClient`：使用 httpx 调用最小 AIO HTTP API，统一处理响应包装、超时和错误。
- `AioSandboxProvider`：实现 SandboxProvider，将领域操作映射为 AIO 文件、Shell 和代码执行请求。
- `PathPolicy`：校验绝对/相对路径、`..`、反斜杠、租户和 workspace 隔离。
- `AdapterConfig`：配置镜像、AIO 端口、工作根目录、超时、TTY 和 e2e 标签。
- `models.py`：保存当前文件、Shell、执行和 Docker 生命周期所需的配置 DTO；错误统一使用 common 的 `ServiceException` 和本目录的 `error_codes.py`。

没有迁移完整的 TypeScript、Python 生成 SDK、Go SDK、浏览器、MCP、Jupyter、Node.js、网站、示例或评测代码。

## 4. 生命周期与领域模型

正常生命周期为：

```text
CREATING -> WARMING -> READY -> ALLOCATED -> RUNNING
RUNNING -> SYNCING -> DESTROYING -> DESTROYED
```

异常销毁最终进入 `LOST`，不回 READY。

| 状态 | 含义 | 允许的后继状态 |
| --- | --- | --- |
| `CREATING` | 已提交创建，尚未健康 | `WARMING`、`DESTROYING` |
| `WARMING` | 正在等待平台就绪 | `READY`、`DESTROYING` |
| `READY` | 健康且无租约，可 checkout | `ALLOCATED`、`DESTROYING` |
| `ALLOCATED` | 已绑定租约，正在准备环境 | `RUNNING`、`DESTROYING` |
| `RUNNING` | 用户正在使用 | `SYNCING`、`DESTROYING` |
| `SYNCING` | 正在提交工作区 | `DESTROYING` |
| `DESTROYING` | 正在调用平台销毁 | `DESTROYED`、`LOST` |
| `DESTROYED` | 已确认销毁 | 无 |
| `LOST` | 无法确认销毁或平台失联 | 无 |

非法状态转换由 Repository 统一抛出 `INVALID_STATE_TRANSITION`。用户实例不实现 reset/reuse，因此释放后不会直接回 READY。

核心标识包括 `sandbox_id`、`lease_id`、`request_id`、`tenant_id`、`workspace_id` 和 fencing token。`provider_id` 只在 Adapter 和内部记录中使用；管理 API 的状态响应会移除 provider_id、metadata 和 endpoint token。

### 4.1 Pool 原子语义

`checkout_ready()` 在同一把锁内完成 READY 选择、状态改为 ALLOCATED、租约创建、request 映射和单调 fencing token 分配。并发请求不会取得同一个沙箱。

Watcher 通过以下顺序将预热实例加入 Pool：

```text
WARMING
  -> prepare_readiness(health_token)
  -> return_ready(sandbox_id, health_token, expected_generation)
  -> READY
```

`return_ready` 要求状态为 WARMING、无 lease/request/tenant/workspace、health token 正确且 generation 未变化。用户释放路径禁止调用该接口。

### 4.2 租约与 fencing

- 同一 `request_id` 重试返回原租约；相同 request_id 携带不同租户或工作区返回 `REQUEST_CONFLICT`。
- 每次新分配生成单调 fencing token。
- execute 必须校验 lease_id、tenant_id、workspace_id、request_id 和 fencing token。
- 租约过期、release 开始或 fencing token 不匹配后，新的 execute 被拒绝。
- release 先关闭租约入口，再执行同步和销毁；重复 release 不重复 commit/destroy。

## 5. 端口与 API

### 5.1 SandboxProvider

```python
class SandboxProvider(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxRef: ...
    async def wait_ready(self, sandbox: SandboxRef, timeout_seconds: float) -> Health: ...
    async def health(self, sandbox: SandboxRef) -> Health: ...
    async def prepare_workspace(self, sandbox: SandboxRef, workspace: WorkspaceSnapshot) -> None: ...
    async def activate(self, sandbox: SandboxRef, lease: SandboxLease) -> Endpoint: ...
    async def forward(self, sandbox: SandboxRef, request: ExecutionRequest) -> ExecutionResult: ...
    async def export_workspace(self, sandbox: SandboxRef, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot: ...
    async def destroy(self, sandbox: SandboxRef, reason: str) -> None: ...
```

Provider 方法由 Adapter 自己负责 HTTP/Docker 超时、可重试错误和 AIO 错误映射。destroy 对 404 幂等成功，平台原始异常不会直接泄漏到领域 API。

### 5.2 WorkspaceStore

```python
class WorkspaceStore(Protocol):
    async def snapshot(self, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot: ...
    async def commit(self, snapshot: WorkspaceSnapshot, lease_id: str, fencing_token: int = 0) -> None: ...
```

LocalWorkspaceStore 会校验 tenant/workspace 标识、相对路径、符号链接和路径穿越。缓存范围是 `tenant_id + workspace_id`；当前 Chat/VNC/MCP 入口分别将其映射为用户和会话。commit 采用完整快照替换语义：本次导出不存在的旧文件会从缓存中删除，并写入 manifest 记录 lease、fencing、文件数和字节数。commit 失败时 Scheduler 仍继续 destroy，实例绝不回池。未创建的 workspace 目录可表示为空快照。

### 5.3 内部 API

Sandbox API 与 Chat API 使用相同的接口表达约定：HTTP 端点按域位于 `sandbox.api.endpoints.health`、`pool` 和 `sandbox`；每个模块在顶层声明 `APIRouter` 和端点函数，并通过 `sandbox.container.Container` 注入 `SandboxPool` 或 `SandboxScheduler`。对应 Pydantic DTO 分别位于 `sandbox.api.schemas.health`、`pool` 和 `sandbox`，并由 `sandbox.api.schemas` 统一导出。业务接口使用 `R(code/msg/data)` 包装，并在端点上提供 `summary`、详细 `description` 和 `response_model`。健康探针保留裸 JSON 和 HTTP 503 语义，避免影响容器编排和负载均衡。

启动服务后可通过以下入口查看机器可读和交互式文档：

- Swagger UI：`GET /docs`
- OpenAPI JSON：`GET /openapi.json`

| 方法与路径 | 请求 | 成功响应 | 主要失败 |
| --- | --- | --- | --- |
| `GET /healthz` | 无 | `{"status":"ok"}`，HTTP 200 | 进程无响应 |
| `GET /readyz` | 无 | `{"status":"ready","ready":N,"min_ready":M}`，HTTP 200 | READY 不足 -> HTTP 503、`MIN_READY_NOT_REACHED` |
| `POST /internal/sandboxes/allocate` | `request_id`、`tenant_id`、`workspace_id` | `R[SandboxLeaseResponse]` | `POOL_EMPTY`、`REQUEST_CONFLICT`、`SANDBOX_UNAVAILABLE` |
| `POST /internal/leases/{lease_id}/execute` | `request_id`、租户/工作区、`fencing_token`、`operation`、`payload` | `R[ExecutionResultResponse]` | `LEASE_NOT_FOUND`、`LEASE_EXPIRED`、`FENCING_REJECTED`、`SANDBOX_UNAVAILABLE` |
| `POST /internal/leases/{lease_id}/release` | `fencing_token` | `R[{"status":"released"}]` | `LEASE_NOT_FOUND`、`LEASE_EXPIRED`、`FENCING_REJECTED`、`WORKSPACE_SYNC_FAILED` |
| `GET /internal/sandboxes/{sandbox_id}` | 无 | `R[SandboxStatusResponse]` | `LEASE_NOT_FOUND` |
| `GET /internal/pool/metrics` | 无 | `R[PoolMetricsResponse]` | `SYSTEM_ERROR` |

#### 5.3.1 分配、执行和释放示例

分配请求：

```json
{
  "request_id": "chat-turn-123",
  "tenant_id": "user-10001",
  "workspace_id": "session-20001"
}
```

分配响应中的 `data.lease_id`、`data.fencing_token` 和 `data.endpoint` 供同一租约的后续操作使用：

```json
{
  "code": 200,
  "msg": "操作成功",
  "data": {
    "lease_id": "lease_1",
    "request_id": "chat-turn-123",
    "sandbox_id": "sandbox-1",
    "tenant_id": "user-10001",
    "workspace_id": "session-20001",
    "expires_at": "2026-07-29T10:00:00Z",
    "fencing_token": 1,
    "endpoint": {"base_url": "http://sandbox:8080", "token": null}
  }
}
```

执行请求通过租约路径传递 fencing token：

```json
{
  "request_id": "tool-call-456",
  "tenant_id": "user-10001",
  "workspace_id": "session-20001",
  "fencing_token": 1,
  "operation": "shell_exec",
  "payload": {"command": "python main.py"}
}
```

释放请求只需要 fencing token。释放入口先关闭执行，再提交完整工作区快照并销毁用户实例；成功响应为 `data.status = "released"`。重复释放保持幂等。

#### 5.3.2 状态、指标和安全边界

状态接口返回生命周期状态、租约上下文和非敏感 endpoint 地址。`provider_id`、Provider metadata、endpoint token 和 readiness token 属于 Sandbox Service 内部信息，不会出现在状态响应中。allocate 响应中的 endpoint token 只服务于当前短期租约，释放后失效。

metrics 响应固定包含 `generation`、`empty_checkouts`、`min_ready` 和 `target_ready`，并携带 readiness、状态计数、租约、预热、销毁和 workspace 同步指标。后续新增指标会作为 `data` 的额外字段返回。

稳定错误码包括 `POOL_EMPTY`、`LEASE_NOT_FOUND`、`LEASE_EXPIRED`、`FENCING_REJECTED`、`REQUEST_CONFLICT`、`SANDBOX_UNAVAILABLE`、`WORKSPACE_SYNC_FAILED` 和 `WORKSPACE_CACHE_LIMIT_EXCEEDED`。

## 6. 生命周期流程

### 6.1 服务启动与预热队列初始化

标准进程入口是 `sandbox.main:app`。启动时先加载引导配置和 Sandbox 业务配置，创建 Repository、Pool、Provider、Scheduler、WorkspaceStore、LeaderLease 和 Watcher，再把 Watcher 作为 FastAPI 后台任务启动。标准启动方式会在配置加载阶段从 Nacos 拉取业务配置，并在应用 startup/shutdown 阶段注册和注销服务；无 Nacos 的开发演示可以使用 README 前文描述的直接组装 launcher，但生命周期顺序不变。

服务启动阶段只负责把 AIO 容器预热到 READY Pool，不会创建用户 lease，也不会执行用户工具。`/healthz` 只表示进程已存活，Watcher 可能仍在创建容器；只有 READY 数达到 `min_ready` 后，`/readyz` 才返回 200，Chat 的 allocate 才能取得可用实例。

#### 6.1.1 UML 泳道图

```mermaid
sequenceDiagram
    autonumber
    participant OS as 进程/启动命令
    participant CFG as Bootstrap/AppSettings
    participant N as Nacos
    participant APP as FastAPI 应用
    participant R as Repository
    participant P as Pool
    participant S as Scheduler
    participant A as AIO Adapter
    participant D as DockerRuntime
    participant C as AIO Container
    participant L as LeaderLease
    participant W as Watcher
    participant Q as readiness 探针

    OS->>CFG: 导入 sandbox.main:app
    CFG->>CFG: 加载 SERVICE_HOST、SERVICE_PORT、PROFILE
    CFG->>N: pull_config()
    N-->>CFG: Sandbox 镜像、Pool、租约、超时配置
    CFG-->>OS: 完成 settings 初始化

    OS->>R: 创建 MemorySandboxRepository
    OS->>P: 创建 SandboxPool(repository)
    OS->>A: 创建 AioSandboxProvider.from_environment()
    OS->>S: 创建 SandboxScheduler(pool, repository, provider, workspace)
    OS->>L: 创建 MemoryLeaderLease
    OS->>W: 创建 Watcher(pool, repository, provider, scheduler, leader)
    OS->>APP: create_app(scheduler, pool)
    APP-->>OS: FastAPI app ready

    APP->>APP: startup event
    APP->>N: register_instance()
    N-->>APP: 注册结果
    APP->>W: asyncio.create_task(watcher.run())
    APP-->>Q: /healthz = 200

    loop 每轮 reconcile，直到服务停止
        W->>L: acquire(key, owner, ttl)
        alt 未获得 LeaderLease
            L-->>W: false
            W->>R: watcher_not_leader += 1
        else 获得 LeaderLease
            L-->>W: true
            W->>S: recover_expired()
            S->>R: 查找过期 ALLOCATED/RUNNING lease
            S->>A: 导出并缓存过期用户实例工作区
            S->>A: 销毁过期用户实例
            W->>R: 清理 CREATING/WARMING/DESTROYING 超时实例
            W->>P: snapshot()
            P-->>W: ready、warming、creating、generation
            W->>W: 计算 target_ready + reserve - ready - warming - creating

            alt READY 缺口大于 0
                W->>A: create(SandboxSpec)
                A->>D: docker run -d -it -p 127.0.0.1::8080
                D-->>A: container_id、动态 endpoint
                A-->>W: SandboxRef
                W->>R: save(state=CREATING)
                W->>R: CAS CREATING -> WARMING
                W->>A: wait_ready(ref, warmup_timeout)
                A->>C: GET /v1/sandbox 轮询
                C-->>A: HTTP 200
                W->>A: health(ref)
                A->>C: GET /v1/sandbox 二次确认
                C-->>A: healthy
                W->>P: prepare_readiness(record)
                P->>R: 生成 health_token 和 expected_generation
                W->>P: return_ready(sandbox_id, health_token, generation)
                P->>R: 校验状态、token、generation、无 lease
                R-->>P: CAS WARMING -> READY
                P-->>W: 预热实例可分配
            else 没有缺口
                W->>W: 不创建新容器
            end
            W->>L: release(key, owner)
        end
        W->>W: 等待 interval_seconds 后进入下一轮
    end

    Q->>APP: GET /readyz
    APP->>P: snapshot()
    alt ready_count < min_ready
        P-->>APP: readiness=degraded
        APP-->>Q: HTTP 503 MIN_READY_NOT_REACHED
    else ready_count >= min_ready
        P-->>APP: readiness=ready
        APP-->>Q: HTTP 200 ready
    end

    OS->>APP: shutdown signal
    APP->>W: stop()
    APP->>W: cancel watcher task
    APP->>N: deregister_instance()
    N-->>APP: 注销结果
```

#### 6.1.2 启动阶段的状态和可用性

1. **配置和装配**：配置加载完成后才创建运行时对象。Repository 和 LeaderLease 当前是进程内实现；服务重启后 Pool、generation 和 lease 映射会重新开始。
2. **应用存活**：FastAPI startup 创建 Watcher 任务后，`/healthz` 即可返回 200。这个返回值不代表已有 READY 容器。
3. **Watcher 首轮 reconcile**：Watcher 先获取 LeaderLease，再调用 `Scheduler.recover_expired()`，清理旧状态，最后根据 `ready + warming + creating` 计算缺口。
4. **容器预热**：新容器先保存为 `CREATING`，然后进入 `WARMING`。只有 Docker 创建成功、动态 endpoint 可访问、`GET /v1/sandbox` 健康检查成功，并且 `return_ready()` 的 health token 和 generation 校验通过，实例才进入 `READY`。
5. **对 Chat 开放**：`/readyz` 在 READY 数量达到 `min_ready` 后变为 200。Chat 请求随后调用 allocate，从 READY Pool checkout，而不是在 Chat 工具调用时临时创建容器。
6. **预热失败**：健康检查、generation 或 `return_ready()` 失败时，实例进入销毁流程；销毁失败或无法确认时进入 `LOST`，不会进入 READY。Watcher 记录失败指标并按配置退避重试。
7. **服务停止**：停止时取消 Watcher 后台任务并注销服务。当前内存 Repository 不负责跨进程恢复，未完成实例的外部收敛需要后续接入持久化 Repository。

### 6.2 Watcher 预热与恢复

每轮 Watcher 执行：

```text
LeaderLease.acquire
  -> Scheduler.recover_expired()
  -> 清理 CREATING/WARMING 超时实例
  -> 清理 DESTROYING 超时实例
  -> 读取 Pool snapshot 和 generation
  -> 计算缺口并创建预热实例
  -> health + return_ready
  -> LeaderLease.release
```

缺口计算为：

```text
deficit = max(0, target_ready + reserve - ready - warming - creating)
create_count = min(deficit, max_create_batch)
```

Watcher 会排除 CREATING/WARMING 实例，避免并发重复创建；预热失败使用有限重试和退避。warmup timeout 或 destroy failure 后实例进入 LOST。两个 Watcher 在同一进程共享 LeaderLease 时只有一个可以执行补池决策；内存实现不宣称跨进程选主能力。

### 6.3 allocate、execute、release

1. allocate 校验字段并按 request_id 查询幂等记录。
2. Pool 原子 checkout READY，生成 lease 和 fencing token。
3. Scheduler 从 WorkspaceStore 获取快照，调用 Provider.prepare_workspace。
4. Provider.activate 后状态进入 RUNNING，返回短期 endpoint 和租约信息。
5. execute 只接受 lease_id，校验租户、workspace、租约状态、有效期和 fencing token，再调用 Provider.forward。
6. release 原子关闭租约入口，状态进入 SYNCING。
7. Provider.export_workspace 后调用 WorkspaceStore.commit，将完整工作区快照缓存到 `tenant_id + workspace_id`。
8. 无论 commit 成功或失败，都调用带超时和有限重试的 destroy。
9. destroy 成功进入 DESTROYED；超时/连续失败进入 LOST；成功销毁后清理租约映射。
10. Watcher 根据 READY 数量下降补充新的 WARMING 实例，健康后进入 READY。

```mermaid
sequenceDiagram
    participant W as Watcher
    participant P as Pool/Repository
    participant S as Scheduler
    participant F as WorkspaceStore
    participant A as AIO Adapter
    participant C as AIO Container
    participant U as 用户/后端

    W->>P: snapshot / generation
    W->>A: create + wait_ready + health
    A->>C: docker run -d -it
    C-->>A: /v1/sandbox=200
    W->>P: return_ready(token, generation)
    U->>S: allocate(request_id, tenant, workspace)
    S->>P: atomic checkout READY
    P-->>S: lease + fencing token
    S->>F: snapshot
    S->>A: prepare_workspace + activate
    A->>C: file write / health
    S-->>U: lease + endpoint
    U->>S: execute(lease_id, fencing_token)
    S->>A: forward
    A->>C: file / shell / code API
    U->>S: release(lease_id, fencing_token)
    S->>A: export workspace
    S->>F: commit(snapshot, lease, fencing)
    S->>A: destroy with timeout/retry
    A->>C: docker rm -f
    S-->>U: release acknowledged
    W->>P: detect READY deficit
    W->>A: create replacement
```

### 6.4 Chat 调用沙箱工具的完整请求链路

Chat 的沙箱租约边界是“一轮 Chat Turn”，不是一次工具调用。`ChatTurnCoordinator` 在进入 LLM 流式推理前创建本轮 `sandbox_request_id`，先调用 `SandboxClient.allocate_request()` 获取租约；本轮后续的文件、Shell 和脚本工具都复用这个租约。无论模型正常结束、工具执行失败、LLM 流异常还是客户端断开，最终都会在 `finally` 中调用 `release_request()`。

#### 6.4.1 请求标识映射

| Chat 标识 | Sandbox Service 标识 | 用途 |
| --- | --- | --- |
| `user_id` | `tenant_id` | 租户隔离和工作区路径隔离 |
| `session_id` | `workspace_id` | 当前会话对应的持久化工作区 |
| `sandbox_request_id` | allocate 的 `request_id` | 一轮 Chat 的幂等分配键 |
| `lease_id` | lease URL 路径参数 | execute/release 的唯一租约入口 |
| `fencing_token` | execute/release 请求字段 | 拒绝旧租约或并发失效请求 |
| 工具调用 ID | execute 的 `request_id` | 一次具体工具操作的请求标识 |

Chat 的 `SandboxClient` 有两种寻址方式：配置 `SANDBOX_SERVICE_URL` 时直接通过 HTTP 调用 Sandbox Service；未配置时使用 `RpcClient` 按 `wisepen-sandbox-service` 服务名访问。两种方式使用相同的内部 API 和租约语义。HTTP 直连时会携带 `X-From-Source: APISIX-wX0iR6tY`。

#### 6.4.2 UML 泳道图

下图中的每个 participant 都是一个泳道。Watcher 是后台并行泳道，不参与用户请求的同步返回，但会在 READY 数量下降后补充新的预热实例。

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户/前端
    participant CA as Chat API
    participant CC as ChatTurnCoordinator
    participant QR as QueryLoopRuntime/ToolDispatcher
    participant SC as Chat SandboxClient
    participant SA as Sandbox Service API
    participant SS as SandboxScheduler
    participant PR as Pool/Repository
    participant WS as WorkspaceStore
    participant AD as AIO Adapter
    participant DR as DockerRuntime
    participant C as AIO Container
    participant W as Watcher

    Note over W,PR: 后台持续运行：预热、恢复过期租约、补充 READY
    W->>PR: snapshot() 读取 READY、WARMING、CREATING
    W->>SS: recover_expired()
    W->>AD: create(spec) + wait_ready()
    AD->>DR: docker run -d -it -p 127.0.0.1::8080
    DR-->>AD: sandbox_id、动态 endpoint
    AD->>C: GET /v1/sandbox 轮询健康状态
    C-->>AD: HTTP 200
    W->>PR: return_ready(health_token, generation)
    PR-->>W: WARMING -> READY

    U->>CA: POST /chat/completions
    CA->>CC: handle_chat(user_id, session_id, query)
    CC->>CC: 创建 sandbox_request_id 和 tool_context
    CC->>SC: allocate_request(tool_context)
    SC->>SA: POST /internal/sandboxes/allocate
    SA->>SS: allocate(request_id, tenant_id, workspace_id)
    SS->>PR: request_id 幂等查询 + 原子 checkout READY
    PR-->>SS: READY -> ALLOCATED，生成 lease/fencing
    SS->>WS: snapshot(tenant_id, workspace_id)
    WS-->>SS: WorkspaceSnapshot 或空快照
    SS->>AD: prepare_workspace(snapshot)
    AD->>C: POST /v1/file/write 写入工作区文件
    SS->>AD: activate(sandbox, lease)
    AD->>C: GET /v1/sandbox 确认实例可用
    SS->>PR: CAS ALLOCATED -> RUNNING
    SS-->>SA: lease_id、endpoint、expires_at、fencing_token
    SA-->>SC: R.data(lease)
    SC->>SC: 缓存 LeaseContext
    CC-->>QR: 启动 LLM 流式推理和工具循环

    QR->>QR: LLM 返回 tool_call
    QR->>SC: write_file/read_file/shell_exec/execute
    SC->>SC: 复用已有 LeaseContext
    SC->>SA: POST /internal/leases/{lease_id}/execute
    SA->>SS: execute(lease_id, ExecutionRequest)
    SS->>PR: 校验租户、workspace、状态、过期时间、fencing
    PR-->>SS: RUNNING 且租约有效
    SS->>AD: forward(operation, payload)
    AD->>C: /v1/file/*、/v1/shell/exec 或 /v1/code/execute
    C-->>AD: AIO data 响应
    AD-->>SS: ExecutionResult
    SS-->>SA: R.data(result)
    SA-->>SC: 工具结果
    SC-->>QR: 工具输出
    QR-->>CC: ToolOutputAvailableEvent
    CC-->>U: 流式返回工具状态/最终文本

    Note over QR,CC: 还有工具调用时重复 execute；始终复用同一 lease
    QR->>QR: 将工具结果加入上下文并继续下一轮 LLM

    alt 正常结束、异常或客户端断开
        CC->>SC: finally: release_request(sandbox_request_id)
        SC->>SA: POST /internal/leases/{lease_id}/release
        SA->>SS: release(lease_id, fencing_token)
        SS->>PR: 原子关闭租约入口
        PR-->>SS: RUNNING -> SYNCING
        SS->>AD: export_workspace(sandbox, tenant, workspace)
        AD->>C: POST /v1/file/list + POST /v1/file/read
        C-->>AD: 工作区文件快照
        AD-->>SS: WorkspaceSnapshot
        SS->>WS: commit(snapshot, lease_id, fencing_token)
        alt commit 成功
            WS-->>SS: commit success
        else commit 失败
            WS-->>SS: WORKSPACE_SYNC_FAILED
            Note over SS,AD: 仍然继续销毁，实例不回 READY
        end
        SS->>AD: destroy(reason, timeout/retry)
        AD->>DR: docker rm -f provider_id
        DR->>C: 销毁用户实例
        alt destroy 成功
            SS->>PR: DESTROYING -> DESTROYED，清理 lease/request 映射
        else 超时或连续失败
            SS->>PR: DESTROYING -> LOST
        end
        SA-->>SC: released 或稳定领域错误
        SC->>SC: 清理本地 LeaseContext
    end

    par 用户实例释放后，Watcher 补池
        W->>PR: 读取 READY 数量下降
        W->>W: 计算 target_ready + reserve - ready - warming - creating
        W->>AD: 创建替代预热实例
        AD->>C: docker run + GET /v1/sandbox
        W->>PR: generation 校验后 return_ready()
        PR-->>W: 新实例进入 READY
    and 运行期间的后台恢复
        W->>SS: recover_expired()
        SS->>PR: 查找 ALLOCATED/RUNNING 过期租约
        SS->>AD: 关闭并销毁过期用户实例
    end
```

#### 6.4.3 分阶段行为

1. **Chat 建立租约**：Chat API 校验会话后进入 `ChatTurnCoordinator`。协调器根据 `user_id`、`session_id` 和本轮随机生成的 `sandbox_request_id` 构造工具上下文，并在 LLM 调用前完成 allocate。此时没有 READY 实例会直接阻止本轮进入工具推理，返回 `POOL_EMPTY`。
2. **Sandbox 原子分配**：API 只接收 `request_id`、`tenant_id` 和 `workspace_id`。Scheduler 通过 Repository 在同一把锁内完成 request_id 幂等查询、READY checkout、租约绑定和 fencing token 生成，状态从 `READY` 进入 `ALLOCATED`。
3. **工作区准备和激活**：Scheduler 从 WorkspaceStore 读取持久化快照。Adapter 将文件写入 AIO 工作区，然后通过 `/v1/sandbox` 确认实例可用，状态从 `ALLOCATED` 进入 `RUNNING`。allocate 返回的 endpoint 只属于本次 lease，Chat 不直接使用 AIO token 或 Docker container ID。
4. **工具调用复用租约**：LLM 返回工具调用后，QueryLoopRuntime 通过 ToolDispatcher 执行 `read_file`、`write_file`、`list_directory`、`grep_files`、`edit_file`、`shell_exec` 或 `run_sandbox_script`。每个工具都调用同一个 SandboxClient，SandboxClient 根据 Chat 请求的 `request_id` 命中缓存的 LeaseContext，不会重复 allocate。
5. **执行请求校验**：每次 execute 都携带租约上下文。Sandbox Service 校验 lease_id、tenant_id、workspace_id、fencing token、租约有效期和 `RUNNING` 状态；校验失败时拒绝请求，防止旧 Chat 请求或旧租约继续操作容器。
6. **Adapter 协议转换**：Sandbox Service 只看到 `SandboxProvider`。Adapter 将领域操作转换成 AIO HTTP 请求，使用 `/v1/file/search`、`/v1/shell/exec`、`/v1/code/execute` 等实际路径，并通过 PathPolicy 将工作区映射到 `/home/gem/{tenant_id}/{workspace_id}`。Chat 工具说明中的 `/workspace` 是逻辑路径，演示时应优先使用相对路径。
7. **释放与持久化**：Chat 的 `finally` 调用 release。Scheduler 先关闭租约入口，因此 release 开始后新的 execute 会被拒绝；随后导出容器工作区、以完整替换语义提交 WorkspaceStore，并无条件进入销毁流程。commit 失败只影响持久化结果，不允许实例回 READY。下一次同 `tenant_id + workspace_id` 分配会读取该缓存并写回新沙箱。
8. **销毁和补池**：销毁成功后用户实例进入 `DESTROYED`，失败或超时进入 `LOST`。Watcher 发现 READY 缺口后创建新的实例，只有健康检查和 `return_ready()` 成功的新实例才能进入 READY。用户实例和替代预热实例不会复用同一个 sandbox_id。

#### 6.4.4 请求边界与失败返回

| 阶段 | 调用边界 | 失败表现 |
| --- | --- | --- |
| Chat allocate | Chat `SandboxClient` -> Sandbox API | `POOL_EMPTY`、`REQUEST_CONFLICT`、`SANDBOX_UNAVAILABLE`，本轮 Chat 不进入工具循环 |
| 工具 execute | Tool -> SandboxClient -> lease execute API | 工具包装为 `[Tool Error]`，QueryLoopRuntime 可将错误作为工具结果继续推理 |
| release | Chat `finally` -> lease release API | `LEASE_NOT_FOUND` 和 `LEASE_EXPIRED` 视为可清理状态，其他错误继续上抛 |
| workspace commit | Scheduler -> WorkspaceStore | 返回 `WORKSPACE_SYNC_FAILED`，但仍销毁用户实例 |
| AIO destroy | Scheduler -> Adapter -> DockerRuntime | 404 幂等成功；超时/连续失败进入 `LOST`，不回 READY |
| Watcher recovery | Watcher -> Scheduler.recover_expired | 过期 `ALLOCATED/RUNNING` 实例走关闭、导出缓存和销毁流程，不直接回池 |

### 6.5 失败补偿

| 失败点 | 处理 |
| --- | --- |
| create 失败 | 记录失败、退避，不创建 READY 实例 |
| wait_ready/health 超时 | 转 DESTROYING，销毁失败则 LOST |
| workspace prepare 失败 | 立即销毁，实例不回池 |
| activate 失败 | 销毁已 checkout 实例，返回 `SANDBOX_UNAVAILABLE` |
| execute 期间 AIO 失联 | 拒绝后续操作，交由恢复流程销毁 |
| workspace commit 失败 | 记录 `WORKSPACE_SYNC_FAILED`，仍继续销毁 |
| destroy 超时 | `wait_for`、指数退避和有限重试，最终 LOST |
| 租约过期 | Watcher 调用 Scheduler.recover_expired，先尝试导出并缓存工作区，再销毁；缓存失败也不直接回 READY |

## 7. AIO Adapter 实现细节

### 7.1 Docker

当前真实 AIO 镜像为：

```text
enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
```

DockerRuntime 使用动态宿主机端口，将容器端口 8080 映射到 `127.0.0.1`，默认以 `-d -it` 启动。TTY 是必要配置：对该镜像验证发现普通 detached 容器会退出，而 `docker run -d -it` 可持续提供 HTTP 服务。真实测试可设置 `SANDBOX_E2E_LABEL=true`，只给测试容器增加 `wisepen.e2e=true` 标签，避免误删手动容器。

`-w /home/gem` 未继续传给 Docker。手动容器验证表明该镜像需要自身启动方式，`/home/gem` 只作为 AIO 文件 API 的可写工作根目录。

### 7.2 HTTP 协议映射

手动容器实际协议如下：

- 健康检查：`GET /v1/sandbox`，HTTP 200，响应包含 `success/message/data/home_dir/version/detail`；
- 文件写入/读取/列表/替换：`/v1/file/write`、`/v1/file/read`、`/v1/file/list`、`/v1/file/replace`；
- 文件搜索：实际为 `/v1/file/search`，请求字段为 `file`、`regex`，不是旧假设的 `/v1/file/grep`；
- Shell：`/v1/shell/exec`，响应包含 `session_id`、`command`、`status`、`output`、`console`、`exit_code`；
- 代码执行：`/v1/code/execute`，请求字段为 `language`、`code`，响应包含执行状态、stdout/stderr 和 exit_code；
- AIO 响应普遍使用 `data` 包装，AioClient 会解包后交给 Provider；
- 当前未发现 endpoint/token 必须认证的情况，但客户端保留 Authorization header 支持。

### 7.3 路径与工作区隔离

Provider 将每个工作区映射为：

```text
/home/gem/{tenant_id}/{workspace_id}/...
```

PathPolicy 拒绝空路径、越界绝对路径、`..`、非法租户/工作区标识和符号链接逃逸。list、search、Shell 默认使用当前 workspace 根，而不是整个 `/home/gem`。export 只读取当前作用域；目录不存在时返回空 WorkspaceSnapshot。

## 8. Metrics、安全与可观测性

`PoolSnapshot` 包含 generation、全部状态计数、Pool empty 次数、ready/min_ready/target_ready、readiness、低于 min_ready 的持续时间，以及以下生命周期指标：

- create/warmup/destroy 成功和失败次数；
- warmup、destroy 耗时和失败率；
- 过期租约恢复数、当前僵尸租约数；
- workspace commit 成功/失败次数；
- active leases by tenant；
- Watcher reconcile、非 leader 和低 readiness 统计。

metrics、状态查询和错误响应不返回 AIO token、workspace 内容、Docker container ID、完整异常堆栈或完整请求体。endpoint/token 只在 allocate 返回的短期租约上下文中使用，释放后失效。

## 9. 从 25a9157 起的实现演进

以下提交内容来自 `25a9157af6856dbeefaa07c939ae337feb57131b`（包含）之后的 Sandbox 实现，提交说明已在本分支整理为中文：

| 提交 | 内容 |
| --- | --- |
| `be7adb66` `refactor(Sandbox): 迁移沙箱服务` | 建立两层目录、领域端口、AIO Adapter、Pool/Scheduler/Watcher 骨架、Chat 工具入口和设计文档 |
| `3a914cf6` `refactor(Sandbox): 接入 Chat 沙箱依赖` | 将 Chat 文件、Shell、脚本工具统一接入 Sandbox Client，传递租约上下文 |
| `1eb48d1d` `feat(Sandbox): 完成抽象沙箱管理` | 实现状态机、Repository 原子操作、租约幂等、fencing、workspace 同步、Watcher 和内部 API |
| `c7cd99bc` `test(Sandbox): 覆盖生命周期与适配器契约` | 增加生命周期、错误映射、请求幂等、过期恢复和 Adapter fake 契约测试 |
| `0e0bcb65` `fix(Sandbox): 修复沙箱生命周期恢复与 AIO 适配` | 修复 Watcher recovery、readiness、metrics、return_ready、destroy 重试、真实 AIO 路径/协议、TTY 和工作区隔离 |
| `1055dbb4` `test(Sandbox): 补充生命周期与 AIO 契约测试` | 补充 health token、generation、active lease、AIO search/execute、TTY、e2e 标签和 metrics 测试 |

原始提交 `25a9157...` 至 `d1c0a207...` 的树内容保持不变，仅提交说明被重写为中文；后续两个新提交按运行时代码和测试代码拆分。

## 10. 测试与真实试跑结果

### 10.1 单元测试

执行方式：

```bash
cd services/wisepen-sandbox-service
PYTHONPATH=src pytest -q
# 21 passed
```

覆盖内容包括并发 checkout、非法状态、request_id 幂等、租户冲突、租约过期、fencing、return_ready、工作区路径、commit 失败销毁、release 幂等、销毁前缓存、下次分配恢复、完整快照替换、Watcher 补池和 readiness metrics，以及 AIO HTTP、错误映射、真实 search/execute 字段、路径隔离、TTY 和 Docker 参数。

### 10.2 手动 AIO 容器探测

用户手动启动的容器使用宿主机 `8080`，测试从未销毁该容器。探测结果：

- `GET /v1/sandbox`：PASS，HTTP 200，版本 `1.0.0.156`；
- `/health`、`/v1/health`、`/openapi.json`：不可用，未作为健康路径；
- 文件写入、读取、列表、搜索、替换：PASS，工作根为 `/home/gem`；
- Shell 执行：PASS；
- Code Execute：PASS，使用 `language` + `code`；
- `/v1/file/grep`：不存在，已改用 `/v1/file/search`。

### 10.3 专用容器与服务全链路

所有测试专用容器都使用 `wisepen.e2e=true` 标签，并在每次试跑后确认清理完成。真实 Sandbox Service 试跑结果：

```text
Watcher warmup       PASS  CREATING -> WARMING -> READY
health/readiness     PASS  healthz=200, readyz=200
allocate             PASS  READY -> ALLOCATED -> RUNNING
execute              PASS  AIO code execution succeeded
Watcher replenish    PASS  用户实例占用后补充新的 READY 实例
fencing rejection    PASS  错误 fencing token 返回 409
release              PASS  workspace commit -> destroy -> DESTROYED
release repeat       PASS  幂等，不重复 commit/destroy
user not READY       PASS  用户实例未回 READY
metrics              PASS  generation/readiness/tenant metrics 可见且不泄密
e2e cleanup           PASS  无测试容器残留
```

第一次真实 release 暴露了“空 workspace 目录不存在”的边界，修复为 Adapter 返回空快照后再次试跑成功。

## 11. 已知限制与后续扩展

- 当前 Repository 和 LeaderLease 是进程内实现，进程重启不会保留租约和 Pool 数据；跨进程选主需替换为外部存储/锁。LocalWorkspaceStore 已支持本地工作区缓存，但生产环境仍建议替换为对象存储或带元数据的外部持久化实现。
- AIO 镜像的 Docker 内置 healthcheck 可能因为 browser 子进程 SIGABRT 显示 `unhealthy`，但本次验证中 `/v1/sandbox` HTTP 接口可正常返回 200；生产环境应分别监控 Docker health 和 AIO HTTP health。
- 当前用户沙箱默认销毁，不支持 reset 后复用。
- 当前工作区缓存按文本内容读写，二进制文件和大对象传输仍需后续扩展专用协议。
- 尚未实现真实 Redis/Mongo Repository、跨实例 Watcher 选主、文件大对象传输、VNC/Proxy 端到端和故障注入测试。
- AIO Adapter 只保留当前文件、Shell、代码执行和容器生命周期所需的最小协议，后续新增 AIO 能力仍应保持平台依赖在 Adapter 内部。
