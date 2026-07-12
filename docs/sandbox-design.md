# 沙箱预热与调度系统设计

> 状态：Draft 设计
>
> 本文只依据沙箱需求文档和结构描述编写，不依赖当前分支中尚未存在的 sandbox 实现。实现阶段如发现底层 AIO 协议与本文假设不一致，应优先保持领域接口稳定，在 wisepen-aio-adapter 内完成映射。

## 1. 背景与目标

用户请求到达时临时创建容器会引入不可预测的启动延迟。本系统通过提前启动一批已完成基础初始化的空闲沙箱，收到请求后快速分配，并在请求结束后同步用户工作区、销毁实例，再由后台 Watcher 补充池容量。

本设计的目标是：

- 将预热、分配、文件同步、生命周期和容量补充拆成可测试的职责；
- 让调度逻辑只依赖统一的 SandboxProvider 和 WorkspaceStore 抽象；
- 通过 AIO Adapter 接入当前平台，后续可增加其他平台而不修改 Pool、Scheduler、Watcher；
- 明确并发、故障、幂等和租户隔离规则，避免同一个沙箱被重复分配或脏状态泄漏；
- 给前端/VNC 通信和后端协议提供稳定的管理面、数据面边界。

非目标：本文不定义前端界面、VNC 编码/转发协议和业务后端的具体 API 格式；这些由其他协作者负责，但需要遵守本文的租约和沙箱标识契约。

## 2. 总体架构

系统拆为两个服务层：

### 2.1 wisepen-sandbox-service

平台无关的沙箱管理层，包含：

- SandboxPool：保存已预热且可分配的沙箱，提供原子 checkout/return；
- SandboxScheduler：为一个用户请求创建租约，分配沙箱，准备工作区，激活并转发请求；
- Watcher：周期性读取 Pool 快照，计算缺口并提交预热任务；
- WorkspaceManager：通过端口读写用户短期持久化工作区；
- SandboxRepository：记录实例、租约、状态和版本，用于恢复、审计和幂等；
- 管理 API、指标、日志和 tracing。

### 2.2 wisepen-aio-adapter

AIO 平台适配层，实现 SandboxProvider：

- 创建并启动预热实例；
- 查询实例健康状态；
- 将工作区内容复制到实例；
- 激活/暂停/转发执行请求；
- 停止并销毁实例；
- 将 AIO 错误、状态和地址转换为领域错误和领域模型。

Scheduler、Pool 和 Watcher 不得直接导入 AIO SDK、AIO URL 或 AIO 状态枚举。替换平台时只新增 Adapter 和配置，不改调度规则。

### 2.3 逻辑组件关系

~~~mermaid
flowchart LR
    Client[用户或后端] --> API[Sandbox API]
    API --> Scheduler[Sandbox Scheduler]
    Scheduler --> Pool[Sandbox Pool]
    Scheduler --> Workspace[Workspace Manager]
    Scheduler --> Provider[SandboxProvider]
    Watcher[Watcher] --> Pool
    Watcher --> Provider
    Pool --> Repository[Sandbox Repository]
    Scheduler --> Repository
    Provider --> Adapter[AIO Adapter]
    Adapter --> AIO[AIO Sandbox Platform]
    Workspace --> Store[短期持久化存储]
    Scheduler --> Proxy[Sandbox Proxy / Load Balancer]
    Proxy --> Runtime[被分配的 Sandbox]
~~~

## 3. 核心领域模型

### 3.1 沙箱状态

状态只描述管理层可观察的生命周期；AIO 的原始状态由 Adapter 映射。

| 状态 | 含义 | 可进入状态 | 允许的操作 |
| --- | --- | --- | --- |
| CREATING | 已提交创建，尚未健康 | WARMING、DESTROYING | 查询、取消/销毁 |
| WARMING | 正在执行预热检查 | READY、DESTROYING | 查询、销毁 |
| READY | 无租约、可分配、基础环境已完成 | ALLOCATED、DESTROYING | checkout、健康检查 |
| ALLOCATED | 已和租约绑定，正在准备或使用 | RUNNING、DESTROYING | 复制工作区、激活、转发 |
| RUNNING | 用户请求正在使用 | SYNCING、DESTROYING | 转发、心跳、结束 |
| SYNCING | 正在把工作区写回存储 | DESTROYING、READY | 同步、销毁 |
| DESTROYING | 已从可用集合移除，正在销毁 | DESTROYED、LOST | 等待结果、重试 |
| DESTROYED | 已确认销毁 | 终态 | 无 |
| LOST | 无法确认状态或平台已失联 | 终态（需人工/恢复任务） | 告警、补偿 |

