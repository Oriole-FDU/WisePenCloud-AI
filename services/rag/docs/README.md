# RAG 文档站

RAG V4 将完整 Markdown 发布为可检索的当前内容事实，通过请求级版本与权限决定返回正文和图证据，并允许业务扩展复用同一平台生命周期。

本目录是现行说明与接手入口。文档依据当前 services/rag 源码及测试整合，初始核对基线为 `af2dd565`（2026-10-13）；后续维护以实际实现为准。旧计划、demo、接手与实施记录原样放在 [archive](archive/README.md)，不能用历史接口推断现状。

## 推荐阅读顺序

1. [中立数据模型](models.md)：正文、revision、章节、图身份与来源术语。
2. [核心抽象](abstractions.md)：五类组件、Pipeline、metadata 增量与存储合同。
3. [实际架构](architecture.md)：发布、查询与运行装配关系。
4. 按需要阅读下面的用例、扩展或运维说明。
5. [设计文稿](design.md)：理解为什么这样划分职责与接受哪些取舍；[决策记录](decisions.md)解释容易误读的历史变化。

## 文档导航

| 读者/任务 | 文档 |
|---|---|
| 接手整体系统 | [设计](design.md)、[架构](architecture.md)、[模型](models.md)、[抽象](abstractions.md) |
| 内容摄入及版本状态 | [发布、消费与永久销毁](usecases-publication.md) |
| 可信身份、ACL 和内容读取 | [授权与请求快照](usecases-access.md) |
| 正文候选和命中 | [向量检索](usecases-vector.md) |
| 目录与完整章节原文 | [结构化阅读](usecases-reading.md) |
| 图种子、证据与一跳探索 | [图用例](usecases-graph.md) |
| 永久销毁后的物理清理 | [GC 用例](usecases-gc.md) |
| 业务开发与专用索引/API | [扩展指南](extensions.md)、[Employment 示例](employment.md) |
| 启动、配置及常见问题 | [运行说明](deployment.md)、[Nacos 配置样例](platform-settings.yaml) |
| 明确资源范围的离线修复 | [两脚本运维手册](operations.md) |
| 历史变化与项目脉络 | [关键决策](decisions.md)、[一页回顾](retrospective.md)、[归档](archive/README.md) |

## 当前能力与注意事项

普通发布、三模式正文检索、授权回查与排序、目录/邻域/own/subtree 阅读、上下文增强及离线 repair 已实现。图事实、本体约束、多来源拓扑/向量、三模式图检索和显式一跳 traversal 已实现。平台装配、共享客户端、消费者握手、定时 GC、type 路由和完整 Employment 示例已实现。

正文权威在 Mongo，向量和拓扑是投影。快照归 application，Retriever 只接收 query。metadata_factory 提供业务增量，资源/revision 身份不能覆盖。图 metadata filter 仅有 resource_id/content_revision/ontology_name 三个字段，支持字符串 EQ/IN 与组合；与普通向量条件能力不同。

两条默认 Pipeline 带/不带上下文，由平台开关惰性选择；Employment 明确使用不带上下文链。GC 和平台重排始终装配，Kafka 可关闭。在线发布完整必需阶段失败阻止 apply，离线 repair 的部分成功保留；这两个入口的失败语义不同。

## 验证与后续工作

定向测试及隔离后端验证方法见[运行说明](deployment.md)。单元/替身通过不代表业务环境、真实模型或检索效果已验收；历史测试数字只解释对应切片。

真实后端/模型联调、效果基线、父块、在线重构建、活跃资源历史贡献回收和通用 HTTP/MCP transport 是后续独立工作；mix 待定，确定性 Producer 不排期。已挂载的 Employment 只读 API 与尚未实现的通用 transport 分开说明。

维护本目录时先核对源码/测试，再更新对应专题；新增能力同时更新导航和架构。模型术语在 models.md，抽象合同在 abstractions.md，设计文稿只保留决策与取舍，不继续累积进度记录。
