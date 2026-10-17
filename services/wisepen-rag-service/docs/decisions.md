# 易误读决策与变更记录

只记录被替换或维护时容易误解的选择；完整设计见[设计文稿](design.md)。archive 内保留当时原稿，现行合同以各专题及源码为准。

## 1. 请求决定从 resolver/召回配置收敛到 application（实施记录 18–19）

- 原计划：早期资料讨论独立版本/ACL resolver 与混合检索层次，见 [04](archive/04-内容版本与请求内读取边界.md)、[18](archive/18-application-snapshot-implementation.md)。
- 变更原因：版本和权限决定属于一次请求，召回组件需要保持固定依赖与字符串查询接口。
- 现状：ResourceSnapshot + ResourceSnapshotReader；application 每次调用 from_storage 构造 Retriever，见[授权](usecases-access.md)和[抽象](abstractions.md)。
- 注意：不要恢复 retriever_factory、VectorRetrievalQuery、隐式 context 或跨请求缓存。快照缓存的是决定，不是正文或跨库原子时间点。

## 2. Chunk 身份与章节定位分开（实施记录 20）

- 原计划：早期结构使用 Common 章节 ID，见 [20](archive/20-structured-reading-implementation.md)。
- 变更原因：只传章节 ID 的读取需要区分资源与 revision，防止跨版本定位。
- 现状：Document/Chunk/命中共用资源限定的 rag_section_id，Chunk.chunk_id 保持原规则，见[模型](models.md)。
- 注意：两个 ID 看起来都与分块有关，但职责不同；不解析 ID，也没有旧章节 ID 自动迁移。

## 3. 图拓扑与图向量由同一 Indexer 维护（实施记录 23–24）

- 原计划：历史方案有独立图向量阶段和旧结果对象，见 [09](archive/09-VectorStorage%20与%20GraphStorage%20最小存储合同.md)、[24](archive/24-graph-vector-retriever-implementation.md)。
- 变更原因：两种投影消费同一来源事实，需要共用解析和来源身份。
- 现状：GraphIndexer 先拓扑再实体/关系来源向量，图搜索结果为 GraphRetrievalResult，显式探索结果为 GraphTraversalResult，见[抽象](abstractions.md)及[图用例](usecases-graph.md)。
- 注意：没有 GraphVectorIndexer/graph_vector.py 或 Receipt；成功缓存不证明投影已写成功。旧名在 archive 出现不表示要恢复接口。

## 4. 本体约束写入，图读取不绑定本体（实施记录 25、30）

- 原计划：早期图查询资料按单一本体组织，见 [06](archive/06-Chunk%20模型与%20LightRAG%20图谱查询合同.md)、[25](archive/25-graph-application-implementation.md)。
- 变更原因：通用查询要允许共存本体并从当前来源事实验证证据，而不是用查询侧本体配置筛掉历史事实。
- 现状：写入绑定 Ontology，查询使用通用事实解析与三个来源/本体筛选字段，见 [30](archive/30-graph-ontology-filter-implementation.md)和[图用例](usecases-graph.md)。
- 注意：GraphNode.relations 只是允许关系提示；应用 ChunkHit.seed_entities.relations 则按全图当前授权事实确认，未启用图时为 null。旧拓扑缺 ontology_name、旧向量缺 fact_sha256 需要显式投影维护。

## 5. 向量初始化从后端配置收敛为显式声明（2026-10-13）

- 原计划：早期集合初始化使用后端专有 metadata_indexes，见 [29](archive/29-vector-index-contract-implementation.md)。
- 变更原因：平台需要通过统一协议初始化默认/扩展集合，业务包无需了解数据库 SDK 索引类型。
- 现状：VectorCollection + IndexableField/IndexKind，ensure_collection 必填声明列表，见[模型](models.md)和[扩展](extensions.md)。
- 注意：payload 有字段不等于必须建字段索引；删掉声明不会自动物理删除已有索引，无无参/字符串列表兼容接口。

## 6. Employment 从机构示例改为公司内部部门合同（2026-10-13）

- 原计划：初版 employer/包装层及专用结果/prepare，见 [28](archive/28-employment-extension-implementation.md)、[29](archive/29-vector-index-contract-implementation.md)。
- 变更原因：用户确认以公司内部合同展示扩展，并要求工具只映射参数、复用通用检索。
- 现状：平面 employee/start_date 原样保存；Person/Department/in_department 来自正文；factory 仅返回 employee/year，见 [31](archive/31-employment-department-metadata-implementation.md)和[Employment](employment.md)。
- 注意：不恢复员工名称大小写/内部空白规范化；不回写 DTO model_dump；content_revision 必须留 payload，但专用集合只声明 resource_id/year/employee 索引。

## 7. 图失败合同取决于入口（实施记录 22–27）

- 原计划：早期普通发布后独立构建图的表述容易被泛化成“图失败永不影响发布”，见 [14](archive/14-publication-implementation.md)、[22](archive/22-graph-extraction-implementation.md)。
- 变更原因：type 路由选择的是完整必需链；离线维护和新版本发布的目标不同。
- 现状：注册链任何必需图阶段失败都阻止 apply；独立 repair 失败保留已有 applied 和已完成写入，见[发布](usecases-publication.md)和[运维](operations.md)。
- 注意：没有跨库补偿，不在 Pipeline 吞错误；Contextualizer 自身的逐块降级不能推广到所有 Transformer。

## 8. 默认双 Pipeline 与扩展显式选链（2026-10-13）

- 原计划：默认链按 ENABLE_CONTEXTUALIZE 选择，旧 Employment 说明沿用“可选平台上下文”，见[归档扩展指南](archive/extensions.md)。
- 变更原因：当前 Employment 代码明确避免额外上下文模型调用，并允许垂类显式选带/不带上下文链。
- 现状：平台保留两条共享默认链，Selector 惰性二选一；Employment 固定使用 pipeline_without_context，见[扩展](extensions.md)。
- 注意：平台开关不会覆盖扩展的显式选择；未选中的默认上下文链不解析 instruction 客户端，但 Employment 图抽取仍需要指令配置。
