# Codex 本地 Multi-Agent 实现报告

## 阅读范围与证据标记

本文只解释固定克隆中 Codex Rust core 的本地实现，基线为 b337fe06b7d8d6e5b42295e04c5e92c2b9c46567。 本文中的代码链接都相对当前文档位置，进入同级
codex/ 克隆。 为了把源码事实和讲解模型分开，使用三种标记：

- **静态证据**：本地源码中能直接看到的类型、分支、调用和测试定义。
- **教学推演**：为了说明调用顺序而构造的搜索委派例子或概念伪代码，不代表一次实际运行。
- **未确认边界**：本地源码没有提供足够证据作更强承诺的地方。
- 本文没有编译、安装或运行 Codex 上游测试；测试入口只用于说明测试意图。

主结论先放在前面：

- 本地协作以父子线程树、AgentPath 和 AgentControl 操作为中心。
- V1 和 V2 是两代实际不同的 tool schema 与 handler 协议，不只是同一个工具的不同提示词。
- V2 有共享讨论型 Message Board，但没有由它维护任务认领和完成状态的 Team / TaskBoard 抽象。
- V2 的 residency slot、执行中 turn 计数和 mailbox 是不同状态层；不能把它们合成一个“任务调度队列”。
- 已消费并写进 history / rollout 的 agent message 有恢复路径；仍在本地 runtime / input queue 中等待消费的消息，不因此获得跨进程持久队列保证。

## 1. 对象职责图：身份、驻留 runtime 与一次 turn

### 先把这些对象分开

**ThreadId 是对话身份。**
普通持久 thread 的历史与 metadata 以该身份关联；spawn edge 也引用 parent / child ThreadId。 ThreadId 不等于当前进程里某个活着的
Rust 对象。 Ephemeral thread 是例外，不能把所有 spawn 都当成持久记录。

**CodexThread 是 ThreadManager 中当前加载的 thread runtime。**
它包装 Session、事件 IO、session source 和 rollout path。 V2 residency eviction 会从 ThreadManagerState 的 map 移除一个
child 的 CodexThread； thread 的历史身份可以仍留在 thread store / rollout / spawn-edge 数据中。

**Session 是一个 thread 的运行时会话。**
Session 有 ThreadId、input queue、active turn、history 状态和服务依赖。 同一个 Session 最多一个正在运行的 task；新的 turn
会复用该 thread 的 Session。 所以“thread”讲的是可寻址的对话实体，“Session”讲的是该实体当前这份 core runtime。

**TurnContext 是一次 turn 的冻结上下文。**
它记录本次 turn 的配置、来源、model/provider、插件选择和 multi-agent version。 一次 turn 可能包含多次模型采样和 tool 调用。

**StepContext 是一次模型请求的快照。**
每次采样请求拿到一个 StepContext；里面包括 settings、环境、MCP 绑定和 ToolRouter。 step 配置可随后续步骤变化，已开始的请求仍使用自身快照。

**ToolRouter 是这个 sampling request 的可见 tool specs 与执行器集合。**
模型看到的 schema 和 core 实际 dispatch 的 handler 由同一个 finalized router 配对。 它挂在 StepContext 上，不作为 spawn
请求的父工具列表快照传给子线程。

**AgentControl 是 agent 操作接口。**
spawn、send、interrupt、list、mailbox watch/take、turn admission、terminal notification 等操作通过这个 trait 与 backend 边界交互。
本地实现是 LocalAgentControl；远端 backend 的 ownership、重试和恢复需要各自实现，不能从 trait 名称推断。

**LocalAgentRuntime 是一个 agent tree 共享的本地状态。**
Root 和 child 的 control handle 克隆同一 runtime allocation。 其中有 AgentRegistry、V2Residency、mailboxes、执行 turn 计数、树
shutdown 状态以及共享预算等。

**AgentRegistry 是本地树的路径与 agent metadata 索引。**
它可登记 root / children，并在本地 eviction 时留住环境快照。 它本身不是磁盘任务数据库，也不保存共享任务的 claim / done 状态。

**InputQueue 是一个已加载 Session 的输入交接点。**
它区分普通输入、tool result、steer 和 inter-agent mailbox 内容。 未加载时没有这个 Session queue；runtime Mailboxes 可暂存
QueueOnly 消息，待再次加载后转交。

### 对象关系

下面是教学图；箭头表示数据或调用关系，不表示每个对象都在同一层持久化。

~~~mermaid
flowchart TD
    A[Root ThreadId] --> B[CodexThread: loaded runtime]
    B --> C[Session: history, input queue, active turn]
    C --> D[TurnContext: one turn]
    D --> E[StepContext: one sampling request]
    E --> F[ToolRouter: schemas plus executors]
    F --> G[Tool handler]
    G --> H[AgentControl trait]
    H --> I[LocalAgentRuntime shared by tree]
    I --> J[AgentRegistry: paths and metadata]
    I --> K[V2Residency: resident child slots]
    I --> L[Mailboxes: unloaded QueueOnly mail]
    H --> M[ThreadManagerState: loaded CodexThread map]
    M --> N[ThreadStore and rollout history]
    M --> O[AgentGraphStore: parent-child edges]

~~~

