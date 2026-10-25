# 多智能体实现研究：Codex 与 DeepSeek Harness

这里是 `experiment_multi_agent` 的研究入口。报告直接解释子代理、协作消息、运行生命周期与 agent team
的实现思路，并比较两套系统。读者无需先打开源码；路径、符号、测试和固定提交链接作为进一步核查的证据保留。

本次保留现有 Chat/MCP 搜索能力作为未来实验基础，没有提前实现新的多智能体运行时。文件改动保留在工作区，不创建 commit。

Nacos、Mongo、Kafka、Redis 等基础设施正常使用。实验默认不启动 RAG/Sandbox 业务服务，也不发现 Sandbox 工具。

## 文件导航

| 文件                                                                             | 阅读目的                                                                            |
|----------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|
| [01-experiment-startup.md](01-experiment-startup.md)                             | 了解当前分支实际改动、启动命令、搜索入口和验证范围                                  |
| [05-architecture-comparison.md](05-architecture-comparison.md)                   | 建立共同执行模型，比较身份、上下文、消息、任务、并发和持久性；建议先读              |
| [06-search-collaboration-walkthrough.md](06-search-collaboration-walkthrough.md) | 跟随同一个搜索案例，理解两套系统的创建、补查、等待、认领、综合和失败恢复            |
| [02-codex-multi-agent.md](02-codex-multi-agent.md)                               | 独立讲解 Codex 的上下文、V1/V2、线程控制、mailbox、并发和恢复，并附源码地图         |
| [03-deepseek-multi-agent.md](03-deepseek-multi-agent.md)                         | 独立讲解 subagent、Provider/Manager、Team 成员与任务、消息和 workflow，并附源码地图 |
| [04-research-path.md](04-research-path.md)                                       | 理解正文后，逐项核验源码、继续深入和规划真实搜索实验                                |
| [sources.json](sources.json)                                                     | 精确记录两个克隆的来源、分支和提交，避免后续更新源码后错用行号                      |

## 建议阅读方式

先读 **05 → 06**，获得共同概念和具体事件顺序；再读 **02 → 03**，了解每套实现内部的对象与协议。最后用 **04**
规划自己翻源码的顺序。若要启动本项目服务，单独查阅 **01**。

正文区分三类内容：已核对的源码事实、用于教学的简化图/伪代码/虚构案例，以及尚未实测的设计推断。教学推演不代表已经完成真实多代理搜索；静态代码证据也不替代上游运行验证。

读完核心报告，应当可以直接解释：

- 为什么会话身份、运行时是否装载、一次模型轮次要分开管理。
- fresh/fork 选择历史起点，而工具、权限和共享工作区属于另外的配置与授权问题。
- Codex V2 的消息排队和启动后续轮次为什么采用不同动作。
- DeepSeek 的 Provider、activation、parent/caller 与 Team 任务板分别解决什么问题。
- 任务认领、消息接收、内容持久化、模型可见和业务完成为何是不同事实。
- 两套系统各自适合提供哪些机制，哪些质量或性能结论仍需控制变量实验。

## 克隆快照

调研日期：2026-10-22。两个仓库均完成 `git clone --depth 1`，包含完整工作树；历史只有浅克隆范围，本次未安装依赖或运行上游仓库测试。

| 仓库                                                                | 本地目录            | 固定提交                                   |
|---------------------------------------------------------------------|---------------------|--------------------------------------------|
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | `deepseek-harness/` | `687f282e8625c6b8d11ff58211a90524be2e4317` |
| [Codex](https://github.com/openai/codex)                            | `codex/`            | `b337fe06b7d8d6e5b42295e04c5e92c2b9c46567` |

这两个目录作为独立 Git 仓库保留，并被主仓库 `.gitignore` 排除。主仓库只记录本目录的研究文档和快照清单，不创建嵌套仓库
gitlink。

在新的工作副本中，可从项目根目录重新克隆；Windows 为 Codex 及其他较长快照路径启用本次克隆级的 longpaths：

```powershell
git -c core.longpaths=true clone --depth 1 https://github.com/deepseek-ai/deepseek-harness.git references/multi-agent/deepseek-harness
git -c core.longpaths=true clone --depth 1 https://github.com/openai/codex.git references/multi-agent/codex
```

默认分支之后可能推进。先核对 `sources.json`；若要恢复这里的精确快照，必要时 `git fetch origin <commit>`
后再检出对应提交。已存在克隆时直接使用，不重复执行 clone。

## 术语与阅读边界

- **sub-agent**：父代理委派任务给具有独立执行上下文的子代理。具体是否继承历史、是否可继续对话，由各自实现决定。
- **agent team**：需要从实际 Team 身份、成员生命周期、共享任务和消息协议确认，不能只因有多个并发子代理就认定存在完整团队机制。
- **workflow**：预先编排的阶段/调用，与代理自行分工可能共用底层执行能力，但不是同一个控制层。

两份源码地图中值得先比较的事实：

| 机制     | Codex 本地 V2                                                                 | DeepSeek Harness                                                         |
|----------|-------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| 委派入口 | `spawn_agent` → `thread_spawn` → `AgentControl`                               | 模型工具 → `SubagentRuntime` → Provider / Manager                        |
| 上下文   | `fork_turns` 区分新上下文与完整历史 fork                                      | in-process spawn/fork Provider 提供不同 transcript seed                  |
| 后续消息 | `send_message` 只排队；`followup_task` 可启动目标轮次                         | 本地 continuation / Team 消息尝试 Steer，闲置目标可被唤醒                |
| 团队任务 | 所读本地主链没有与 Team 共享任务板等价的领域对象；可选 Message Board 用于讨论 | 实验性 Team 在 Lead Session 上维护 roster 与具备 revision/CAS 的任务 DAG |

这张表是阅读入口，完整说明见架构对比、搜索案例和两份实现专篇；不能由同名工具推断等价行为。

文档中的路径、符号与行号以本次克隆为准。少量源码永久链接用于共享定位；本地文件链接用于继续研究。官方 [Codex 子代理说明](https://learn.chatgpt.com/docs/agent-configuration/subagents)
可作产品层补充，本地源码是这里实现判断的主要依据；托管 API 的 multi-agent 能力不等同于本地 Codex 的工具执行路径。
