# ACL、请求快照与授权读取

权限事实见 [ACL models](../src/rag/application/acl/models.py)，刷新见 [synchronizer](../src/rag/application/acl/synchronizer.py)，共享读取见 [snapshot.py](../src/rag/application/snapshot.py)。这些对象属于 application，不嵌入[内容模型](models.md)。

## 1. ACL 刷新

AclSynchronizer 注入上游 resource collection 与 ResourceAclRepository。`handle_event(payload)` 接收 resourceId 刷新触发，`synchronize(resource_ids)` 去重保序读取上游当前事实；事件不是权威 ACL 快照。

```python
async def refresh_acl(synchronizer, resource_id):
    return await synchronizer.handle_event({"resourceId": resource_id})
```

上游资源集合为 `wisepen_resource_items`，resource_id 必须可转 ObjectId，入口统一为规范小写身份。投影读取 ownerId、updateTime、用户 action masks 和 computedGroupAcls；VIEW 为 `1 << 1`。ResourceAcl.acl_revision 来自 updateTime 的 UTC 毫秒时间戳，用于本地更新准入，不是跨服务一致性 Fence。

上游允许 specifiedUsersGrantedActionsMask、computedGroupAcls 顶层缺失或 null，分别表示没有指定用户权限、尚无群组投影。空对象同样合法。错误类型、非法用户/群组条目以及已有群组内的 baseMask/userMasks 缺项会抛 AuthoritativeAclError，不静默丢弃。RAG 自己保存的本地 ACL 始终包含全部列表字段，读取时严格解析，不用 str/int/bool 转换或空列表补齐掩盖损坏。上游核对证据及尚未修改的失败策略见[失败策略 Review](review-failure-strategies.md)。

返回 repository 接受保存的资源 ID；权威记录不存在则删除本地 ACL，不计入成功列表。Mongo 接受不小于已有值的 acl_revision（包括相同 revision），旧 revision 不覆盖现值。必需字段损坏抛 AuthoritativeAclError，I/O 传播。默认 ACL topic/group 和消费者连接行为见[运行说明](deployment.md)。

## 2. VIEW 判权顺序

| 顺序 | 规则 |
|---|---|
| 1 | 所有者直接可读 |
| 2 | 全局 readable_users 直接可读 |
| 3 | 全局 excluded_read_users 拒绝，优先于群组规则 |
| 4 | OWNER/ADMIN 群组可读，不受该群组排除项限制 |
| 5 | 已加入群组：默认可读时检查群组排除；默认不可读时检查群组白名单 |
| 6 | 无任何允许则拒绝 |

判权按资源/Document，Chunk 与章节继承，不存在单块 ACL。PermissionScope 保存可信 user_id 和群组角色；公开业务参数不能让调用者自填用户或覆盖 ACL。Employment 可信 Header 入口见[示例](employment.md)。

投影忠实保留上游 `userMasks` 对 `baseMask` 的覆盖：默认可读群组内的用户 mask=0 仍进入排除名单；该组拒绝不否决另一个组的有效授权。资源级指定用户 mask 则覆盖所有群组，包括群组管理员。仅投影 VIEW（bit 1），MARKET_VIEW（bit 9）不自动获得 RAG 正文读取权限。

市场组的 `market_offer_version` 来自权威 `groupBinds.marketSaleInfo.offerVersion`，普通成员必须匹配当前 applied 文档版本；OWNER/ADMIN 和资源所有者的优先级与上游预计算快速判权一致。快照在选定版本后传入该版本判权，不引入另一份“当前版本”查询。

旧本地群组 ACL 缺少 `market_offer_version` 时会在严格读取边界失败；需通过 ACL 同步重新生成完整投影，不能把未知的旧市场组投影默认为普通组放行。该字段由 Mongo ACL writer 保存，不属于 Document/Chunk metadata。

## 3. ResourceSnapshot 与 Reader

ResourceSnapshot 绑定 PermissionScope，仅缓存 `resource_id → ContentRevision | None`；不可见也缓存。ResourceSnapshotReader 共享固定 PublicationStore、ACL repository 和 ContentReader，没有跨用户请求状态。

```python
from rag.application.acl.models import PermissionScope
from rag.application.snapshot import ResourceSnapshot


async def read_visible(reader, trusted_user_id, resource_ids, chunk_ids):
    # trusted_user_id 由可信入口传入；同一次上层编排显式共享快照。
    snapshot = ResourceSnapshot(PermissionScope(trusted_user_id))
    documents = await reader.read_documents(resource_ids, snapshot=snapshot)
    chunks = await reader.read_chunks(chunk_ids, snapshot=snapshot)
    return documents, chunks
```

| Reader 方法 | 输入与行为 |
|---|---|
| read_documents(resource_ids, snapshot=...) | 对尚未观察资源选 applied/ACL，再读 Document 并核对完整正文身份 |
| read_documents_by_section_ids(section_ids, snapshot=...) | 通过内容章节索引定位，再执行同样的资源与身份检查 |
| read_chunks(chunk_ids, snapshot=...) | 取事实后选择所属资源，保留 selected revision 中的 Chunk |

没有可见版本、destroyed 或无 ACL 的资源不可见。Document 核对资源、version、revision 字符串及完整正文摘要；Chunk 核对资源与 selected revision，不重新对每个 Chunk 计算文档摘要。正文事实只在授权检查通过后交给调用者或外部模型。

## 4. 一次调用内的决定

首次完成的版本/权限决定固定；同次子操作显式共享 snapshot。后续 applied、ACL、destroyed 变化不触发回查或切换；下一次独立调用创建新快照重新观察。交错子操作保留最先完成决定，不引入全局 context 或跨会话缓存。

快照不是 Mongo/Qdrant/Neo4j 同一时间点快照，也不缓存正文。事实仍可能缺失或在维护时被改写，因此读侧继续执行身份检查。应避免把快照放进 Singleton Retriever 或跨用户复用。

## 5. 空结果与异常

批量输入去重保序，缺失、不可见和错误身份仅省略对应对象，不回退其他版本；空输入无 I/O。基础设施查询、连接或解码错误传播，不伪装成“不可见”，也不写入可见性决定。

普通检索还需核对向量候选的定位身份；图检索还需核对当前事实。Reader 负责授权事实边界，不能以 vector filters 代替 ACL。后续操作见[向量](usecases-vector.md)、[图](usecases-graph.md)和[章节读取](usecases-reading.md)。