**静态证据：**
Session 的 one-running-task 约束和运行字段在 [session/session.rs](codex/codex-rs/core/src/session/session.rs#L60)。
CodexThread 包装 Session、IO 和 residency gate，在 [codex_thread.rs](codex/codex-rs/core/src/codex_thread.rs#L208)。
ThreadManagerState 的线程 map 在 [thread_manager.rs](codex/codex-rs/core/src/thread_manager.rs#L440)。 TurnContext /
StepContext 分别在 [turn_context.rs](codex/codex-rs/core/src/session/turn_context.rs#L323)
和 [step_context.rs](codex/codex-rs/core/src/session/step_context.rs#L23)。
ToolRouter 在 [router.rs](codex/codex-rs/core/src/tools/router.rs#L85)，本地 tree runtime
在 [control/runtime.rs](codex/codex-rs/core/src/agent/control/runtime.rs#L184)。 AgentControl trait 与 AgentRegistry
的起点是 [agent/api.rs](codex/codex-rs/core/src/agent/api.rs#L41)
和 [agent/registry.rs](codex/codex-rs/core/src/agent/registry.rs#L28)。

### Python 学习类比

可以把 Session 看作 per-thread runtime，把 TurnContext 看作一次请求的配置快照，把 StepContext 看作单次 sampling
request；这是概念映射，非可运行实现。Rust 阅读时先跟值、Arc 和 guard 的所有权，持久性另看 ThreadStore / rollout / graph
store。 读 Rust 时优先追这些值从哪里创建、何时被 Arc 克隆、谁持有 guard。 不需要先假设 Arc 就是“数据库”或“全局状态”；
它只解释引用计数所有权，持久性要继续看 ThreadStore / rollout / graph store。
最重要的三种时间尺度是：

| 时间尺度         | 典型对象                                                 | 该对象回答的问题                                |
|------------------|----------------------------------------------------------|-------------------------------------------------|
| 身份与历史       | ThreadId、rollout、thread metadata、spawn edge           | 这是谁的对话、父子关系是什么、历史从哪里恢复    |
| 当前驻留 runtime | CodexThread、Session、InputQueue、AgentRegistry、Mailbox | 现在内存里有哪些线程，消息正排在哪里            |
| 一次工作         | TurnContext、StepContext、tool invocation                | 当前 turn / sampling 使用什么上下文和 tool plan |

因此，V2 子 agent 被 eviction 后，正确说法是“runtime 暂时卸载，身份 / 存储历史仍可供恢复”，不是“agent 对话被删了”；
也不能反过来说“恢复后整个 process state 和 pending mailbox 一定都还在”。

## 2. 从模型工具调用到 child 运行：一条搜索委派时序

### 教学推演：请 child 查找 mailbox 生命周期

设主 agent 要回答“QueueOnly 消息在什么时刻进入 rollout”，主模型希望把搜索交给 child。 这是教学推演，不是一次上游运行记录。

1. 主 thread 已有自己的 Session、TurnContext 和 StepContext。 StepContext 的 ToolRouter 向模型公开当前版本允许的协作
   schema。
2. 模型根据 spawn_agent schema 产生函数调用。 V2 参数包括 task_name、message，以及可选 fork_turns、agent_type、model /
   reasoning 等参数。
3. spawn handler 解析 schema 输出，再调用共享 thread_spawn 入口。
4. thread_spawn 准备 child config，构建新的 SessionSource::SubAgent (ThreadSpawn)，生成 task path，并将首条任务消息作为
   TriggerTurn 交给 AgentControl。显式的 parent、turn、环境和 plugin
   元数据在 [agent/types.rs](codex/codex-rs/core/src/agent/types.rs#L27)
   与 [thread_spawn.rs](codex/codex-rs/core/src/thread_spawn.rs#L137)。
5. 本地 backend 分配 child ThreadId，准备运行时并注册父子边，再启动 child turn。
6. child 用自己的 Session 和 StepContext 运行。 它的 tool router 根据 child 当下 model / provider / config 重新构造；parent
   没有把一个 ToolRouter 对象交给它。
7. child 找到实现后完成 turn。 local turn_finished 路径生成 queue-only completion message 发给直接 parent；完成通知本身不会触发
   parent 新 turn。
8. parent 的 wait_agent 等的是当前 Session 收到 mailbox activity 的信号。 wait_agent 输出只告诉模型“发生了活动”，不把
   child result 正文塞进该工具的返回值。

### 顺序图

此图展示源码中由 schema 到通知的主要路径。 “搜索”工具是否出现仍取决于 child model / provider / configuration，并非 spawn
保证。

~~~mermaid
sequenceDiagram
    participant RM as Root model
    participant RT as Root ToolRouter
    participant SH as spawn_agent handler
    participant TS as thread_spawn
    participant AC as AgentControl
    participant CM as ThreadManager
    participant CT as Child Session and Turn
    participant CR as Child ToolRouter
    participant CP as Completion path
    participant RI as Root InputQueue

    RM->>RT: 调用 spawn_agent schema
    RT->>SH: dispatch 函数参数
    SH->>TS: ThreadSpawnRequest(task_name, message, fork_mode)
    TS->>AC: child config + TriggerTurn + parent metadata
    AC->>CM: 建立并发布 child thread
    CM->>CT: 用新 ThreadId 启动 Session
    CT->>CR: child 自己准备 sampling tools
    CR->>CT: 搜索与读取工具结果
    CT->>CP: turn_finished(outcome)
    CP->>AC: 给直接 parent 投 QueueOnly completion
    AC->>RI: 当前加载 parent 收到 mailbox activity
    RM->>RT: wait_agent 只取 activity 摘要
    RI-->>RM: 实际 child 消息进入正常输入/history 路径

~~~

**静态证据：**
V2 schema 从 [multi_agents_spec.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs#L108) 开始。 V2 handler
构造 request
并调用共享入口，见 [multi_agents_v2/spawn.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs#L94)。
thread_spawn 构造 source、父路径、initial TriggerTurn 和 inheritance
metadata，见 [thread_spawn.rs](codex/codex-rs/core/src/thread_spawn.rs#L58)
与 [thread_spawn.rs](codex/codex-rs/core/src/thread_spawn.rs#L137)。 tool plan 按当前 turn context
构造于 [spec_plan.rs](codex/codex-rs/core/src/tools/spec_plan.rs#L121)
，完成结果由 [control/completion.rs](codex/codex-rs/core/src/agent/control/completion.rs#L27) 路由。

### 这条链并不共享对话历史

Root 与 child 有不同 ThreadId、Session 和 rollout history。 FullHistory fork 会把 parent 的选定历史作为 child 的初始历史快照；
之后新发生的消息分别进入各自的 history。 Fresh spawn 不复制整段会话 history，但仍继承适用的 config / role / environment
等运行设置，并收到独立的任务消息。
所以“历史独立”意思是 history 后续各自增长，不表示 full-history child 的开头没有 parent 对话前缀。 完成后 child 将文本通过
agent communication 交给 parent，而不是把两条 history 合并成一个共享日志。 Parent 收到的已记录 AgentMessage 成为 parent
history 的一部分； child 的源码历史仍独立保存在 child ThreadId 下。

## 3. Fresh 与 FullHistory：到底继承哪些内容

### 配置、历史和工具是三条不同的传递路径

新建 child 时，代码先准备 child config。 它会根据 parent turn 的有效设置生成共享配置，再应用 child role、model、reasoning
和运行权限相关覆盖。 Spawner 还显式带入触发 child 的环境快照、禁用插件选择和 multi-agent usage hints。 对话历史是另一条选择：
V2 fork_turns=none 走 fresh history； fork_turns=all 走 FullHistory； 默认值为 all。 当前解析器也接受旧的正整数值，但将其视作全量历史
fork，并不会按数字截断。
V2 显式传入旧 fork_context 布尔字段会被拒绝。 ToolRouter 是第三条路径： child 依据自身 model_info、provider
capabilities、配置和注册器重新生成 model-visible schemas 与 executors。 没有找到代码把 parent router 已 finalize 的
schema/executor list 塞进 ThreadSpawnRequest。
Child 与 parent 可能因为继承相同 config 和 model 而“看起来能力相同”，这和显式复制 ToolRouter 是不同结论。

| 维度                              | Fresh：fork_turns=none                              | FullHistory：fork_turns=all                                       |
|-----------------------------------|-----------------------------------------------------|-------------------------------------------------------------------|
| ThreadId / Session                | 新建身份和 Session                                  | 同样新建身份和 Session，不是复用 parent Session                   |
| 首条任务                          | 仍会发送 child 的 message 并启动 turn               | 同样发送 child 的 message 并启动 turn                             |
| Parent 对话 history               | 不复制整段 parent transcript                        | 作为初始历史快照，按 fork 过滤规则保留                            |
| 父子 developer 指令               | child 按有效 config / role 生成自己的初始 context   | 可替换识别出的 parent instructions；必要时追加 child instructions |
| V2 usage hint                     | child 初始 context 按 subagent source 选择提示      | fork initializer 可清理 parent hint，再附 child hint              |
| 工具 history                      | 不带 parent 的历史 tool call                        | 某些历史 response items 会保留为对话记录                          |
| live tool router / process handle | 不继承 live parent executor 或 shell process        | 仍不继承 live executor/session handle；旧调用只可能是历史记录     |
| model search 暴露                 | 由 child 当前 effective model / provider / 配置重算 | 同样由 child router 重算，不因历史 fork 自动增加能力              |
| 共享增长                          | 以后 parent / child 各自写自己的 history            | 以后同样各自写；fork 时复制的只是初始化前缀                       |

### 具体消息例子

**教学推演，不是线上 transcript。**
主上下文可能是：

~~~text
Developer: 你是根 agent，安排当前请求并整合结果。
User: 找到 mailbox 消息何时被记录到对话历史。
Assistant: 我会把实现链路交给一个 worker 查证。
Tool call: spawn_agent(task_name="mailbox-lifecycle",
                        message="沿本地 core 查 QueueOnly 消息如何从 mailbox 到 rollout。",
                        fork_turns="none")

~~~

使用 none 时，child 的语义起点可以概念化为：

~~~text
Developer: 当前线程的 subagent instructions / usage hint。
Agent task message: 查 QueueOnly 消息如何从 mailbox 到 rollout。

~~~

它没有 parent 的旧 user / assistant 轮次和 tool call 作为 transcript 前缀。 但它仍使用父 turn 传来的 child
config、角色选择、环境和禁用插件配置。 使用 all 时，child 的起点近似为：

~~~text
[筛过的 parent history snapshot]
Developer: child 身份和子任务边界说明。
Developer: child role instructions / usage hint（适用时）。
Agent task message: 查 QueueOnly 消息如何从 mailbox 到 rollout。

~~~

代码还处理 parent developer instruction 的替换、引用上下文 baseline、compaction metadata 和 Guardian history。
这里不要把“复制完整历史”简化成“复制了一切状态”： live tool session、process handle、parent 本地 authorization 和 child turn
state 都不是可由 transcript 自动带走的东西。

### 指令注入与搜索工具继承

V2 root / subagent usage hints 来自配置或 model multi-agent prompt message。 在初始 context 构造中，角色提示以独立
developer message 投入； child 的 SessionSource 选择 subagent variant。 FullHistory fork 还会避免把 parent identity
instructions 当成 child 的 live authority。

**静态证据：**
角色提示解析见 [session/multi_agents.rs](codex/codex-rs/core/src/session/multi_agents.rs#L14)，developer fragments
组装见 [session/mod.rs](codex/codex-rs/core/src/session/mod.rs#L4764)。 root / subagent
初始提示的测试为 [session/tests.rs](codex/codex-rs/core/src/session/tests.rs#L10911)
和 [session/tests.rs](codex/codex-rs/core/src/session/tests.rs#L10933)。 FullHistory 里过滤 parent prompt、插入 child
instructions / hint 的逻辑在 [control/spawn.rs](codex/codex-rs/core/src/agent/control/spawn.rs#L1134)
和 [control/spawn.rs](codex/codex-rs/core/src/agent/control/spawn.rs#L1355)。 搜索工具是否存在要看 child 的 router。
模型有 search capability 与 provider 对 search tool 的实现是条件之一； web-search mode 和 features 还会影响 standalone
web search executor 暴露。 参照 [spec_plan.rs](codex/codex-rs/core/src/tools/spec_plan.rs#L393)
和 [spec_plan.rs](codex/codex-rs/core/src/tools/spec_plan.rs#L1605)。 因此报告不承诺“主 agent 能搜索，child 就一定能搜索”。
更准确的表述是：child 继承部分有效配置，并用 child 当前 model/provider/config 重新做 tool planning。 若 spawn 指定新
model，search exposure 可能随模型能力变化。

## 4. V1 与 V2：模型和用户能观察到的区别

### 为什么版本要分清

Config resolver 会在 MultiAgentV2、Collab、legacy agents enablement 和模型元数据之间选择版本。 之后 tool plan 按版本注册不同
handler。 因此一次 function call 的 schema、允许字段、交付模式和 wait output 都会随版本改变。 不要只根据工具名相同就认为它们行为相同。

| 用户观察   | V1                                                          | V2                                                                              |
|------------|-------------------------------------------------------------|---------------------------------------------------------------------------------|
| 起始配置   | 旧 Collab 路径；可受 agents_enabled 影响                    | MultiAgentV2 可显式覆盖，模型 metadata 和 feature fallback 参与                 |
| 工具分组   | legacy multi_agent_v1 namespace                             | 新工具多数在可配置 namespace，默认 collaboration                                |
| 通信字段   | send_input 可指定 interrupt 重定向已有工作                  | send_message 与 followup_task 分成两种工具；send_message 不收 interrupt 参数    |
| 子任务恢复 | resume_agent 可显式恢复 closed agent                        | 通过本地 child reload / send 路径恢复，不是相同的 V1 resume tool                |
| 等待输入   | wait_agent 等指定 agent 到 final status，结果可能含最终消息 | wait_agent 等任意 mailbox update / steered input / timeout，仅返回 wait summary |
| 关闭       | close_agent 可结束并持久化关闭边                            | V2 提供 interrupt_agent；“打断当前工作”不等于永久 close child                   |
| Fork 参数  | fork_context 布尔值                                         | fork_turns；旧 fork_context 会失败                                              |
| 深度       | legacy max depth 参与暴露/嵌套 gate                         | V2 忽略 legacy depth 设置，依赖 V2 residency / execution capacity               |
| 查找目标   | 旧交互更多用 agent id                                       | V2 message tools 接受 canonical path 或相对 task path                           |

V1 / V2 resolver 和注册分支分别见 [config/mod.rs](codex/codex-rs/core/src/config/mod.rs#L1658)
与 [spec_plan.rs](codex/codex-rs/core/src/tools/spec_plan.rs#L1439)。 schema
定义见 [multi_agents_spec.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs#L70)、[multi_agents_spec.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs#L108)、[multi_agents_spec.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs#L166)、[multi_agents_spec.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs#L204)、[multi_agents_spec.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs#L236)、[multi_agents_spec.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs#L287)
和 [multi_agents_spec.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs#L303)。 V1 的 tool handlers 在
tools/handlers/multi_agents/。 V2 的 handlers 在 tools/handlers/multi_agents_v2/。ToolOutput 将 handler result
转成模型响应项；registry dispatch 起点在 [registry.rs](codex/codex-rs/core/src/tools/registry.rs#L615)
，返回类型见 [tools/context.rs](codex/codex-rs/core/src/tools/context.rs#L35)。
实际注册代码只在 [spec_plan.rs](codex/codex-rs/core/src/tools/spec_plan.rs#L1439) 的相应版本分支里完成； message_board
tools 是可选的另外一套 extension-style discussion tools，不能混进 V2 agent operation tool list 里解释。

## 5. 四种协作操作对 running / idle / unloaded 目标的语义

先定义这里的目标状态：

- **Running loaded**：目标 CodexThread 在 map 中，当前有执行 turn。
- **Idle loaded**：目标 runtime 在 map 中，但当前无运行 turn。
- **Locally evicted**：本进程曾加载过 V2 child，现在仅从 ThreadManager map 卸载；AgentRegistry 还可能保存 path /
  environment snapshot。
- **Cold-restorable**：当前 map 未加载该 child，需从已保存 thread metadata / rollout 重建；没有本次进程的 evicted
  environment snapshot。
- **Unknown / gone**：目标无法被 registry / persisted thread resolution 找到。下面不会把 unknown 伪装成成功运行。

| 操作            | Running loaded                                                          | Idle loaded                                 | Locally evicted V2 child                                                               | Cold-restorable V2 child                                            |
|-----------------|-------------------------------------------------------------------------|---------------------------------------------|----------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| send_message    | 投递 queue-only message；交付给当前输入路径，不打断或启动新 turn        | 进入 pending input，不因该工具单独启动 turn | 本地 runtime mailbox 暂存，不为 queue-only 强制加载                                    | 本地 send 路径会先按 stored history 懒加载，再排入 queue-only input |
| followup_task   | 向活动 turn 提交 follow-up，在可接收输入的边界处理，不是 interrupt      | 触发目标启动新 turn                         | 先 reload child，再触发 turn；capacity / owner / history 检查可能失败                  | 先从存储恢复 child，再触发 turn；restore 错误会返回                 |
| interrupt_agent | 发 Interrupt 操作并返回派发前观测到的 status                            | 发 interrupt 操作，但不因此启动工作         | 不 reload；对 unloaded / dead 的已知 child 按 V2 语义容忍                              | 目标必须能先被解析为已知 spawn agent；interrupt 自身不恢复 thread   |
| wait_agent      | 没有 target 参数；当前 caller 等任意 mailbox activity / steer / timeout | 同左；wait 不启动 child                     | 如果 caller 是 child，wait 只看 caller 自己的输入活动；不会按 path 去 load 某个 target | 同左；它不做 child lookup 或恢复                                    |

### 每个工具具体做什么

**send_message 是“提交消息”，不是“催它运行”。**
Handler 选择 QueueOnly，并调用共享消息提交路径。 给正在运行的 target 时，消息走 target 当前的 input queue / communication
path； 给已加载但 idle 的 target 时，内容会排队，工具本身不启新 turn。 本地已 eviction 的 child 可以不 reload：消息放在共享
LocalAgentRuntime 的 mailbox map。
cold-restorable child 则有特殊处理：API 注释说 cold-restored children 会在收到任意 message 时懒加载；本地已 eviction 的
child 可保留 queue-only mail 而不 reload。

**followup_task 是“投递并保证触发一轮工作请求”。**
handler 选择 TriggerTurn。 对于 idle target，这会开始 work；对于 active target，schema 描述为在采样边界或当前 tool call
完成后送达。 V2 不允许 child 把 root 作为 follow-up target。 V2 follow-up 若目标尚未加载，会先调用
ensure_v2_agent_loaded，再在持有 residency pin 的窗口内提交 input。
load failure、capacity failure、parent/config authority 变化都可能使本次调用返回错误；源码没有把失败改写成“任务已排队”。

**interrupt_agent 是当前执行控制。**
V2 本地实现先确认是已登记的 spawned child，拒绝 root / self，然后读取 status snapshot 并提交 Interrupt。 它不调用
ensure_v2_agent_loaded，所以不会为了打断一个 locally evicted worker 而重新加载它。 ThreadNotFound / InternalAgentDied 在该
V2 interrupt 路径被容忍；snapshot 可能是 NotFound 或最后能观测到的状态。 这不是永久关闭，也不是任务 claim 的取消字段。

**wait_agent 不是 wait-for-one-agent。**
它没有 agent id/path 参数。它先订阅 caller 当前 Session InputQueue 的 activity channel，再检查 pending activity；若没有已有事件便等待
mailbox signal、steered input 或 deadline。先订阅再查 pending 是为了避免检查与订阅之间的 event
漏唤醒。因此模型需要结合消息正文、list_agents 状态或其他输入判断具体哪个 child 回报了什么。工具结果只有 `message` 和
`timed_out`；message 报告 “Wait completed.”、“Wait interrupted by new input.” 或 “Wait timed out.”，并可能说明超时参数被收窄。它是通用等待状态
receipt，不是事件列表或 child result payload。

### 未加载的概念分层

“unloaded”至少包括两种情况，二者不能混写：

1. **本地 eviction**：同一 LocalAgentRuntime 知道这个 child 的 path / ThreadId，并保留过的环境选择与 bindings；QueueOnly
   可以挂到本地 runtime mailbox。
2. **进程重启后的 cold restore**：ThreadId / history / graph edge 可由持久 store 读取，但进程内的 AgentRegistry
   evicted-environments 与 Mailboxes map 不是那份磁盘记录。 恢复路径必须重新用存储信息和有效 owner/config 重建 Session。
3. **NotFound**：上述存储身份也不可解析。不要推论工具会自动创建一个同名 child。

**静态证据：**
V2 message helper 在 [message_tool.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_v2/message_tool.rs#L42)。
QueueOnly / TriggerTurn 由 [send_message.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_v2/send_message.rs#L34)
与 [followup_task.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_v2/followup_task.rs#L34) 选择。 本地 send 根据是否
TriggerTurn / 是否有 evicted environment 决定是否
reload，见 [control/api.rs](codex/codex-rs/core/src/agent/control/api.rs#L100)。 V2 interrupt no-reload
注释和错误容忍见 [control/interrupt.rs](codex/codex-rs/core/src/agent/control/interrupt.rs#L1)。
wait 的 subscribe-before-wait 流程在 [wait.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_v2/wait.rs#L40)
和 [input_queue.rs](codex/codex-rs/core/src/session/input_queue.rs#L152)。

## 6. 运行时 mailbox 到 history / rollout：消息到底存在哪里

### 两层 pending queue，再到已记录 history

一个 communication 可以经过以下层次：

1. sender 调用 AgentControl.send 返回 receipt 表示本地/backend 接受了操作，不等于接收者已经读过。
2. 若 recipient runtime 已加载，message 经 ThreadManager send op 到 Session 的 InputQueue。
3. 若 V2 child 本地 eviction 且请求为 QueueOnly，PendingAgentMessage 可留在共享 runtime Mailboxes。
4. child reload 时，mailbox 内容被 take 并先放入 Session InputQueue；eviction 时尚未读的 queue-only input 会被 drain 回
   runtime mailbox。
5. Session 将 InterAgentCommunication 转成模型可见的 AgentMessage history item。
6. history 记入内存后，PendingAgentMessage 的 MessageDelivery 被标记 consumed。
7. inter-agent message 与 trigger_turn metadata 随后交给 persist_rollout_items；写入失败会记录错误并返回 false，但调用路径不撤销已写
   history 或 consumed 状态。
8. rollout 的成功持久化可在 reload 时重建已记录的 conversation history；它不是 runtime Mailboxes 的序列化快照。

### 消息生命周期图

~~~mermaid
sequenceDiagram
    participant S as Sender Turn
    participant C as LocalAgentControl
    participant MB as LocalAgentRuntime Mailboxes
    participant IQ as Recipient InputQueue
    participant H as Recipient History
    participant R as Recipient Rollout
    participant O as Lifecycle Observer

    S->>C: send(InterAgentCommunication)
    C->>O: on_publish(message id, if contributors exist)
    alt unloaded locally-evicted QueueOnly target
        C->>MB: enqueue PendingAgentMessage + delivery ownership
        Note over MB: pending in local process memory
        MB->>IQ: take on reload, then enqueue in order
    else loaded target
        C->>IQ: submit inter-agent communication
    end
    IQ->>H: record_inter_agent_communication
    H->>O: delivery.consumed(recipient)
    H->>R: append AgentMessage + trigger_turn metadata
    R-->>H: recipient resume can reconstruct recorded history

~~~

### 两类 pending 与已提交消息

| 阶段                                                      | 主要持有者                                   | 已否进入 recipient history | 恢复含义                                                                     |
|-----------------------------------------------------------|----------------------------------------------|---------------------------:|------------------------------------------------------------------------------|
| 调用已被接受但未被模型处理                                | runtime Mailboxes 或 InputQueue pending list |                         否 | 本地 runtime 可跨 V2 residency eviction 暂存；不等于独立 durable message log |
| 进入当前 active turn 的 pending input                     | InputQueue / TurnState                       |                     不一定 | 仍在当前 runtime 交接中；要等 record_inter_agent_communication               |
| 已调用 record_inter_agent_communication 并写 history      | Session history                              |                         是 | 消息可以作为本线程历史参与本次运行                                           |
| inter-agent item 和 metadata 已 append / flush 到 rollout | ThreadStore / rollout                        |                         是 | 持久 thread 后续可重建已记录 AgentMessage                                    |
| child-parent spawn edge                                   | AgentGraphStore                              |                     不适用 | 只记录 thread 之间的 parent-child lifecycle 关系，不记录消息内容             |

**因此，不能只说“mailbox 是 in-memory，所以已交付消息都会丢”。**
源码确实有 in-memory Mailboxes，但消息被 history recorder 接收后会写进 rollout； rollout reconstruction 会重建历史
AgentMessage。 反过来，也不能说“send 成功返回就已持久化”： AgentControl 的接口把成功定义为 accepted / delivered to
operation boundary，而不是 read/consumed。
尚在 runtime mailbox 或 input pending queue 的消息没有因为 API receipt 自动变成 rollout item。 MessageDelivery 另外持有
extension lifecycle observer 的 publication state。 它在接收 history 记录时通知 on_consumed，消费所有权与 mailbox vector
的具体驻留位置不是一回事。 恢复历史里的通信会保留 protocol message 形状，但已恢复的历史不重发旧 publication callback。

**静态证据：**
本地 tree mailboxes 位于 [control/mailbox.rs](codex/codex-rs/core/src/agent/control/mailbox.rs#L15)，enqueue/take/watch
在该文件 L36、L59、L81。 已加载 recipient 的 input queue
转交在 [input_queue.rs](codex/codex-rs/core/src/session/input_queue.rs#L179)、[input_queue.rs](codex/codex-rs/core/src/session/input_queue.rs#L207)。
history recorder 在 [session/mod.rs](codex/codex-rs/core/src/session/mod.rs#L4149)；它把消息写为 response item 和
trigger-turn metadata，再调用 rollout persist，见该文件 L4225
和 [persist_rollout_items](codex/codex-rs/core/src/session/mod.rs#L4853)。 ownership / consumed observer
见 [agent_communication.rs](codex/codex-rs/core/src/agent_communication.rs#L101)
和 [agent_communication.rs](codex/codex-rs/core/src/agent_communication.rs#L146)。
测试 [session/tests.rs](codex/codex-rs/core/src/session/tests.rs#L2775) 说明 inter-agent history 会保留到 rollout 并参与
resume；[session/tests.rs](codex/codex-rs/core/src/session/tests.rs#L2650) 说明 message recording 要尊重已确认 delivery
的顺序边界。

### 完成通知与正文

V2 child turn 完成时，AgentControl 的 turn_finished 路径收到 terminal AgentTurnOutcome。 本地 completion handler：

- 对提供了 parent turn 的 Completed outcome，尝试向发起该任务的 thread 发一条 SubAgentActivity::Completed event。
- 根据 Completed (final text)、Errored、Shutdown 等状态格式化 result message。
- 把 result 作为 trigger_turn=false 的 InterAgentCommunication 发给直接 parent。
- 若 status 是 Interrupted，formatter 返回 None；它不会据此构造通常的 completion message。
- parent 若已加载，消息走 parent 的 InputQueue / history recorder；parent unloaded 时 queue-only message 可以先暂存于
  runtime mailbox。
- 这一路是 best-effort，而且没有 dedup。不能承诺 exactly-once completion notification。

Model 工具可调用 send_message 或 followup_task；这和系统 turn_finished 自动发送的 completion / result 消息是两种来源。 V2
wait_agent 对任何 mailbox activity 都可能醒，不会判断 activity 一定来自任务完成。

**静态证据：**
AgentControl 文档直接写明 local turn_finished best-effort 且 not
deduplicated，见 [agent/api.rs](codex/codex-rs/core/src/agent/api.rs#L132)。
实现见 [control/completion.rs](codex/codex-rs/core/src/agent/control/completion.rs#L27)
；状态到文本的映射见 [session_prefix.rs](codex/codex-rs/core/src/session_prefix.rs#L20)。 V2 wait 的 schema 只承诺 mailbox
activity summary，在 [multi_agents_spec.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs#L303)。

## 7. V2 capacity、驻留 eviction 与恢复不是同一个 scheduler

### 三个分别管理的容量 / 状态

**Spawn identity / registry。**
AgentRegistry 登记 path、thread id 和环境快照。 V1 legacy 分支使用 registry 限制总 child 数和 max depth。 V2 residency
spawn 会走自己的 slot 流程，调用旧 registry 时不一定以相同方式限制 child lifetime 总数。 所以不要拿
AgentRegistry.total_count 说成 V2 可同时运行 worker 数。

**Resident child runtime。**
V2 max_concurrent_threads_per_session 默认 4，effective_agent_max_threads 为此值减一，因为配置以 root-inclusive thread
capacity 表达；配置定义与换算见 [config/mod.rs](codex/codex-rs/core/src/config/mod.rs#L1375)
和 [config/mod.rs](codex/codex-rs/core/src/config/mod.rs#L1687)。 local V2 residency 将 effective child capacity 用于驻留
slot。 默认配置下数字推导是 3 个 child residency slots；这是配置实现事实，不是对所有产品设置或进程的承诺。 Slot reserve 在
mutex 下同时计入 residents 与 pending_slots；
slot 满时尝试按 resident 顺序卸载 idle child，成功卸载一个再重试 reservation。

**Currently executing V2 child turns。**
AgentExecutionLimiter 另有 active counter 与 maximum。 它只检查 MultiAgentV2 subagent turn；root 和 V1 不计这个 guard。
AgentControl trait 特别说明 check_turn_admission 是 advisory，不预留 slot； admit_turn 以 separate capacity check and
running count 工作，不做 atomic reservation。
具体 local 代码先读 active < max，再在 execution_guard 里 fetch_add； 这不是严格的 semaphore 许可原子操作，不保证高争用时绝不短暂
oversubscribe，也没有公平 task wait queue。

### 容量层次图

~~~mermaid
flowchart TB
    C[config max_concurrent_threads_per_session]
    C --> E[effective child capacity: subtract root]
    E --> R[V2 resident slots]
    E --> X[V2 execution limit setting]
    R --> S[loaded child CodexThread runtimes]
    R --> P[pending slot reservations during spawn]
    X --> A[advisory check_turn_admission]
    A --> G[admit_turn execution guard increments active]
    G --> T[one active subagent turn]
    S --> I[eviction eligibility]
    I -->|idle and safe| U[materialize rollout, stop, drain mail, save env, remove runtime]
    I -->|running or busy| N[not evictable now]

~~~

### Eviction 的实际判定

当申请新 child residency slot 时，如果 resident 数量加 pending reservations 已到容量，local code 遍历 residency order 中的
candidates。 PendingInit 的 child 不会被拿来作 capacity eviction； 其他候选还须满足 is_unloadable 条件：

- 候选须处于可卸载的终态集合（Completed、Errored 或 Interrupted）；capacity-selection 还会跳过 PendingInit；
- 当前 active_turn 必须为空；
- 不得有 outstanding durable sleep；
- InputQueue 里不能有需要 TriggerTurn 的 mailbox item。

成功 eviction 的重点顺序是：

1. 获取该 thread 的 write residency gate；已有 delivery / reload pin 时返回 Busy。
2. 确认 map 中仍是同一个 CodexThread identity。
3. materialize history、停止 runtime。
4. drain 已加载 Session 的 mailbox pending items。
5. 将尚未消费的消息转移回共享 LocalAgentRuntime mailboxes。
6. 保存 environment selections / configured bindings。
7. 再确认没有 replacement runtime，然后从 ThreadManager map 移除旧 runtime 并释放 residency slot。

这是 runtime 卸载，通常不是关闭 child 的持久 parent-child edge，也不是删除已写 rollout。 如果正在运行的工具、turn、durable
sleep 或 follow-up mail 仍在，系统不会把该 child 当成任意可卸载 idle slot。

### 恢复路径的边界

V2 本地 child reload 通过 ensure_v2_agent_loaded。 它可以读取 stored thread metadata 与 history，重建 InitialHistory::
Resumed，应用存储 model/provider/reasoning、role、environment / execution policy 检查，再重新注册 thread runtime。 若由
parent 作 owner-aware load，parent 必须仍可验证、running、是该 child 的当前父 runtime，parent/child 必须属于同一 tree
runtime；父亲 authority 不可用时会要求先恢复 parent。 Sender-driven reload 则保留另一路径。
恢复过程不自动等于“把整个 subtree 一次性全部启动”。 V2 rollout resume 会恢复指定 child，不自动 reopen
descendants；测试入口见 [control_tests.rs](codex/codex-rs/core/src/agent/control_tests.rs#L1518)。 因此恢复可以是按需的，而不是后台
scheduler 遍历所有待办。

### 概念伪代码

下面是教学伪代码，非实际 Rust，也没有表达每个锁和错误分支。

~~~text
reserve_child_slot():
    while resident_children + pending_slots >= capacity:
        if not evict_one_idle_child():
            return AgentLimitReached(ResidencyCapacity)
    pending_slots += 1
    return SlotGuard

evict_one_idle_child():
    for child in residency_order:
        if child.is_running or child.has_active_turn:
            continue
        if child.has_durable_sleep or child.has_trigger_mail:
            continue
        if not acquire_child_write_gate():
            continue
        flush_history()
        shutdown_runtime()
        move_unread_queue_mail_to_tree_mailbox()
        retain_environment_snapshot()
        remove_loaded_runtime_but_keep_thread_identity()
        return true
    return false

~~~

实际代码见 [control/residency.rs](codex/codex-rs/core/src/agent/control/residency.rs#L40)、[control/residency.rs](codex/codex-rs/core/src/agent/control/residency.rs#L218)
和 [control/residency.rs](codex/codex-rs/core/src/agent/control/residency.rs#L323)。 spawn slot reservation
的测试 [residency_tests.rs](codex/codex-rs/core/src/agent/control/residency_tests.rs#L23) 会让新 child slot eviction
oldest idle child。

## 8. 三个来自源码边界的失败与竞态推演

### 例一：admission check 与 active count 不是一个原子 reservation

当一个 turn 进入时，check_turn_admission 先检查 active count。 之后 admit_turn 创建 guard 时才 fetch_add。 两个并发请求可能都在
fetch_add 前看到 capacity，随后各自增计数。 接口注释明确说这不是 atomic reservation。
这意味着报告不能将 active limit 描述成严格 mutex semaphore，或许诺“永不超过 max”。 它仍记录活跃 turn 并在常见路径提早拒绝超额请求；
可证明的保证止于 source contract，不是所有 schedule 下的数学互斥上限。

**静态证据：**
[agent/api.rs](codex/codex-rs/core/src/agent/api.rs#L109) 与 [agent/api.rs](codex/codex-rs/core/src/agent/api.rs#L118)。
local check / counter 在 [control/execution.rs](codex/codex-rs/core/src/agent/control/execution.rs#L34) 和该文件 L53。
测试 [execution_tests.rs](codex/codex-rs/core/src/agent/control/execution_tests.rs#L18) 覆盖 guard count
的顺序情形，没有证明高争用严格限额。

### 例二：eviction 不能在消息交付中间删 runtime

child 的 residency gate 在交付 / reload 路径中保持 read pin； eviction 需要 try-write gate。 若 delivery 持有读
pin，eviction 得到 Busy，不应删掉仍在交接的 Session。 若 eviction 已进入不可撤销 teardown，它 materialize history 并把尚未消费的
mailbox message 移回共享 runtime mailbox，随后才移除 CodexThread。
队列 ownership 的迁移是“不要丢失本地已接受但尚未消费的队列内容”的机制，但不升级为磁盘持久消息保证。

**静态证据：**
CodexThread residency gate 注释见 [codex_thread.rs](codex/codex-rs/core/src/codex_thread.rs#L208)。 stop、drain、保存
env、删除 map entry 顺序见 [control/residency.rs](codex/codex-rs/core/src/agent/control/residency.rs#L24)。
可读测试 [control_tests.rs](codex/codex-rs/core/src/agent/control_tests.rs#L1095)
和 [control_tests.rs](codex/codex-rs/core/src/agent/control_tests.rs#L1431) 覆盖 cancel-reload 和 pin 阻止 eviction
的具体路径。

### 例三：发送方被取消，不等于已开始的 child reload 应留成半注册

V2 follow-up 可以先触发 child reload，再等待注册 / residency pin 允许消息提交。 测试把 send future 取消在 reload
已开始、publication 尚未完整的时点； 随后断言 reloaded child 最终完成注册，能 eviction 后再次 reload，并且 pending slot /
pin 没泄漏。 所以“调用方工具被 cancel”与“child runtime 的构建事务应被放弃”被分开处理。
这个测试没有证明 cancelled follow-up 的原消息一定送达。 它证明的是 reload registration cleanup / ownership 自身仍能完成，后续新
follow-up 可再成功发送。

**静态证据：**
[test name: cancelled_v2_send_finishes_reload_registration](codex/codex-rs/core/src/agent/control_tests.rs#L1095)。 恢复入口和
pin contract 见 [control/spawn.rs](codex/codex-rs/core/src/agent/control/spawn.rs#L325)。

## 9. ThreadSpawn graph 与真正的 Message Board

### 线程树不是任务表

V2 child 的 agent path 类似 /root/research/mailbox。 父线程来自 SubAgentSource::
ThreadSpawn.parent_thread_id；depth、path、role/nickname 等数据和线程身份一起表达层级。 AgentControl.resolve / list / child
paths 是按当前 tree 身份和 prefix 找到 agents。 本地协作没有 task object 生命周期，因而没有谁 claim “search mailbox
lifecycle”、谁把 task 标 done 的共享权威状态。
AgentGraphStore 的持久边也不是任务表。 ThreadSpawn edge 连接 parent_thread_id 与 child_thread_id，open/closed 状态描述
child thread edge 生命周期。 它可用于恢复 / list child relationships，不含任务标题正文、owner assignment 或 completion
checklist。 数据库表证据在 [0021_thread_spawn_edges.sql](codex/codex-rs/state/migrations/0021_thread_spawn_edges.sql#L1)。

### Message Board 是共享讨论

可选 Message Board 的工具是真正共享 board namespace 下的 discussion surface： create/list channel、list/search posts、read
thread / post、subscribe、unsubscribe、post / reply。 一条 post 可以被其他 agent 搜索或读取，因此团队可以把文字贴在共享讨论区；
这仍不自动产生任务分配器、状态机或 exactly-once notification bus。
源码中的通知文案规定了两个重要边界： 通知只送给正在运行 turn 的目标； 漏掉的通知不保存； 被 post 点名不会唤醒 idle agent。
因而 board post 本身和 send_message/followup_task 的目标唤醒语义也不同。 Agent Message Board config 可以选择 in-memory /
remote 配置； 没有在本文验证某一个 deployment 的实际服务 durability、事务边界或重启恢复。 请勿从 “board” 名称或 repository
tool schema 推导出来。

**静态证据：**
V2 config 的 board fields 在 [config/mod.rs](codex/codex-rs/core/src/config/mod.rs#L1375)。 board tools schema 说明
channel/post/search
在 [message-board/tools/spec.rs](codex/codex-rs/message-board/src/tools/spec.rs#L19)、[message-board/tools/spec.rs](codex/codex-rs/message-board/src/tools/spec.rs#L41)。
订阅通知与 post 点名规则分别在 [message-board/tools/spec.rs](codex/codex-rs/message-board/src/tools/spec.rs#L64)
和 [message-board/tools/spec.rs](codex/codex-rs/message-board/src/tools/spec.rs#L74)。 host install entry
在 [agent_message_board.rs](codex/codex-rs/core/src/agent_message_board.rs#L39)。

## 10. 设计取舍与适用结论

### 这套设计当前解决什么

- **Tool contract 明确分流**：旧模型调用与新模型能力通过 V1 / V2 schema 路由，避免把 V1 interrupt 输入隐式塞进 V2
  send_message。
- **上下文受控**：none / full-history 选择初始化 prompt 边界；child 使用角色提示表达 parent / child 身份区别。
- **状态 ownership 分层**：ThreadManager map 管 loaded runtime，AgentRegistry 管本地 tree metadata，rollout
  管已记录会话，AgentGraphStore 管 parent-child edge。
- **消息模式显式**：QueueOnly 表示把内容放到输入通道；TriggerTurn 表示在消息之外请求启动 / 推进一次 turn。
- **内存容量可回收**：对 idle V2 child 可卸载运行时，同时保留 thread history / 可重建所需 state；新 child 申请 resident
  slot 时可回收老 idle child。
- **接口支持不同 backend**：AgentControl 把调用面抽出来；但本地 best-effort mailbox semantics 不会自动变成远端的重试/持久/恢复保证。

### 这份源码不支持的保证

本文没有证据声称存在共享任务 claim / completion state machine、严格原子 execution semaphore、独立持久 mailbox journal 或
exactly-once completion notification。也不保证 child 总有与 parent 相同的 search tools，或所有 provider / host backend
共用本地恢复语义。

## 11. 建议的阅读与测试顺序

用户熟悉 Python、刚学习 Rust，建议沿 Prompt / Context → Agent → Tools / output 阅读； 每次先跟数据结构和值流，之后再回头看
trait / ownership 细节。

1. 看 [session/turn.rs](codex/codex-rs/core/src/session/turn.rs#L173) 的 run_turn
   与 [context_manager/history.rs](codex/codex-rs/core/src/context_manager/history.rs#L603) 的 for_prompt。 对照 Python
   中一次 request input 的构造过程。
2. 看 [session/multi_agents.rs](codex/codex-rs/core/src/session/multi_agents.rs#L14)
   与 [session/mod.rs](codex/codex-rs/core/src/session/mod.rs#L4764)。 记录 root / subagent developer text 从何处来、在哪里进
   prompt。
3. 比较 [multi_agents_spec.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_spec.rs#L108) 与 V2 spawn handler。
   Schema 定义模型可以说什么，handler 定义本地发生什么。
4.
跟 [thread_spawn.rs](codex/codex-rs/core/src/thread_spawn.rs#L58) → [child_config.rs](codex/codex-rs/core/src/agent/child_config.rs#L52) → [agent/api.rs](codex/codex-rs/core/src/agent/api.rs#L41)。
先把 Rust struct 字段流看懂，再关注 Arc、BoxFuture 与 trait object。
5. 看 [control/api.rs](codex/codex-rs/core/src/agent/control/api.rs#L100)
   与 [control.rs](codex/codex-rs/core/src/agent/control.rs#L259)。 分清同一 send 操作如何根据 QueueOnly /
   TriggerTurn、loaded / unloaded 状态走不同支路。
6.
看 [input_queue.rs](codex/codex-rs/core/src/session/input_queue.rs#L179) → [session/mod.rs](codex/codex-rs/core/src/session/mod.rs#L4149) →
rollout tests。 这能把 runtime pending input、history commitment 和 rollout persistence 分开。
7. 最后看 residency / execution tests。 不要从名字里的 max-concurrent 直接假设 scheduler 或强一致 capacity semantics。

相关行为测试入口只说明测试意图；本文没有运行测试：

- [session/tests.rs](codex/codex-rs/core/src/session/tests.rs#L3199)
  resolve_multi_agent_version_handles_unset_and_legacy_history：版本选择；[session/tests.rs](codex/codex-rs/core/src/session/tests.rs#L10933)
  build_initial_context_adds_multi_agent_v2_subagent_usage_hint_as_developer_message：V2 subagent developer hint。
- [multi_agents_tests.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_tests.rs#L1222)
  multi_agent_v2_spawn_rejects_legacy_fork_context：V2 拒绝旧字段。
- [multi_agents_tests.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_tests.rs#L1897)
  multi_agent_v2_followup_task_completion_notifies_parent_on_every_turn：follow-up completion。
- [multi_agents_tests.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_tests.rs#L3472) / [multi_agents_tests.rs](codex/codex-rs/core/src/tools/handlers/multi_agents_tests.rs#L3746)
  ：wait 摘要与不返回正文。
- [control_tests.rs](codex/codex-rs/core/src/agent/control_tests.rs#L1095)
  cancelled_v2_send_finishes_reload_registration：cancelled caller 不留下半注册
  child；[residency_tests.rs](codex/codex-rs/core/src/agent/control/residency_tests.rs#L23)：slot eviction。
- [session/tests.rs](codex/codex-rs/core/src/session/tests.rs#L2775)
  record_inter_agent_communication_sets_turn_id_in_rollout_and_resume：已记录消息可从 rollout
  恢复；[control_tests.rs](codex/codex-rs/core/src/agent/control_tests.rs#L1518)：resume 不自动重开 V2 descendants。

## 固定提交来源与核对边界

固定 SHA 的 GitHub
上游源码入口： [tool registration](https://github.com/openai/codex/blob/b337fe06b7d8d6e5b42295e04c5e92c2b9c46567/codex-rs/core/src/tools/spec_plan.rs)、 [thread spawn](https://github.com/openai/codex/blob/b337fe06b7d8d6e5b42295e04c5e92c2b9c46567/codex-rs/core/src/thread_spawn.rs)、 [child control](https://github.com/openai/codex/blob/b337fe06b7d8d6e5b42295e04c5e92c2b9c46567/codex-rs/core/src/agent/control/spawn.rs)。
上述报告以 b337fe06 克隆的静态源码和 test definitions 为事实基线。 教学用时序图 / Python 模型 / 文本消息样例只是解读路径，不声称被实际执行。
尚未确认的远端 AgentControl backend、不同 Message Board deployment durability、全 provider 搜索能力，以及高争用下执行
admission 的精确 oversubscription 窗口，均不向更强保证外推。
