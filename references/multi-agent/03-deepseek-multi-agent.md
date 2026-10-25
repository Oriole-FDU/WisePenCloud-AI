# DeepSeek Harness：多 Agent 实现报告

> 本文直接讲解实现机制，并保留源码证据供进一步核查。只读核对的克隆基线为
> 687f282e8625c6b8d11ff58211a90524be2e4317。没有修改、构建、安装或运行克隆，也没有运行测试。下面标为“教学推演”的伪代码和时序只帮助理解合同，不是执行记录。

## 先看结论

1. Harness 通过 SubagentRuntime、Provider 和 Manager 管理受托执行与可续接子会话。模型通过 tool-subagent 消费它；本地路径可以把
   child Session 留作后续 continuation，
   外部路径则可能只持有一次执行的 handle。
2. 实验性 Agent Team 是建立在可续接直接子代理之上的协作层。它为一个 Lead-root Session 增加成员 roster、共享任务 DAG、Team
   事件日志和消息路由。任务板本身不会启动模型工作；消息遵循目标 Agent 的接收和持久化策略，Team 不另建发送意图日志、重试或去重协议。
3. workflow-ptc 也是子代理服务的消费者，但把结果交给程序化 caller，并由 workflow 自己保存 handle、等待结果和释放执行；这和由模型自主选择工具的
   Agent Team 属于不同层次。

这份报告重点说明“谁持有哪种状态、调用在哪一步返回、重启或空闲时发生什么”。 它不把源码之外的部署假设当成事实，也不把
writeScopes 或任务 owner 误读为 文件系统权限。

## 1. 先把名词分开

### Agent、Session、Turn、Step、Activation

- **Agent** 是当前运行时中的参与者对象。它有身份 id、当前选项、所属 Session、 inbox、状态及工具/上下文能力。teammate 和 Lead
  都是 Agent；“成员”是 Team roster 对 Agent 的角色描述，不是新的模型运行时。
- **Session** 是 Agent 对话及运行事件的记录边界。Session 由事件日志驱动，模型历史 是从日志派生出来的视图；Session
  可以经过普通持久化服务保存和查询。Team 的 member/task 事件写在 Lead Session，teammate 自己的对话和
  continuation 元数据 则在 child Session。
- **Turn** 是 Agent 一次完整的模型运行周期，可能包含多个工具步骤。Turn 以 turn/start 开始，以 turn/end
  结束；结束原因可能是正常完成、取消或错误。
- **Step** 是 Turn 中更细的一次执行推进，典型边界是一轮模型调用及其请求的工具。 运行中的 Agent 通常在某个 step 边界消化
  steer 消息，而不是在任意指令中间改写 已经开始的模型调用。
- **Activation** 是 Manager 持有的一次子代理驻留/执行周期。它可以指向一个本地 AgentHandle，也可以指向外部
  SubagentRun。child id / child Session 可跨 activation 存在；一个冷恢复会重建新的
  activation，不是让旧的内存对象“复活”。

可用这个 Python 类比理解类型关系：Agent 类似进程内的运行对象，Session 类似它 读写的事件流；Turn / Step
像一次任务和任务里的调度片段；Activation 像一个由 Supervisor 管理的执行租约。这个类比只用于定位状态归属，不意味着 Session
等同
数据库表，也不意味着 Activation 等同操作系统进程。

    TeamId = root SessionId

    Lead Agent  ──运行于──> Lead Session（TeamId）
       │                         ├── team/member 事件
       │                         └── team/task 事件
       └── 直接 child Agent ──> child Session（对话、descriptor、inbox）