默认安全策略是：用户用过的容器进入 SYNCING -> DESTROYING，不直接回到 READY。只有未来实现了可信的全量清理、租户隔离验证和复用策略后，才允许 SYNCING -> READY。

### 3.2 标识与租约

- sandbox_id：平台无关的全局唯一 ID；由管理层生成或持久化映射。
- provider_id：平台实例 ID，例如 AIO container ID，仅 Adapter 使用。
- lease_id：一次用户占用的全局唯一租约 ID；所有分配、释放和请求转发必须携带它。
- tenant_id：用户/租户隔离键；工作区和审计记录必须绑定它。
- request_id：用户请求幂等键；相同 request_id 重试不能产生第二个租约。
- pool_generation：Pool 版本号或快照版本，用于排查并发 checkout 和 Watcher 决策。

一个 sandbox_id 同时最多只能有一个有效 lease_id。租约过期、客户端断开或 Scheduler 重启后，由恢复任务将实例标记为 DESTROYING，不允许直接回池。

### 3.3 Pool 语义

Pool 内只存放状态为 READY 且健康检查通过的沙箱。实现可以使用 Redis、数据库或进程内结构，但必须提供原子语义：

~~~text
checkout(request_id) -> SandboxLease | PoolEmpty
return_ready(sandbox_id, health_token) -> Success | Rejected
snapshot() -> PoolSnapshot(ready_count, warming_count, allocated_count, ...)
~~~

checkout 必须在同一原子操作中从 READY 集合移除实例并创建租约，防止两个并发请求拿到同一实例。return_ready 只接受没有用户数据、租约已清理、健康检查令牌匹配的实例；本期正常请求不调用它，而由 Watcher 对新建实例调用。

## 4. 接口设计

以下是领域端口的建议契约，具体 Python 类型和 HTTP/RPC 映射可在实现阶段确定。

### 4.1 SandboxProvider

