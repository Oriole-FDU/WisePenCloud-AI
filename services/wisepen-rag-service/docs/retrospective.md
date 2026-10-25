# 项目回顾

RAG V4 已完成中立事实模型、五类抽象、核心应用用例、运行装配及 Employment
扩展示例。这里归纳实施脉络，不替代[现行文档](README.md)或原始验收记录。

## 阶段与转折

| 阶段           | 形成的能力与关键变化                                                             | 原始记录                                                                                                                                             |
|----------------|----------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| 设计与基础     | 划定内容/投影、权限/发布边界；完成 schema、摄入和 Mongo 内容存储                 | [00](archive/00-rag-v4-restructure-plan.md)、[12](archive/12-schema-ingestion-implementation.md)、[13](archive/13-content-storage-implementation.md) |
| 发布与正文读取 | 条件发布、Kafka 重试、向量闭环、请求快照、正文检索与结构化阅读                   | [14](archive/14-publication-implementation.md)–[20](archive/20-structured-reading-implementation.md)                                                 |
| 上下文与图事实 | 增量上下文、图抽取、本体约束、离线修复及多来源拓扑/向量                          | [21](archive/21-contextualizer-implementation.md)–[24](archive/24-graph-vector-retriever-implementation.md)                                          |
| 图用例与平台   | 授权证据、一跳探索、共享客户端、消费者握手、永久销毁 GC、type 路由               | [25](archive/25-graph-application-implementation.md)–[27](archive/27-document-type-routing-implementation.md)                                        |
| 扩展收敛       | 完整业务示例、统一索引声明、通用结果与本体条件、内部部门合同及 metadata 增量合同 | [28](archive/28-employment-extension-implementation.md)–[31](archive/31-employment-department-metadata-implementation.md)                            |
| 本轮资料整合   | 先统一模型/抽象术语，再形成架构、用例与运维，最后收束设计和入口；旧资料原样归档  | [文档入口](README.md)、[归档说明](archive/README.md)                                                                                                 |

2026-10-12 至 10-13 的实施记录逐片保留当时测试范围与结果；当前双默认链由 af2dd565 落地。历史接手文件开头仍引用更早基线，不能据此判断当前代码缺少能力。

## 维护经验

事实、版本决定与投影身份分开，才能避免把旧候选和未授权材料送进响应或模型。来源缓存和抽取指纹只证明生成结果可复用，投影成功必须由实际写入确认。

内部组合保持简单，错误和恢复由真实入口承担：发布要求完整链成功，离线维护接受资源级部分成功，查询允许过滤后的部分结果。把这些不同失败合同统一包装反而容易误导。

扩展示例帮助发现通用合同缺口：索引声明需要后端中立，metadata_factory 应提供业务增量，业务工具应复用通用授权准备。文档也需要单一现行入口，避免后续修正淹没在进度追加中。

## 验证与后续边界

原始测试数字保留在对应记录，不在此累计成新验收结果。替身测试覆盖模型、装配、状态与失败合同；真实
Mongo/Qdrant/Neo4j/Kafka、模型联调和效果基线仍需独立验收。

本轮资料整合验证（2026-10-13）：文档示例、Container、Employment、lifespan 和两脚本定向测试合计 **131 passed**；现行本地链接、Python
示例编译、两个脚本 --help、修改测试文件 Ruff check/format 检查通过。42 份原稿移动前后 SHA-256 一致。未启动业务入口、连接业务库、调用真实模型或执行
GC/repair；本轮没有重跑全服务业务回归。

父块、在线重构建、活跃资源历史贡献回收与通用 HTTP/MCP transport 留后；mix 待定，确定性 Producer 不排期。GC 仅回收永久
destroyed 资源，离线 repair 也不替代这些后续能力。