事实锚点：Agent runtime shape 见 [Agent](deepseek-harness/packages/core/agent/src/types.ts#L14-L18) 和
[AgentRuntime](deepseek-harness/packages/core/agent/src/runtime-types.ts#L164-L231)； Turn/Step 事件合同见 [Session
EventMap](deepseek-harness/packages/core/session/src/types.ts#L276-L302)； Session event log 与派生模型历史的说明见
[session](deepseek-harness/packages/core/session/src/index.ts#L1-L5)； TeamId 定义见
[types.ts](deepseek-harness/packages/experimental/agent-team/src/types.ts#L8-L18)。

### Service、Provider、Manager 与 Consumer

一条调用链至少有四种职责，不能将同名“子代理”都当作执行后端：

    Consumer（模型工具 / workflow / Team tool）
       │ 选择 delivery，消费 child id 或 result
       ▼
    SubagentRuntime 校验请求并选择 Provider
       ├── 本地 prepareContinuable -> Manager -> Agent / Session / inbox / resume
       └── 外部 start              -> Manager -> SubagentRun / result / dispose

| 职责          | 负责的事                                                            | 源码入口                                                                                                          |
|---------------|---------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| Service 门面  | provider registry、请求校验、路由到 manager、公开等待/消息接口      | [SubagentRuntime](deepseek-harness/packages/subagent/subagent/src/index.ts#L194-L284)、registerProvider L539-L565 |
| Provider      | 声明如何开始一次外部运行，或为可续接本地 Agent 提供创建 seed 与能力 | [SubagentProvider](deepseek-harness/packages/subagent/subagent/src/types.ts#L305-L355)                            |
| Manager       | 容量、父子归属、创建/恢复、收件箱准入、自然结束、结果及资源释放     | [SubagentManager](deepseek-harness/packages/subagent/subagent/src/manager.ts#L247-L290)、L379-L520                |
| 普通模型工具  | 把模型的委派参数转成 parent-delivery 请求，并把 child id 返回给模型 | [tool-subagent](deepseek-harness/packages/subagent/tool-subagent/src/index.ts#L193-L219)、L276-L337               |
| Team Service  | 校验成员身份，协调 roster、任务、消息和等待，不负责运行模型         | [TeamService](deepseek-harness/packages/experimental/agent-team/src/index.ts#L55-L109)                            |
| Workflow Host | 保存 caller-owned handle，等待 result，并在结束时 dispose           | [WorkflowPtcHost](deepseek-harness/packages/workflow/workflow-ptc/src/host.ts#L178-L255)                          |

一个关键分界是 Provider 的两种入口。prepareContinuable () 为 manager 提供 本地 Session 创建资料；随后 manager 自己创建或恢复
Agent、接收 prompt、处理消息 及冷恢复。start () 则把一次工作交给外部实现，并返回外部 run
句柄。Provider 不是 Team 任务调度器，Team task board 也不是 Provider。 证据锚点：SubagentProvider.start/prepareContinuable
的接口注释在
[types.ts](deepseek-harness/packages/subagent/subagent/src/types.ts#L313-L355)； Service 的按能力分派在
[index.ts](deepseek-harness/packages/subagent/subagent/src/index.ts#L255-L284)。

## 2. 普通 subagent：模型委派、返回值和结果交付

### 请求从哪里进入

普通模型委派由 tool-subagent 安装为 Agent 可见工具，每个工具实例通过配置绑定一个子代理执行后端。模型给出任务；工具还可按配置暴露
preset 或 LLM 路由选择参数。工具取得当前 Agent，将请求交给 SubagentRuntime，并指定 delivery=parent。Runtime 检查调用者与后端能力，然后让
Manager 选择本地 continuation 或外部 run。工具响应包含 child id，而不等待子代理最终输出。

这里的两个 provider 要分清：插件的 `config.provider` 选择子代理执行后端；模型参数中可选的 `provider` 与 `model` 组合选择子级
LLM 路由。后者并不让模型任意更换当前工具绑定的执行后端。实际 schema
和绑定逻辑见 [tool-subagent](deepseek-harness/packages/subagent/tool-subagent/src/index.ts#L189-L272)。

    模型 -> tool-subagent -> SubagentRuntime.startActivation
                                  │ 按 Provider 能力分派
                                  ├── prepareContinuable -> 本地 child Agent / inbox
                                  └── start -> 外部 SubagentRun
    工具立即返回 child id；child settle 后 Manager 清理并按 parent delivery 通知父 Agent

**教学推演，非真实运行结果：**下面只展示接口意图，不是可复制的 API 调用。

    async def delegate(question):
        receipt = await subagents.start_activation(
            provider="spawn",
            task=question,
            delivery="parent",
        )
        # 工具此处交付一个引用；child 可能还在工作。
        return {"child_id": receipt.child_id}

    # 随后 Agent 自己继续推理或调用别的工具。
    # child 自然结束后，parent 收到子代理结果/完成通知。
    # 如需汇总，parent 再读取通知并将结果整合进最终回答。

为什么工具立即返回 id？源码合同将“启动 child”与“等待 child 结果”拆成两个 操作：startActivation 返回 activation
receipt，模型工具投影成 id；Manager 在 child settle 后执行 parent
delivery。这样模型调用不会因为同步等待整项子任务而 占住当前工具调用。**研究判断：**这是由 API 返回时点和后续通知路径推导的交互
设计效果，不是源码注释中的产品动机声明。

这里需要区分两个接收方：

| 路径   | 谁拿到结果                           | start 阶段返回什么                         | 结束后如何继续                                                    |
|--------|--------------------------------------|--------------------------------------------|-------------------------------------------------------------------|
| parent | 建立 child 的直接父 Agent            | child id；本地路径另有初始 inbox messageId | Manager 在清理后向原父 Agent 投递结束消息，并按父状态 queue/steer |
| caller | 调用 SubagentRuntime 的程序化 caller | child id 与 result/dispose 能力            | caller await result；没有 parent 结束通知                         |

本地 parent-delivery 的初始 prompt 经 child Agent inbox 接受后，start 会返回 receipt， 不是 child 完成值。自然结束时，Manager
捕获输出及 stop reason，释放准确的 activation handle，再决定是否通知父
Agent。delivery=caller 时通知函数直接跳过 父级通知，result Promise 是调用方合同的一部分。 源码锚点：工具参数和 execute 在
[tool-subagent](deepseek-harness/packages/subagent/tool-subagent/src/index.ts#L276-L337)； parent/caller 和 child id
的类型合同在
[SubagentActivationSpec](deepseek-harness/packages/subagent/subagent/src/types.ts#L30-L58)； 本地与外部启动在
[manager.ts](deepseek-harness/packages/subagent/subagent/src/manager.ts#L379-L520)； 清理和 settlement
通知在 [finishDisposal /
notifySettlement](deepseek-harness/packages/subagent/subagent/src/manager.ts#L1155-L1205)。

### 一条具体调用链

1. tool-subagent.execute 读取当前 Agent 的调用上下文，并验证工具参数。
2. 工具调用 SubagentRuntime.startActivation，默认选择配置 provider，指定 parent delivery，给 Manager 提供任务和必要的预算/输出选项。
3. Runtime 解析 capability：Provider 有 prepareContinuable 时进入 startLocal；否则进入 startExternal。
4. 本地路径先准备 child id、descriptor、parent lineage 和 delegated policy，再创建 child Agent；prompt 作为普通 Agent inbox
   消息被接受，child 开始或等待调度。
5. startLocal 返回 messageId 和 child id。模型工具只返回 id，不消费 result Promise。
6. child 可在同一 Session 上后续接收 follow-up；自然结束后 Manager 收集本次 activation 的输出，释放资源，按 delivery 发
   caller result 或 parent 通知。

以上步骤把“创建会话”“接受消息”“模型运行完毕”拆成不同事件。报告中使用“接受”只指 inbox admission 成功；不能把它改写成“模型已执行”或“任务成功”。

### 父代理究竟收到多少内容

本地 continuation 的初始 prompt 会附加返回指导：子级应通过 `send_message` 发送自包含结果，父方共享 workspace，但不会自动获得子级全部
transcript、工具输出和 reasoning。发送中间发现也不会结束子级 turn。

自然结算另外构造宿主拥有的 settlement notice。当前实现会保留子级 closing output 中的非空 text blocks，并按实际结果附带
structured result 或 diagnostic；没有 closing text 时会明确报告。它仍不是子级完整事件日志或所有搜索工具调用的复制。

因此，显式报告可以传递对父方下一步有用的证据，自动结算通知则说明结束原因并提供 closing text。通知路径检查父 Agent
仍是原实例，投递异常会记录日志，不提供无条件可靠送达保证。证据见 [返回指导与结算消息构造](deepseek-harness/packages/subagent/subagent/src/continuation-messages.ts#L75-L167)
和 [notifySettlement](deepseek-harness/packages/subagent/subagent/src/manager.ts#L1184-L1205)。

## 3. Spawn 与 Fork：transcript、preset、route 和权限不是一件事

两个代表性的 in-process Provider 都选择可续接本地 Agent，但对创建时的对话 seed 作不同选择。这个差异不决定该 child
有哪些工具或系统服务。

| 维度               | spawn in-process                        | fork in-process                                                            |
|--------------------|-----------------------------------------|----------------------------------------------------------------------------|
| 初始 transcript    | 返回空 seed，不复制父 Session 对话      | 复制父 Session 到最近一个完整 turn/end 的前缀；不截取正在进行中的半个 Turn |
| seed 的时间        | 本次 child 创建时决定                   | 创建时捕获一次；后续不会追随父 Session 自动增长                            |
| 独立 preset 选择   | provider capability 允许请求指定 preset | capability 不允许同等的独立 preset 选择                                    |
| child 后续生命周期 | Manager 本地 continuation               | 同一个 Manager 本地 continuation                                           |
| Team 含义          | 可用作 fresh teammate 的后端            | 可用作 fork teammate 的后端；不会因此自动变成 Lead/Team                    |

源代码：[SpawnInProcessProvider](deepseek-harness/packages/subagent/subagent-spawn-in-process/src/index.ts#L32-L59)，
[ForkInProcessProvider](deepseek-harness/packages/subagent/subagent-fork-in-process/src/index.ts#L38-L79)。

**教学推演，非真实运行结果：**

    parent Session: 完成的 turn 前缀 + 当前未结束的 turn
    spawn seed:     空 transcript + 新任务 prompt
    fork seed:      截至最近 turn/end 的完整前缀 + 新任务 prompt
    parent 后续新 turn: 已创建的 fork seed 不会自动增长

使用时至少分开看四层：

| 层                         | 实际决定什么                                   | 本地 child 的相关实现                                                                      | 不应推出的结论                    |
|----------------------------|------------------------------------------------|--------------------------------------------------------------------------------------------|-----------------------------------|
| transcript seed            | child Session 开始时的历史                     | spawn / fork provider                                                                      | 空 seed 不表示无工具              |
| Agent preset / composition | child 安装哪组 Agent 能力与上下文组合          | [child-agent.ts](deepseek-harness/packages/subagent/subagent/src/child-agent.ts#L156-L218) | fork transcript 不自动决定 preset |
| route options              | provider、model、effort、maxTokens 等路由/预算 | [child-agent.ts](deepseek-harness/packages/subagent/subagent/src/child-agent.ts#L68-L118)  | 不能把每项都视为权限              |
| delegated policy           | 可写入 child 的有限权限种子                    | [child-agent.ts](deepseek-harness/packages/subagent/subagent/src/child-agent.ts#L220-L279) | 不是父权限对象的整体复制          |

未显式选独立 preset 时，本地 child 组合可基于 parent context；显式 preset 则按 选中的 preset 建立 child 组合。route options
默认有父侧值的继承与 capability 允许的覆盖。provider 的
inheritsParentContext 只谈 transcript 语境，不承诺工具、 注入服务或权限会继承。delegated policy 仅从允许的信息生成子会话初始策略，而不是
把 parent 当前所有权限不加区分地复制过去。

**教学推演：**想象父 Agent 可使用“网页搜索”和“代码编辑”两个工具。spawn 只让 child 从空 transcript 开始；如果 preset
composition 仍装入同一组工具，child 仍 能看到这些工具。反过来，fork
得到父历史也不自动获得新工具或更高权限。判断能力 要分别读 preset composition、tool filters、route options 与 policy seed。
源码锚点：Provider 的 inheritsParentContext 注释见
[SubagentProvider](deepseek-harness/packages/subagent/subagent/src/types.ts#L313-L323)； 子 Agent composition、工具过滤及授权种子见
[child-agent.ts](deepseek-harness/packages/subagent/subagent/src/child-agent.ts#L156-L279)。

## 4. 本地 continuation、external run、取消与恢复

### 两种执行资源的能力边界

Manager 的 Activation 联合类型把“本地 Agent 句柄”和“外部 run 句柄”明确分开。 两者都能被 Manager 观察完成、捕获结果和
dispose，但本地 Agent 能接收继续消息， 外部句柄没有通用的 Harness 本地 inbox continuation
接口。

| 能力          | 本地 continuation                                                      | 外部一次执行                                   |
|---------------|------------------------------------------------------------------------|------------------------------------------------|
| Provider 入口 | prepareContinuable() 提供创建 seed / 能力                              | start() 返回 SubagentRun                       |
| Manager 持有  | child AgentHandle、Session 和 activation 状态                          | 外部 run 句柄和结果状态                        |
| 后续本地消息  | 可向已驻留 child 投递；未驻留时可走冷恢复                              | requireLocalActivation 拒绝通用 follow-up      |
| 长期身份      | child id / Session 可持久化                                            | 不由接口保证可重建的 local Session             |
| 收尾          | dispose 当前 AgentHandle；可能另有新 activation 使用同一 child Session | dispose 当前 external run                      |
| 代表实现      | in-process spawn、fork                                                 | 外部 Provider，具体 transport 依 provider 而定 |

“External execution 不可续接”在这里精确指 SubagentRuntime 的通用本地 follow-up API 不接受 external Activation；不能据此断言某个
Provider 自己的远端 产品完全没有续接能力。实现位置：
[Activation](deepseek-harness/packages/subagent/subagent/src/activation.ts#L38-L122)、
[startLocal/startExternal](deepseek-harness/packages/subagent/subagent/src/manager.ts#L379-L520)。

### Session 身份可保留，Activation 不等于永久 Agent

本地 child 可在一个 activation 结束后释放驻留资源，而 child Session 和描述它如何 恢复的 descriptor 保留在持久层。之后，直接
parent 发来 follow-up 时，Manager 可 查询 Session、验证父子 lineage 及
descriptor，再 materialize 一个新的本地 Agent。 于是“续接”表示复用 child 的持久身份和历史，不表示同一个内存 Agent 对象一直活着。

    持久身份：child-17 Session + continuable descriptor
    activation #1：物化 AgentHandle -> settle / dispose
    后续 parent message：读取 descriptor + Session
      -> activation #2：同一 child id 的新 AgentHandle -> 接受消息并继续

冷恢复路径要求可查 Session、descriptor 可续接，并且 session persistence/query 服务已装配。普通调用若选
delivery=caller，可使用临时本地 Session；不能把 “本地 Provider”一概说成“必须启用持久化”。本地 parent
delivery 需要持久化； 冷恢复需要持久化与 Session 查询服务。Team 的 roster/task 要 durable，也依赖
持久化和日志查询。因此要把三件事分开：本地执行能力、parent-delivery 的记录要求、 冷恢复的服务要求。 源码锚点：Manager 冷恢复见
[coldResume](deepseek-harness/packages/subagent/subagent/src/manager.ts#L1325-L1384)；
持久化与查询服务要求见 [requirePersistence /
requireSessionQuery](deepseek-harness/packages/subagent/subagent/src/manager.ts#L1458-L1479)； parent/caller 与临时本地
Session 约束见 [subagent
README](deepseek-harness/packages/subagent/subagent/README.zh.md#L59-L62)； Team member/task 持久事件见 [Team
README](deepseek-harness/packages/experimental/agent-team/README.zh.md#L142-L148)。

### 取消与 dispose 的语义边界

取消一轮和释放一次 activation 是不同动作。Agent cancel 作用于当前正在执行的 Turn；SubagentRuntime dispose 则针对 receipt
中的精确 activation 释放资源。Team 的 interrupt 明确采用 keepInbox：取消
live teammate 当前 Turn，但留下已接受 inbox 输入供后续运行处理。它不删除成员、不释放 task owner，也不保证某个业务任务已经
回滚。

**教学推演，非真实运行结果：**

    Lead: "Member A，先检查 schema，再改文件。"
    Member A inbox: [该消息已 accepted]
    Lead interrupt_agent("Member A")
      -> 若 A 正在运行，取消当前 Turn，保留 pending inbox
      -> 若 A 已 idle，返回此前状态；没有正在运行的 Turn 可取消
    之后：Lead 仍需检查任务 owner / task status，必要时显式 release 或 reassign

普通 Manager 的完整 teardown 还可能处置该 Agent 所拥有的子执行并等待 idle、 flush 最终状态、捕获 stop
reason。这里不把这些内部收尾细节泛化为 Team interrupt 一定会 dispose 整个 member；Team API 走的是
keepInbox 的当前轮取消路径。 源码锚点：cancel keepInbox
合同见 [runtime-types.ts](deepseek-harness/packages/core/agent/src/runtime-types.ts#L37-L45)； Team interrupt
授权与调用见 [TeamRoster.interrupt](deepseek-harness/packages/experimental/agent-team/src/roster.ts#L181-L198)； Member
task owner 不随 interrupt 自动释放见 [Team
README](deepseek-harness/packages/experimental/agent-team/README.zh.md#L138-L140)、
[L203-L207](deepseek-harness/packages/experimental/agent-team/README.zh.md#L203-L207)。

## 5. Agent Team：在子会话上增加 roster、journal 与 task board

### 为什么不把 Team 等同于“多个 subagent”

普通 subagent 提供父子关系和执行生命周期；它本身没有一支具名、共享身份的成员表， 也没有 Team-local task graph。Team 将 Lead
的 root Session id 作为 Team 身份，再 把符合条件的直接 continuable child 登记为队员。它在
Lead Session 中追加成员和任务 事件，并从事件投影出当前 Team 视图。

| 对象           | 权威事实放在哪里                      | 负责的边界                                                       |
|----------------|---------------------------------------|------------------------------------------------------------------|
| Team 身份      | root / Lead Session id                | 一个 root Agent 对应 Team scope                                  |
| Team roster    | Lead Session 的 team/member 事件      | immutable name、child id、provider/context 与 provisioning phase |
| teammate 对话  | teammate 自己的 child Session         | prompt、peer inbox、模型对话、续接 descriptor                    |
| task board     | Lead Session 的 team/task 事件        | task revision、status、owner、blockers、writeScopes              |
| 当前 Team 视图 | 从 Lead Session 事件派生的 projection | 查询和广播用的 snapshot，不是第二份权威写库                      |
| wait activity  | 当前进程内活动通知                    | 等待某个后续变化；不等同持久消息队列                             |

因此 Team Journal 不是另一个业务数据库：它把 Team 事实追加到 Lead Session 并 flush，projection 从事实重建；child Session
继续保存 child 自己的执行与对话。 Team 事件不进入模型会话表面，避免 roster/task
元数据被误当成自然语言对话。
源码锚点： [TeamId / member / task types](deepseek-harness/packages/experimental/agent-team/src/types.ts#L8-L98)；
[TeamJournal](deepseek-harness/packages/experimental/agent-team/src/journal.ts#L11-L72)；
[projection](deepseek-harness/packages/experimental/agent-team/src/projection.ts#L126-L138)； [Session event log
contract](deepseek-harness/packages/core/session/src/index.ts#L1-L5)。

### 谁能做什么：一个 Lead 与两名队员

- Team tool 在 Agent 的准确 scope 内注入 Team identity 和模型可见操作。当前 Agent 必须能被 Team roster 解析为 Lead
  或成员，不能只凭同一个进程中碰巧存在一个 child id 就获得队员身份。
- 只有 Lead 可以 spawn teammate、reassign 任务和 interrupt teammate。创建任务与 消息操作先通过精确 live membership 检查；task
  mutation 进一步按 owner / Lead 权限和状态迁移决定是否接受。
- 普通直接 subagent 不会因为存在而自动加入 Team。Team 只识别登记过的直接 child； nested child 不变成 Team member。新建 root
  Agent 则有自己的 root scope。

| 示例身份       | spawn / reassign / interrupt |           发 Team message | 创建 task | claim 自己可领的 ready task |         修改/完成已领 task |
|----------------|-----------------------------:|--------------------------:|----------:|----------------------------:|---------------------------:|
| Lead           |                         可以 |                      可以 |      可以 |                        可以 |    可以；Lead 也可代为修改 |
| teammate A     |                       不可以 |                      可以 |      可以 |                        可以 | 仅 owner task；Lead 可代办 |
| teammate B     |                       不可以 |                      可以 |      可以 |                        可以 | 仅 owner task；Lead 可代办 |
| 未登记的 child |           无 Team membership | 不可作为 Team member 路由 |      不可 |                        不可 |                       不可 |

权限来源不是一段 system prompt，而是 Team service/roster 对 live Agent 的身份检查 和 task board 的 mutation
授权。工具提示有助于模型按流程工作，但并不替代这些 代码边界。源码： [TeamRoster.membership /
member resolution](deepseek-harness/packages/experimental/agent-team/src/roster.ts#L76-L117)； [task mutation
authorization](deepseek-harness/packages/experimental/agent-team/src/task-board.ts#L98-L165)； [scoped Team
tools](deepseek-harness/packages/experimental/tool-agent-team/src/index.ts#L167-L190)。

## 6. Provisioning、Active、Failed 与 Running、Inactive 是两套状态

### Member phase 记录“创建是否落稳”

spawn teammate 不是先建 child 再随意补一行名字。Lead 调 Team 工具后，TeamRoster 先把带保留 child id 的 member provisioning
checkpoint 写入 Lead Session；随后 SubagentRuntime 用这个
child id 建本地 continuable Agent，并接纳初始 prompt。Team 确认 child prompt 的接受事实经过 session flush 后，才把 member
标成 active。 创建阶段失败则把 member 记为 failed。

    Lead Session: append + flush provisioning checkpoint
       -> SubagentRuntime: create child + accept initial inbox
       -> child Session: flush accepted-prompt evidence
       -> Lead Session: append + flush active; creation error -> failed

这里有两个 Session 和多个 flush 边界。源码没有把 Lead roster event 与 child inbox 写成一个跨 Session
原子事务，所以应将它读作可恢复的顺序协议，而不是“两个存储 一起原子提交”。在启动/恢复时，Team 会检查持久 child 的
parent/descriptor 和已接收 初始 prompt，来决定 provisioning 项是否可变为 active 或 failed。报告不推断所有
进程崩溃点都能无损恢复，只说明代码显式实现的 checkpoint reconciliation。

### Runtime status 记录“当前是否有 Turn 在跑”

Team member phase 与 Agent availability 是正交的：

| 维度                 | 值例                           | 回答的问题                                                 |
|----------------------|--------------------------------|------------------------------------------------------------|
| 持久 member phase    | provisioning / active / failed | roster 中创建生命周期走到哪一步                            |
| 运行时 availability  | running / inactive             | 当前是否有 Agent Turn 正在执行                             |
| Agent runtime status | running / idle                 | Agent 当前调度状态，Team 对外视图可把 idle 表达成 inactive |

active 不等于 running。一个 active teammate 可以完成当前 Turn 后变为 inactive， 仍留在 roster 中并等待下一条 inbox
消息。failed 在这里表示 provisioning 创建失败， 不能仅凭 phase
名称推断成员任何一次后续任务失败都会变成 failed。
源码锚点： [spawnAdmitted](deepseek-harness/packages/experimental/agent-team/src/roster.ts#L228-L319)； [checkpointInbox /
provisioning recovery](deepseek-harness/packages/experimental/agent-team/src/roster.ts#L322-L416)； [member view
status](deepseek-harness/packages/experimental/agent-team/src/roster.ts#L126-L150)； [runtime
status](deepseek-harness/packages/core/agent/src/runtime-types.ts#L102-L109)。

**教学推演，非真实运行结果：**Lead 等待 spawn 工具返回后看到 phase=active、 status=inactive，这不自相矛盾。它只说明 teammate
已创建且初始 prompt 被接受， 当前没有模型 Turn 正在跑；不表示其工作已完成。

## 7. Shared Task DAG：元数据协调，不是自动派发器

### 例子：两条研究任务汇成一个综合任务

假设 Team 要比较两个 API：

    A / B：两名 teammate 并行调查 Provider 1 / Provider 2
    C：Lead 综合结果，blockedBy = [A, B]

A 和 B 没有依赖，可以并行认领；C 在两项都 completed 前不是 ready。task board 只 记录共享意图、owner、依赖和状态。它不会因为
A/B owner 有值就自动启动 Agent，也 不会因为 A、B 文本看起来完成就替模型检查产物。Lead
或成员仍须通过消息、提示和 模型行为让工作发生，再显式更新 task 状态。

**教学推演，非真实运行结果：**下面的字段表达源码合同，省略工具返回 JSON。

    A = create_task(subject="研究 Provider 1", description="查合同与实现")
    B = create_task(subject="研究 Provider 2", description="查合同与实现")
    C = create_task(subject="汇总比较", description="整理差异", blocked_by=[A.id, B.id])
    A2 = update(A.id, expected_revision=A.revision, action="claim")
    B2 = update(B.id, expected_revision=B.revision, action="claim")
    # 模型各自做研究；TaskBoard 不执行任务。
    A3 = update(A.id, expected_revision=A2.revision, action="complete")
    B3 = update(B.id, expected_revision=B2.revision, action="complete")
    C2 = update(C.id, expected_revision=C.revision, action="claim")

### 状态迁移与 CAS 的真实约束

- 创建 task 得到初始 revision 和 pending 状态。Lead/member 都可创建；创建动作把完整 task snapshot 追加到 Lead Session 并
  flush。
- claim 只接受 pending 且所有 blocker 已 completed 的任务。成功后进入 in_progress， owner 变为当前 caller。
- complete 只接受 in_progress。当前 owner 或 Lead 可以完成；不要求 owner 才能 complete，因此 Lead 可在确认交付后代为收尾。
- release 将 owner 负责的 in_progress task 退回 pending；reassign 由 Lead 执行。 task owner 不会因成员
  idle、interrupt、进程退出或失败被自动清空。
- 每次更新都带 expectedRevision。task board 在 journal 内比对当前 revision， 成功转换为新 revision 并追加
  snapshot；版本过期则拒绝，而不是把旧更新静默覆盖。
- blockedBy 只描述依赖图。图校验检查缺失/删除引用、重复、自依赖和 cycle； readiness 要求每个 blocker 当前 status 是
  completed。

版本冲突的意义：它保护共享 task snapshot 不被并发的陈旧写入覆盖；它不锁住实际 文件，也不表示某个 Agent 正在执行对应研究。研究者遇到
revision conflict 应先重读 task view，再基于新 revision 作出新的合法动作。

### Write scopes 是提醒，不是锁或 ACL

writeScopes 是 task 对预期修改路径的声明。task view 会比较它与其他 in_progress task 的路径前缀重叠并给出 warning；代码没有据此阻止
claim，也没有 用它授予或剥夺文件权限。

**教学推演：**

    A: in_progress, writeScopes=["src/api"]
    B: in_progress, writeScopes=["src/api/client.ts"]
    B view -> warning: write scopes overlap with A

这个告警用于让 Lead/team 调整分工、依赖关系或 review 计划。它不是文件锁、工作树隔离、 Sandbox 策略，也不能拦截
shell、格式化器或代码生成器写同一路径。Team 包当前使用 共享工作目录/checkout，没有独立 cwd、跨进程 Team
协调、worktree、merge 或文件锁。
源码锚点： [TeamTaskBoard.create/update](deepseek-harness/packages/experimental/agent-team/src/task-board.ts#L27-L69)、 [CAS
与状态转换](deepseek-harness/packages/experimental/agent-team/src/task-board.ts#L98-L209)； [taskReady / write
overlap](deepseek-harness/packages/experimental/agent-team/src/task-view.ts#L18-L61)； [task graph
validation](deepseek-harness/packages/experimental/agent-team/src/task-graph.ts#L20-L69)； [README
限制](deepseek-harness/packages/experimental/agent-team/README.zh.md#L203-L207)。

## 8. Team peer message：当前是 inbox Steer，不是 durable Team mailbox

### 发送动作的路由

Team send_message 先验证 exact live caller 的 roster membership 和目标名称。消息内容 附带发送者名字及 agent-message
source。发给 Lead 时直接走目标 Agent inbox； 发给 teammate 时，因为
subagent API 约束 sender 与 child 的直接父子邻接，Team 用 Lead host authority 路由到目标，同时保留真实 teammate 作为消息显示来源。

    teammate A -> TeamService validates membership -> Lead-authorized inbox route to B
    Lead        -> TeamService routes to B; message source still identifies actual sender

成功返回 messageId 的含义只是目标 Agent inbox 接受了该消息，并服从目标 Agent 普通 persistence policy。Team 不会为新消息追加
queued/delivered 事件，也不会对这个 发送单独做一个 Team-level synchronous
flush。成功不代表模型已收到上下文、处理完毕 或产出结果。

README 对当前实现另作了明确限制：同一 target 没有 Team 层额外顺序保证；Team 不 保留重试意图，也不负责去重。不能把一次成功发送当成可恢复消息队列中的
durable ack，也不能把旧 mailbox 投影读到的 queued 记录当作会自动送达的新命令。

### running、idle、unloaded 时的目标行为

| 目标状态                                   | 当前实现的主要路径                                                            | 发送成功证明什么                                |
|--------------------------------------------|-------------------------------------------------------------------------------|-------------------------------------------------|
| running                                    | 对本地 child 发 steer；运行中的模型在最近 step 边界消费                       | inbox 接受；不证明当前 Turn 已处理此内容        |
| idle 且 Agent 驻留                         | 将消息放入普通 inbox 并唤醒/开始新 Turn                                       | inbox 接受；不证明新 Turn 已完成                |
| inactive / 未驻留的持久 child              | 通过 child id 查询 Session 与 descriptor，冷 materialize 新 Agent，再提交消息 | 冷恢复和 inbox admission 成功；不证明模型已执行 |
| 非 roster 成员或不允许 continuation 的对象 | Team membership / SubagentRuntime 能力检查拒绝                                | 没有 accepted receipt                           |

在 Team 对外视图里，Agent idle 常表达为 inactive；inactive 表示当前没有 Turn 执行， 不表示完成、成功、失败或正在等待其他成员。Team
的标准 teammate 是可恢复的直接 本地 child；外部一次性 run 不会自动成为可被 Team inbox
路由的成员。

### 历史 mailbox 与 wait / interrupt

旧版 team/message/queued 和 team/message/delivered 事件仍可由 projection 读取， 用于解释历史快照；当前发送路径不再写入它们，也不在
restart 时重放未送达的旧 queued 记录。它们是兼容性历史数据，不是当前发送协议。

Team 的 wait_agent 订阅调用之后发生的下一次 roster、task、消息接收或实时状态变化。 若没有 running/provisioning
的其他成员能产生后续变化，工具可能直接返回 noProgress。 它只等待通知，不会启动 inactive Agent；需要
inactive teammate 继续工作时，先 send_message 唤醒/续接，再 wait_agent 观察后续变化。

interrupt_agent 只有 Lead 可调用，并只取消目标当前 Turn 且保留 pending inbox。 它不会释放 task owner；若被中断者占有
in_progress task，Lead 必须检查工作并显式 release / reassign，不能把取消当作任务回滚或
owner 清理。
源码锚点： [TeamService.sendAdmitted](deepseek-harness/packages/experimental/agent-team/src/index.ts#L223-L248)；
[SubagentRuntime.sendMessage](deepseek-harness/packages/subagent/subagent/src/index.ts#L296-L316)； [manager direct message / cold
resume](deepseek-harness/packages/subagent/subagent/src/manager.ts#L533-L574)、 L1325-L1384； [Team activity
wait](deepseek-harness/packages/experimental/agent-team/src/activity.ts#L10-L86)； [wait/interrupt
tool](deepseek-harness/packages/experimental/tool-agent-team/src/index.ts#L249-L295)； [README
消息与持久性合同](deepseek-harness/packages/experimental/agent-team/README.zh.md#L68-L76)、
[L130-L146](deepseek-harness/packages/experimental/agent-team/README.zh.md#L130-L146)。

## 9. Workflow caller 编排与 Agent Team 自主协作

workflow-ptc 启动 child 时设置 caller delivery，并把 receipt 放到自己的 child map。 workflow guest 后续用 child handle
请求结果或 dispose；workflow 的程序代码负责“先做
哪个、等哪个、如何组合输出”。subagent 服务只提供执行原语和 result Promise。

Agent Team 的模型可以自主选择 spawn_teammate、send_message、wait_agent、共享 task 操作；Team 负责验证成员身份、写 roster/task
状态和协调 inbox。它不替 Lead 写一段固定程序来决定每个模型的先后顺序。两者可以复用相同
SubagentRuntime，但 调用者、结果路径、协调事实和控制权不同。

| 比较点              | workflow-ptc                | Agent Team                               |
|---------------------|-----------------------------|------------------------------------------|
| 调用方              | 程序化 workflow host        | Lead / teammate Agent 模型               |
| delivery            | caller                      | Team roster 的 parent-child inbox 路由   |
| 控制顺序            | workflow 代码               | 模型选择工具 + Team 协作协议             |
| 结果                | workflow await child result | 子代理结束通知或 peer message，Lead 汇总 |
| 持久协作元数据      | workflow 自己的 handle/状态 | Lead Session 中的 member/task events     |
| 自动共享 task board | 无                          | 有，但仅保存/协调元数据                  |

源码锚点：[WorkflowPtcHost.startChild / childResult / disposeChild](deepseek-harness/packages/workflow/workflow-ptc/src/host.ts#L178-L255)；
caller delivery
合同见 [SubagentActivationSpec](deepseek-harness/packages/subagent/subagent/src/types.ts#L30-L58)。

## 10. 从代码语义推演三个失败/恢复案例

以下是帮助阅读边界的案例推演，不代表已运行或覆盖所有异常路径。

### 案例 A：两个成员竞争 claim 同一个任务

A 和 B 都读到 revision 1。A 先以 expectedRevision=1 claim 成功，snapshot 前进到 revision 2、owner=A。B 随后提交的旧
revision=1 请求会冲突，不会悄悄把 owner 覆盖成 B。B 需重读任务后决定领取其他
pending 任务、等 A 完成，或请 Lead reassign。

保护语义：revision CAS + transition authorization。它保护 task log 的并发更新， 不保证 A 真在运行，也不替文件系统阻止两人修改相同路径。

### 案例 B：Team spawn 在两个 Session checkpoint 之间中断

Team 已把 provisioning member 写入 Lead Session，但 child 初始化、prompt acceptance 或后续 active checkpoint
尚未都完成时，进程可能离开。启动恢复读取 roster 与 child 持久描述，检查父
lineage、continuable descriptor 和已接受 prompt，尝试收敛该 provisioning 项到 active 或 failed。

保护语义：先写意图再开始资源创建，以及恢复时的证据核对。边界：这是分阶段 reconciliation，不是 Lead Session 与 child Session
的原子事务；具体崩溃故障注入结果 未在此静态研究中验证。

### 案例 C：消息 accepted 后目标没来得及处理

Team send 返回 messageId，之后目标被 interrupt 或进程终止。对当前 Team send 而言， 不存在 queued/delivered 新事件、Team
重试记录或去重键。因此发送方应通过目标状态、 后续消息或任务结果确认协作进度，而不能仅因得到 messageId
就向用户报告任务完成。 消息目标 Session 的正常 inbox/persistence 策略仍适用；Team 层没有额外交付承诺。

保护语义：API 命名和文档显式把 success 定义为 inbox acceptance。边界：目标 Agent 何时在模型上下文消费 inbox 还受 Agent
调度与当前 turn 状态影响。

## 11. 设计取舍与阅读时的常见误读

- **执行与协调分层：**Manager 统一管理子执行；Team 在上层维护成员和 task facts。 好处是 workflow、普通模型工具与 Team
  复用同一生命周期服务；代价是读者必须区分 child result、Team message 和 task status 三种不同信号。
- **持久事实归属明确：**成员和任务归 Lead Session，child 对话归 child Session。 这是按现有源码得出的事实；跨 Session
  的事务边界仍需单独研究，不应假设为原子。
- **操作系统资源没被 task board 替代：**Team 在共享 checkout 内协作，write scope 仅给出重叠提醒。要安全拆分代码更改，需要
  Lead 真的安排不重叠范围并审查最终 diff。
- **Agent 可以在 Team 协议外继续存在：**普通 child 并不自动成为 roster member； Team membership 是显式登记的身份，不是“同一父
  Agent 的所有 child”集合。
- **等待和唤醒是两个动作：**send_message 可以接纳并唤醒目标；wait_agent 观察变化， 不会让没人运行的模型自动醒来。
- **工具提示不是授权机制：**模型可见的 Team policy 提醒它按 list/get/claim/work/ complete 操作；实际是否可操作仍由 Team
  Service、Roster 和 TaskBoard 校验。
- **消息和 task 状态不是同一事实：**“已发送”不代表“已读”；“已 claim”不代表 “已开始做”；“已 complete”是调用方提交的状态转换，不是
  Harness 对产物正确性的 自动判定。

## 12. 推荐的后续阅读顺序

按下面顺序阅读可从稳定接口一路走到失败/恢复边界；每步只需打开列出的主路径文件。

1. **先读合同。**看 [subagent.zh.md](deepseek-harness/docs/subsystems/subagent.zh.md) 和
   [agent-team.zh.md](deepseek-harness/docs/subsystems/agent-team.zh.md)，分别记下 provider 能力、delivery、Team
   identity、任务状态和当前限制。
2. **校准运行时名词。**看 Agent runtime types、Session Turn/Step event map 和 Activation union，画出
   Agent/Session/Turn/Activation 的状态归属。
3. **追普通 subagent。**从 tool-subagent execute 读到 Service dispatch，再读 Manager startLocal/startExternal、submit
   inbox、finishDisposal 和 notifySettlement。
4. **对比 spawn / fork。**读两个 provider 和 child-agent composition/policy，分别记录 transcript seed、preset、route options
   与 delegated policy。
5. **追 Team provisioning。**读 Team tools、TeamService、TeamRoster spawn/checkpoint、 TeamJournal/projection，再对照 child
   Manager 的 inbox acceptance/cold
   resume。
6. **追任务与消息。**读 TaskBoard update + task-view + task-graph，再读 sendAdmitted/activity/interrupt，重点标注
   CAS、权限和“accepted 不等于处理”。
7. **最后比较 caller。**读 WorkflowPtcHost.startChild、childResult 和 dispose，对照 Team 的自主工具循环与 parent delivery。

## 13. 测试入口：静态定位，不代表本次执行

以下测试入口用于后续人工研究保护的行为合同；本次没有运行测试。

- [service.spec.ts](deepseek-harness/packages/subagent/subagent/tests/service.spec.ts#L131-L188)： provider
  registry、start/remove 生命周期，以及外部执行不暴露
  continuation 操作。
- [tool-subagent.spec.ts](deepseek-harness/packages/subagent/tool-subagent/tests/tool-subagent.spec.ts#L64-L75)： 模型工具启动
  managed child 并返回
  id，不同步收集其最终输出。
- [continuation.spec.ts](deepseek-harness/packages/subagent/subagent/tests/continuation.spec.ts#L1034-L1090)： settled
  child 的冷恢复及等待激活；另可看 L2802-L2820
  的 sender attribution、 L3143-L3175 的 parent notification / idle wake 语义。
- [team.spec.ts](deepseek-harness/packages/experimental/agent-team/tests/team.spec.ts#L213-L255)： fresh/fork 和 spawn
  checkpoint；L606-L681 的 task
  revision、ownership、dependency 和 warning；L890-L930 的 peer message/wait；L1047-L1075 的 interrupt/wait。
- [persistence.spec.ts](deepseek-harness/packages/experimental/agent-team/tests/persistence.spec.ts#L198-L235)： recovery
  对 provisioning、child
  descriptor 与 inbox evidence 的处理； L315-L345 说明历史 queued mailbox 不被重放投递。
- [tool-team.spec.ts](deepseek-harness/packages/experimental/tool-agent-team/tests/tool-team.spec.ts#L492-L559)： tool
  JSON schema 到 Team Service
  的映射；L768-L790 核对 fresh/fork provider 配置。

## 14. 事实、研究判断与未确认边界

- **代码事实：**continuable child 通过 Harness 的 Session persistence/query 服务契约 保存与恢复；Team roster/task 以 Lead
  Session event/projector 表示。具体 Session backend
  需看目标装配，不从这几处代码指定某一种产品。
- **项目约束：**后续实验允许正常使用现有 Nacos、Mongo、Kafka 等基础设施；本轮研究 不依赖 RAG 和 Sandbox
  业务服务。这里没有把基础设施重新定义成某个 Harness Session persistence backend。
- **代码事实：**writeScopes 是路径前缀 overlap warning；**研究判断：**task board 不是文件锁、工作树隔离或权限执行器。
- **代码事实：**当前 Team 工具使用共享工作目录，且包 README 明示不支持独立 cwd、 跨进程 Team 协调或自动释放
  owner。未来若研究多进程，应重新看持久协调合同。
- **未确认：**当前具体 Loader 实际装配的 Provider、Agent preset、工具集、permission preset 与 Session persistence/query
  backend，需要回到应用入口和服务组合核实。
- **未确认：**外部 ACP/Codex/Claude Code Provider 各自 transport、context 与恢复语义 不由 in-process spawn/fork
  代表；本报告没有穷举这些后端。
- **未确认：**Team package 当前 recovery 路径之外的所有进程崩溃窗口、真实部署持久 服务故障模式和运行时竞争结果；本文只核对静态源码合同和测试入口，未做运行验证。

## 固定 SHA 代码链接

以下 GitHub permalink 固定到本文核对的克隆 SHA；本地链接保留更完整的相邻源码说明。

- [SubagentRuntime.startActivation](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/subagent/subagent/src/index.ts#L269)
- [SubagentManager.startLocal](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/subagent/subagent/src/manager.ts#L379)
- [TeamRoster.spawnAdmitted](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/experimental/agent-team/src/roster.ts#L228)
- [WorkflowPtcHost.startChild](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/workflow/workflow-ptc/src/host.ts#L200)