~~~python
class SandboxProvider(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxRef: ...
    async def wait_ready(self, sandbox: SandboxRef, timeout: float) -> Health: ...
    async def prepare_workspace(
        self, sandbox: SandboxRef, workspace: WorkspaceSnapshot
    ) -> None: ...
    async def activate(self, sandbox: SandboxRef, lease: SandboxLease) -> Endpoint: ...
    async def forward(self, endpoint: Endpoint, request: ExecutionRequest) -> ExecutionResult: ...
    async def health(self, sandbox: SandboxRef) -> Health: ...
    async def destroy(self, sandbox: SandboxRef, reason: DestroyReason) -> None: ...
~~~

Adapter 的要求：

- 每个方法设置超时、区分可重试错误与不可重试错误；
- destroy 必须幂等，已不存在的实例视为成功；
- wait_ready 成功后才允许加入 READY Pool；
- 不把 AIO 原始异常直接泄漏到领域层；
- 支持 provider_id、请求追踪 ID 和幂等键透传。

### 4.2 WorkspaceStore

~~~python
class WorkspaceStore(Protocol):
    async def snapshot(self, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot: ...
    async def commit(
        self, tenant_id: str, workspace_id: str, sandbox_id: str, lease_id: str
    ) -> CommitResult: ...
~~~

snapshot 在分配后、激活前执行。commit 在任务结束后执行，必须带 lease_id，防止旧租约覆盖新数据。文件同步失败时不能把沙箱回池，必须记录告警并继续销毁实例，避免泄漏；是否重试由存储层策略控制。

### 4.3 管理层 API 边界

建议内部接口：

| API | 用途 | 关键约束 |
| --- | --- | --- |
| POST /internal/sandboxes/allocate | 按请求分配租约 | request_id 幂等；Pool 空时返回可区分的 POOL_EMPTY |
| POST /internal/leases/{lease_id}/execute | 转发执行请求 | 校验租约、租户和状态；禁止直接传 sandbox_id 绕过租约 |
| POST /internal/leases/{lease_id}/release | 结束任务并触发同步/销毁 | 可重复调用；首次调用完成完整清理 |
| GET /internal/sandboxes/{sandbox_id} | 查询管理状态 | 不暴露平台凭据 |
| GET /internal/pool/metrics | 供 Watcher/运维读取快照 | 只读；返回 generation 和计数 |

前端/VNC 通信只应拿到短期 endpoint/token，后端协议只应依赖 lease_id 和执行结果，不应依赖 AIO 容器 ID。

## 5. 预热、分配与回收流程

### 5.1 预启动流程

启动时或 Watcher 发现缺口时，计算目标：

~~~text
deficit = max(0, target_ready + reserve - ready_count - warming_count)
create_count = min(deficit, max_create_batch)
~~~

每个预热任务依次执行：

1. 生成 sandbox_id，持久化 CREATING；
2. 调用 Adapter create，记录 provider_id；
3. 更新为 WARMING，调用 wait_ready；
4. 执行基础健康检查和版本/镜像校验；
5. 原子 return_ready，更新为 READY；
6. 失败则进入 DESTROYING 并调用幂等销毁，不进入 Pool。

初次启动应先完成 min_ready，再接受需要低延迟的用户请求；在降级模式下也可以接受请求，但必须明确返回“正在启动”的状态，而不是伪装成已预热。

### 5.2 分配流程

1. API 校验 tenant_id、workspace_id、request_id。
2. Scheduler 以 request_id 查询已有租约；已有则返回原租约，保证重试幂等。
3. Pool 原子 checkout 一个 READY 沙箱并创建 ALLOCATED 租约。
4. 从 WorkspaceStore 获取快照，调用 prepare_workspace。
5. 调用 activate，得到短期 endpoint/token。
6. 更新沙箱为 RUNNING，向后端/Proxy 返回 lease_id、endpoint 和过期时间。
7. 后续流量经过 Proxy，Proxy 每次校验租约状态、租户和过期时间，再调用 forward。

任何步骤失败都执行补偿：标记实例不可用，尝试同步（仅在已修改且策略允许时），随后销毁；原请求得到稳定的领域错误。补偿失败由 Watcher/恢复任务继续处理，不能把失败实例放回 READY Pool。

### 5.3 释放、回队列与销毁

本期“回队列”有两个明确含义：

- **预热实例回队列**：新实例完成健康检查后从 WARMING 进入 READY Pool；
- **用户实例不回队列**：用户使用后的实例携带不可信的工作区和进程状态，正常路径同步后销毁。

释放步骤：

1. 原子地将 RUNNING 租约置为 RELEASING/关闭入口，阻止新请求；
2. 等待或取消正在进行的转发请求，应用宽限期；
3. 将沙箱置为 SYNCING，调用 WorkspaceStore commit；
4. 无论 commit 成功与否，都将沙箱置为 DESTROYING 并调用 Adapter destroy；
5. 成功确认后标记 DESTROYED，清理租约和 endpoint/token；
6. Watcher 观察 READY 数量下降，创建替代实例，替代实例最终回到 READY Pool。

如果产品后来要求复用容器，应新增 ResetPolicy：停止所有进程、删除临时文件、清空凭据和网络状态、验证镜像基线，再允许 return_ready。未完成该策略前不得复用。

## 6. Watcher 与自动恢复

Watcher 是后台控制循环，不直接处理用户请求：

~~~text
loop every interval:
    snapshot = pool.snapshot()
    reconcile(snapshot)
~~~

reconcile 规则：

- ready_count + warming_count < target_ready 时提交缺口数量的预热任务；
- warming_count 对应实例超过启动超时，标记失败并销毁；
- ALLOCATED/RUNNING/SYNCING 超过租约或宽限期，发起恢复释放；
- DESTROYING 超过销毁超时，重试有限次数后标记 LOST 并告警；
- 多个 Watcher 实例通过 leader lease、分布式锁或任务去重键保证只有一个控制决策生效；
- 启动失败使用指数退避和上限，避免 AIO 故障时热循环创建。

Watcher 需要记录：ready_count、warming_count、allocated_count、创建/销毁耗时、Pool 空次数、预热失败率、僵尸租约数、工作区同步失败数。告警至少包括 READY 长时间低于 min_ready、销毁不收敛和同一租户租约异常增长。

## 7. 单个用户请求的全链路案例

假设配置为 target_ready=2、min_ready=1。系统已有 sb-001、sb-002 两个 READY 沙箱；用户 u-42 携带 request_id=req-1001 请求执行一次任务。

1. **预启动**：启动流程或上一次 Watcher 已完成 AIO sb-001、sb-002 的创建、健康检查，二者都已进入 Pool READY 队列。
2. **分配**：Scheduler 原子 checkout sb-001，创建 lease-9001，Pool 从 2 降到 1；sb-001 变为 ALLOCATED。
3. **环境准备**：File/Workspace Manager 从 ws-u42 读取快照，将文件复制到 sb-001。若复制失败，释放租约并销毁 sb-001，本次请求失败且不回池。
4. **激活和使用**：Scheduler 激活沙箱，Proxy 将 req-1001 的流量绑定到 lease-9001 -> sb-001。只有该租约对应的用户能访问该 endpoint。
5. **回队列事件**：Watcher 同时观察到 ready_count=1，但 warming_count=0，创建 sb-003。sb-003 完成预热后进入 WARMING -> READY，回到 Pool，READY 恢复为 2。
6. **任务结束和持久化**：用户断开或后端收到完成事件，Scheduler 关闭 lease-9001 的入口，将 sb-001 置为 SYNCING，把工作区变更 commit 回 ws-u42。
7. **销毁**：无论 commit 成功与否，Scheduler 调用 Adapter 销毁 sb-001，确认后标记 DESTROYED；lease-9001 失效，旧 endpoint/token 不能再使用。
8. **Watch**：若销毁期间 Pool 仍只有 sb-002、sb-003，Watcher 不重复创建；若 sb-003 预热失败并被销毁，下一轮根据快照再次补足缺口，并遵守退避。

请求级不变量：整个过程中 u-42 只绑定 sb-001；sb-001 不会被其他请求 checkout；sb-001 不会带着用户数据回 READY；sb-003 只有健康检查完成后才可被分配。

## 8. UML 泳道图

下图按职责划分泳道，展示一次请求从预热、分配、回队列补充到释放和销毁的活动流。

~~~mermaid
flowchart LR
    subgraph User["用户 / 后端"]
        U1["发起 allocate"]
        U2["使用沙箱"]
        U3["发起 release"]
        U1 --> U2 --> U3
    end
    subgraph Scheduler["Sandbox Scheduler"]
        S1["创建租约"]
        S2["准备并激活"]
        S3["关闭入口"]
        S4["提交同步并销毁"]
        S1 --> S2 --> S3 --> S4
    end
    subgraph Pool["Sandbox Pool"]
        P1["checkout READY"]
        P2["移除已分配实例"]
        P3["接收新 READY 实例"]
        P1 --> P2
        P3 --> P1
    end
    subgraph Watcher["Watcher"]
        W1["读取 Pool 快照"]
        W2["计算缺口"]
        W3["触发预热"]
        W1 --> W2 --> W3
    end
    subgraph Adapter["AIO Adapter / Sandbox"]
        A1["create + wait_ready"]
        A2["prepare + activate"]
        A3["forward"]
        A4["destroy"]
        A1 --> A2 --> A3 --> A4
    end
    subgraph Workspace["Workspace Store"]
        F1["读取快照"]
        F2["写回变更"]
    end

    U1 --> S1
    S1 --> P1
    P2 --> S2
    S2 --> F1
    F1 --> S2
    S2 --> A2
    A2 --> U2
    U2 --> A3
    U3 --> S3
    S3 --> S4
    S4 --> F2
    F2 --> S4
    S4 --> A4
    P2 -. "ready 数下降" .-> W1
    W3 --> A1
    A1 --> P3
~~~

### 8.1 补充时序图

~~~mermaid
sequenceDiagram
    autonumber
    participant W as Watcher
    participant P as Sandbox Pool
    participant A as Sandbox Scheduler
    participant F as Workspace Manager
    participant X as SandboxProvider
    participant I as AIO Adapter
    participant C as AIO Sandbox
    participant U as User/Backend
    participant R as Workspace Store

    Note over W,C: 预启动与入池
    W->>P: snapshot: ready=0, warming=0
    W->>A: reconcile: create 2 warm tasks
    A->>X: create(spec)
    X->>I: map create
    I->>C: create/start
    C-->>I: provider_id
    I-->>X: SandboxRef
    X->>I: wait_ready()
    I->>C: health/version check
    C-->>I: healthy
    I-->>X: Health OK
    X->>P: return_ready(sb-001)
    X->>P: return_ready(sb-002)

    Note over U,C: 用户请求分配与执行
    U->>A: allocate(req-1001, tenant=u-42)
    A->>P: atomic checkout()
    P-->>A: lease-9001 + sb-001
    A->>F: snapshot(ws-u42)
    F->>R: read workspace
    R-->>F: WorkspaceSnapshot
    F->>X: prepare_workspace(sb-001)
    X->>I: map workspace copy
    I->>C: copy files
    A->>X: activate(sb-001, lease-9001)
    X->>I: map activate
    I->>C: activate user runtime
    C-->>I: endpoint/token
    I-->>X: Endpoint
    X-->>A: Endpoint + lease
    A-->>U: lease-9001 + endpoint
    U->>A: execute(lease-9001, request)
    A->>X: forward(endpoint, request)
    X->>I: map execute
    I->>C: run request
    C-->>U: result/stream

    Note over W,C: Watcher 异步补充 Pool
    W->>P: snapshot: ready=1, warming=0
    W->>A: reconcile: create 1 warm task
    A->>X: create(sb-003)
    X->>I: map create/wait_ready
    I->>C: create + health check
    C-->>I: healthy
    X->>P: return_ready(sb-003)

    Note over U,C: 释放、同步与销毁
    U->>A: release(lease-9001)
    A->>X: stop ingress / cancel after grace period
    A->>F: commit(ws-u42, sb-001, lease-9001)
    F->>R: write workspace changes
    R-->>F: commit result
    A->>X: destroy(sb-001, completed)
    X->>I: map destroy (idempotent)
    I->>C: stop/remove
    C-->>I: destroyed/not found
    I-->>X: success
    X-->>A: DESTROYED
    A-->>U: release acknowledged
~~~

## 9. 并发、故障与一致性策略

### 9.1 并发控制

- Pool checkout 使用原子 pop 或带条件版本的事务；不能先读后删。
- 同一个 request_id 使用幂等记录，Scheduler 重试只返回原租约。
- release、destroy、commit 都以 lease_id 做 fencing token，旧调用不能影响新租约。
- Pool 的状态机转换必须校验前置状态；非法转换记日志并拒绝。
- Watcher 使用可抢占的 leader lease 或基于 snapshot generation 的 compare-and-set。

### 9.2 故障矩阵

| 故障点 | 处理 | Pool 结果 | 用户结果 |
| --- | --- | --- | --- |
| 创建失败 | 退避重试，记录 provider 错误 | 不入池 | 分配暂不可用或等待 |
| 健康检查超时 | 销毁实例 | 不入池 | 本次预热不影响已存在租约 |
| checkout 后工作区复制失败 | 失效租约并销毁 | 不回池 | 返回准备失败，可按请求策略重试 |
| activate 失败 | 销毁实例 | 不回池 | 返回启动失败 |
| 执行中 AIO 失联 | 标记 LOST/触发销毁，保留审计 | 不回池 | 返回沙箱不可用 |
| commit 失败 | 告警并按策略重试，仍销毁实例 | 不回池 | 返回持久化失败或部分成功状态 |
| destroy 超时 | 有限重试，最终 LOST | 不回池 | 租约失效；运维告警 |
| Scheduler 重启 | 从 Repository 恢复未结束租约 | READY 不变，活跃实例逐一收敛 | 客户端需重新获取有效租约 |

不使用分布式事务跨 AIO 和 WorkspaceStore。依靠状态机、幂等操作、持久化记录和补偿任务达到最终一致。

### 9.3 租户隔离与安全

- tenant_id、workspace_id、lease_id 三者在每次 API/RPC 调用中校验；不能仅相信客户端传来的 sandbox_id。
- endpoint/token 设短 TTL，释放或过期后立即拒绝；日志中脱敏 token 和文件内容。
- 预热镜像不得包含任何用户数据、长期凭据或上一个租约的运行状态。
- 文件复制采用允许列表、大小限制、路径规范化和病毒/类型策略；禁止通过工作区路径逃逸宿主机。
- AIO Adapter 使用服务身份和最小权限，管理接口与用户数据面隔离。

## 10. 配置与扩展点

建议配置：

~~~yaml
sandbox:
  provider: aio
  target_ready: 2
  min_ready: 1
  reserve: 0
  max_create_batch: 4
  watcher_interval_seconds: 5
  warmup_timeout_seconds: 60
  allocate_timeout_seconds: 15
  release_grace_seconds: 10
  destroy_timeout_seconds: 30
  lease_ttl_seconds: 1800
  reuse_policy: destroy
~~~

扩展新平台时实现：

1. SandboxProvider 的生命周期和能力映射；
2. ProviderError -> DomainError 映射；
3. 平台健康检查和指标；
4. Adapter contract tests。

不应为了支持新平台修改 SandboxPool、SandboxScheduler 或 Watcher 的核心规则。如果平台不支持某个能力，应在能力协商阶段拒绝，而不是在执行中静默降级。

## 11. 单元测试设计

测试使用 Python pytest、pytest-asyncio，核心组件全部注入 fake/in-memory port。测试重点是状态转换、原子性、补偿和幂等，不启动真实 AIO、容器或网络服务。

### 11.1 Pool 测试

| 用例 | Given/When | 断言 |
| --- | --- | --- |
| test_checkout_removes_ready_sandbox_atomically | Pool 有一个 READY，并发执行两个 checkout | 只有一个成功；另一个得到 PoolEmpty；ready 数为 0 |
| test_checkout_never_returns_warming_or_destroying | Pool 注册 WARMING、DESTROYING | checkout 不返回它们 |
| test_return_ready_requires_clean_health_token | 带错误 token 或仍有有效 lease 的实例 return | 操作拒绝，实例不进入 READY |
| test_illegal_state_transition_is_rejected | 对 DESTROYED 调用 activate/return | 抛领域状态错误，状态不变 |
| test_snapshot_has_consistent_generation | 连续 checkout/return | generation 单调变化，计数与集合一致 |

### 11.2 Scheduler 测试

| 用例 | Given/When | 断言 |
| --- | --- | --- |
| test_allocate_prepares_and_activates_warm_sandbox | 有 READY 沙箱且 workspace snapshot 成功 | 依次发生 checkout、snapshot、prepare、activate；返回 lease/endpoint；状态为 RUNNING |
| test_allocate_is_idempotent_by_request_id | 同一 request_id 调用 allocate 两次 | 只创建一个 lease；第二次返回相同结果；Provider 只 activate 一次 |
| test_workspace_copy_failure_destroys_sandbox | prepare_workspace 抛错 | lease 失效；调用 destroy；沙箱不回 READY |
| test_activate_failure_compensates_with_destroy | activate 抛错 | 记录失败并销毁；不留下可用租约 |
| test_release_commits_then_destroys | RUNNING 租约正常 release | 关闭入口、commit 带正确 tenant/workspace/lease、destroy；状态为 DESTROYED |
| test_release_is_idempotent | release 同一 lease 两次 | 只有一次 commit/destroy；第二次返回已完成 |
| test_old_lease_cannot_commit_after_reallocation | 旧租约调用 commit | 被 fencing 拒绝，不覆盖新租约数据 |
| test_expired_lease_rejects_forward | 过期 lease 调 forward | 请求被拒绝，不调用 Provider.forward |
| test_commit_failure_still_destroys_sandbox | WorkspaceStore.commit 失败 | 记录持久化错误，仍调用 destroy，绝不回池 |

### 11.3 Watcher 测试

| 用例 | Given/When | 断言 |
| --- | --- | --- |
| test_watcher_creates_only_the_pool_deficit | target=3，ready=1，warming=1 | 只提交 1 个预热任务 |
| test_watcher_does_not_overcreate_during_warmup | target=3，ready=1，warming=2 | 不创建任务 |
| test_warmup_success_returns_sandbox_to_pool | Provider create/wait_ready 成功 | 状态 WARMING -> READY，并调用 return_ready |
| test_warmup_timeout_destroys_without_pool_return | wait_ready 超时 | 调用 destroy；实例不在 Pool |
| test_watcher_reconciles_stale_lease | RUNNING 超过 TTL | 发起 recovery release/destroy，不直接 return_ready |
| test_watcher_uses_leader_or_generation_fencing | 两个 Watcher 同时 reconcile | 只有一个创建批次生效，不能重复补足 |
| test_destroy_timeout_becomes_lost_and_alerts | destroy 连续超时超过重试上限 | 标记 LOST，发送告警，不继续热循环 |

### 11.4 AIO Adapter 契约测试

使用可控的 AIO HTTP fake，验证 Adapter 而非 Scheduler 的平台映射：

- AIO 创建成功的响应被映射为 SandboxRef(provider_id=...)；
- AIO 404 在 destroy 中视为幂等成功；
- 连接超时映射为可重试 ProviderUnavailable；鉴权/参数错误映射为不可重试错误；
- AIO 原始状态只有在健康检查满足条件时才映射为 Health.ok=True；
- 请求超时、取消和重试携带相同的 idempotency key；
- AIO endpoint/token 不进入普通业务日志。

### 11.5 测试夹具与验证边界

建议提供：FakeSandboxProvider、InMemorySandboxPool、FakeWorkspaceStore、FakeClock、RecordingRepository。测试时用 FakeClock 无 sleep 地验证租约过期和 Watcher 周期；用 RecordingProvider 验证调用顺序和调用次数；并发测试使用 barrier/latch 控制竞态窗口。

单元测试不替代以下后续测试：真实 AIO Adapter 集成测试、服务间协议兼容测试、文件大对象传输测试、多实例 Watcher 选主测试、VNC/Proxy 端到端测试和故障注入测试。

## 12. 可观测性与验收标准

每次请求至少关联 request_id、lease_id、sandbox_id、tenant_id、provider_id（仅内部）和 pool_generation。日志记录状态转移、耗时和错误类别；禁止记录 workspace 内容和访问 token。

第一阶段验收：

- 启动后达到 min_ready，READY 实例可被 checkout；
- 两个并发请求不会拿到同一个沙箱；
- 一个请求按“复制 -> 激活 -> 执行 -> commit -> 销毁”完成；
- 任一中间步骤失败时，失败实例不进入 READY；
- 用户实例销毁后 Watcher 能补充缺口，预热成功的替代实例回到 READY；
- 重复 allocate/release 不造成重复实例、重复同步或状态回退；
- Scheduler 中没有 AIO 具体依赖，替换为 FakeProvider 的单元测试可独立运行；
- 租约过期、旧租约和跨租户请求均被拒绝。

## 13. 实现顺序建议

1. 先在 wisepen-sandbox-service 定义领域模型、状态机、端口、Pool 和 Repository；
2. 编写 Pool/Scheduler/Watcher 的 fake-based 单元测试，再实现服务编排；
3. 实现 wisepen-aio-adapter，先通过 Adapter contract tests，再接入真实 AIO；
4. 和后端协议协作者确定 allocate/execute/release 的租约字段与错误码；
5. 和前端/VNC 协作者确定 endpoint/token 的短期凭证、断线和 release 触发规则；
6. 最后补充真实 AIO、Proxy、文件存储和多实例 Watcher 的集成/端到端测试。
