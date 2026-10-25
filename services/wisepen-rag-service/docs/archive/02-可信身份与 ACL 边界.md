# RAG V4 子方案 01：可信身份与 ACL 边界

## 0. 子方案目的

本方案冻结 RAG 查询能力的身份和资源授权边界。它回答三个问题：

1. MCP 请求中的最终用户身份从哪里来；
2. RAG application 接收什么授权输入；
3. 向量预过滤、Mongo 回查和最终返回如何共同执行 ACL。

本方案不预设 active revision 与 ACL 必须达到全局强一致，也不设计图谱节点的合并边界。active revision、ACL
投影延迟和查询容错策略由后续一致性子方案确定。

## 1. 已确认决策

### 1.1 不引入 tenant/workspace

平台保证资源 ID、用户 ID 和群组 ID 全局唯一。RAG 不新增 tenant/workspace 字段，不把 tenant 加入 Mongo 主键、Qdrant
payload、Neo4j predicate、图谱逻辑 ID 或删除 selector。

全局唯一性是平台合同，不是 RAG 查询时的推断。需要通过 ID 合同测试和 repository selector 测试防止未来实现自行增加隐含命名空间。

### 1.2 身份只从可信安全上下文获取

Chat 的 MCP client 从 `SecurityContextHolder` 读取用户、身份类型、用户状态和群组角色，通过受保护请求头发送。接收端
`SecurityHeaderMiddleware` 校验 `X-From-Source` 后，将这些字段写回本地 `SecurityContextHolder`，请求结束时清理。

RAG 工具参数不得包含 `user_id`、群组角色、ACL 条件或可替换的调用方身份。MCP `tool_context` 只表示会话和工具运行上下文，例如
`session_id`、技能白名单或临时附件引用；它不是授权来源。

### 1.3 MCP 适配层向 RAG application 提供 `PermissionScope`

MCP 适配层负责读取可信安全上下文，并构造 RAG 已有语义的 `PermissionScope`：

```python
PermissionScope(
    user_id=<SecurityContextHolder.get_user_id()>,
    group_roles=<SecurityContextHolder.get_group_role_map()>,
)
```

RAG application 的查询、读取和图谱入口只接收这个 typed scope。application 和 repository 不解析 HTTP header、MCP metadata
或工具参数中的身份字段。

`identity_type` 和 `user_status` 继续由 Common 传递，但只有在 RAG 出现真实业务规则时才加入 `PermissionScope`；当前资源 ACL
只需要用户 ID 和群组角色，不为未来可能的规则预留字段。

## 2. ACL 权威和投影

上游 Resource ACL 是权威事实。RAG 基于现有 ACL 事件保存 `ResourceAcl` 本地投影，用于候选资源确定后的批量回查和请求级 ACL
判权。现有事件只表示刷新触发，不证明上游重算已经完成；`acl_revision` 不作为既定的上游事实，本地投影不得通过 `updateTime`
或自定义时间戳推断上游权限顺序。

各层职责固定如下：

| 层                    | 责任                                                                   | 是否最终授权     |
|-----------------------|------------------------------------------------------------------------|------------------|
| 上游 Resource Service | 产生资源 ACL，并提供重算完成和稳定读取合同                             | 是，事实权威     |
| RAG `ResourceAcl`     | 保存现有 ACL 事件触发读取出的可重建本地投影                            | 否，查询快照来源 |
| Qdrant / Neo4j        | 用 ACL 字段做候选预过滤                                                | 否               |
| RAG read capability   | 为资源确定请求内 selected revision，读取正文/图事实并使用本地 ACL 判权 | 是               |
| MCP tool              | 暴露能力和参数，不自行拼接 ACL                                         | 否               |

索引中的 ACL 是否允许短暂落后、允许多长延迟，以及延迟超限后的处理方式，由一致性子方案和产品安全策略确定。无论采用何种延迟策略，索引预过滤都不能成为绕过统一
ACL capability 的理由；ACL 缺失或无法确认时，资源不能被直接分类为可读。

## 3. 查询边界

所有用户可见的 RAG 读取能力遵循以下顺序：

```text
MCP trusted context
  -> PermissionScope
  -> candidate retrieval / provider pre-filter
  -> batch Mongo lookup of active revision and ResourceAcl
  -> consistency policy + ResourceAcl.can_read(scope)
  -> domain result
```

预过滤可以减少候选数量，但不能代替最终判权。Mongo、Qdrant、Neo4j 的 ACL 表达式必须复用同一套 `ResourceAcl.can_read()` 语义；若
provider 过滤只能表达 ACL 的近似集合，必须由统一 capability 按已确认的延迟和版本策略处理，而不是各入口自行决定。

批量请求可以同时包含多个资源，只要每个资源都使用同一个 `PermissionScope` 建立快照。调用方不能在单次工具参数中为不同资源指定不同用户或群组角色。

## 4. 写入和后台任务

文档事件、ACL 同步事件和投影重建任务不从用户工具参数获得身份。它们使用事件中的资源 ID、版本和 ACL 信息，按幂等规则更新 RAG
权威事实或投影。

写入流程不因为当前用户身份而改变资源 ACL；用户身份只参与面向用户的读取授权。删除和重建按全局唯一的 `resource_id`、
`content_revision` 和投影类型定位，先撤销可见性，再执行物理清理。

## 5. 最小接口合同

MCP 工具的公开输入只包含能力所需的数据，例如 `query`、`resource_ids`、`section_ids`、图谱过滤条件和 `top_k`。以下字段禁止出现在公开工具
schema 或 RAG application request model 中：

- `user_id`；
- `group_roles`；
- `tenant_id` / `workspace_id`；
- 任意调用方提供的 ACL allow/deny 列表。

RAG application 的查询接口显式接收 `PermissionScope`，而不是接收原始 HTTP request、MCP `Context` 或 header mapping。这样
MCP、内部测试和将来的其他可信入口都可以复用同一个 ACL capability。

## 6. 验收用例

实现本方案前先建立以下 focused tests：

1. 缺少 `SecurityContextHolder.user_id` 时，MCP 适配层拒绝构造 scope，工具返回未登录错误。
2. 工具参数包含伪造 `user_id` 时，schema 不接受或 application 完全忽略该字段。
3. `tool_context` 中的 `user_id` 与安全上下文不一致时，授权仍只使用 `SecurityContextHolder` 来源。
4. owner、显式用户、群组角色、排除规则、公开群组和私有群组沿用旧 `ResourceAcl.can_read()` 真值表。
5. Qdrant/Neo4j 预过滤漏掉或多返回候选时，最终 Mongo ACL 快照仍能分别纠正，不能暴露不可见资源。
6. ACL 缺失、ACL 投影读取失败和 active revision 无法确认时，资源不得被直接判定为可读；请求级失败或逐资源过滤由一致性/业务容错子方案决定。
7. 同一次批量请求中的所有资源都使用同一 `PermissionScope`，没有按资源覆盖身份的入口。
8. 全局唯一 ID 合同下，Mongo、Qdrant、Neo4j 和删除 selector 不出现额外 tenant 维度。

## 7. 后续输入

本方案完成后，按 [03-ACL 基础投影与一致性边界.md](03-ACL%20基础投影与一致性边界.md) 实现现有事件驱动的基础 ACL 投影；之后再讨论
`active revision + ACL` 的请求级读取合同。该合同不预设 `acl_revision` 或跨后端强一致 Fence。
