# Application 资源快照与内容读取实施记录

记录日期：2026-10-12。本切片完成一次调用内的资源版本与权限决定，以及基于该决定的 Document／Chunk 读取。它承接 17
的普通向量发布闭环，为后续普通混合检索、标题树与图谱用例提供 application 共享入口。

## 1. 实际文件与接口

实现只新增 `src/rag/application/snapshot.py`，包含两个对象：

| 对象                     | 责任与数据                                                                                                                          |
|--------------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| `ResourceSnapshot`       | 请求运行时载体，绑定可信 PermissionScope，内部缓存 `resource_id -> ContentRevision \| None`；可见保存已选 revision，不可见保存 None |
| `ResourceSnapshotReader` | 共享读取服务，显式注入 PublicationStore、ResourceAclRepository、ContentReader；不保存请求身份或请求缓存                             |

公开入口为：

```python
async def read_documents(
    self, resource_ids: list[str], *, snapshot: ResourceSnapshot
) -> list[Document]: ...

async def read_chunks(
    self, chunk_ids: list[str], *, snapshot: ResourceSnapshot
) -> list[Chunk]: ...
```

用户确认将版本／权限解析收为 Reader 的内部 `_resolve` 方法，不再单独装配 resolver。后续用例统一使用
Reader，因此共享规则和内容读取集中在同一模块。未新增 `rag.reading` 组件层、Storage 合同或 Common 检索接口，也未保留本轮中间命名的兼容别名。

直接装配与同一次编排的调用方式：

```python
from rag.application.snapshot import ResourceSnapshot, ResourceSnapshotReader

reader = ResourceSnapshotReader(
    store=publication_store,
    resource_acls=resource_acl_repository,
    reader=content_storage,
)
snapshot = ResourceSnapshot(scope=trusted_scope)
documents = await reader.read_documents(resource_ids, snapshot=snapshot)
chunks = await reader.read_chunks(chunk_ids, snapshot=snapshot)
```

每次独立能力调用由 application 创建新的 ResourceSnapshot。同一次上层编排中的 Document、Chunk 与后续子操作显式共享它；共享的是
Reader 与规则，不跨独立调用、用户或聊天会话复用快照。

## 2. 版本与权限快照语义

内部解析先去重保序，仅对尚未观察的资源批量读取 publication 状态。只有未 destroyed 且存在 applied 的资源进入 ACL 批量读取；ACL
存在且 `can_read(snapshot.scope)` 通过时保存 ContentRevision，其余保存 None。仅 staged 的资源不进入读取路径。

版本与可见性在该资源首次完成解析后固定，负结果同样缓存。后续 applied、ACL 或 destroyed
变化仍使用首次决定，不增加输出前回查、不切换新版、不回退其他版本。新快照重新观察当前状态。已有 ResourceAcl.acl_revision
不变，也不将其解释为内容版本或上游重算完成版本。

两次后端读取成功后才写入决定，连接／查询／解码失败直接传播，不将失败缓存为不可见。顺序子操作不会重复查询已观察资源；并发子操作交错时保留最先完成的决定，晚完成者不能覆盖它。重叠中的后端查询可能重复，不增加锁、任务注册表、轮询或自动重试来保证单次
I/O。

snapshot 的含义限定为一次调用内逐资源确定的版本与权限决定，不代表资源都在同一时刻被观察，也不提供 Mongo／Qdrant／Neo4j
的原子快照、数据库事务或 Fence。它不保存正文及额外拒绝原因。

## 3. 内容读取与局部失败

Document 读取先完成上述版本与资源授权决定，再按明确的 `(resource_id, content_revision)`
批量读取。返回前核对资源、document_version、revision 字符串，并使用原始 text 的 UTF-8 字节重新计算完整 SHA-256，与 selected
ContentRevision 比较。错误身份与缺失事实省略，其他可见对象继续返回。

Chunk 读取先按 chunk_id 批量定位所属资源，再解析／复用资源决定，仅返回与 selected revision 一致的 Chunk。授权以 Document 所属
resource_id 为单位，Chunk 继承这一判定，不新增 Chunk／Section ACL，也不额外读取 Document 作为 Chunk
判权前置。定位得到的内部事实只有通过授权与版本检查后才能交给响应组装或外部模型。

事实缺失或身份不符属于对象级省略；数据库连接、查询及解码异常属于调用失败并直接传播。批量结果沿用 ContentReader
的去重、输入顺序和缺失省略合同，返回原有 Document／Chunk，不新增结果包装。空输入不发生 I/O；Document 全部不可见时不读正文，缺失
Chunk 不查询 publication 或 ACL。

## 4. Application 与 Retriever 边界

普通混合检索 use case 与 HybridRetriever 分开。后续 application 用例编排召回、资源版本与授权、正文回查、重排及结果组装；Dense／Sparse／融合
Retriever 保留固定依赖和配置，所有请求查询条件显式传入。

ResourceSnapshot 不注入 Retriever constructor，不存入共享 Retriever，不通过全局变量或隐式上下文传递。Storage
仍按明确身份读事实、执行向量或图查询，不选择发布版本或判权。

共享 snapshot 模块不依赖具体检索、标题树或图谱 use case，也不加载运行配置。未来消费者从同一 application 模块直接导入并共享请求快照，使用
Reader 的公开接口。本轮没有实现这些未来消费者或生产 Container／main 装配。

## 5. 验收证据

新增测试位于：

- `tests/application/conftest.py`：规范正文身份、发布／内容 fixture 与本地 ACL 替身。
- `tests/application/test_snapshot_selection.py`：applied 与 ACL 决定、首次不可见缓存、新快照观察变化、仅查询新增资源、批量
  I/O、交错解析及不同用户隔离。
- `tests/application/test_snapshot_reading.py`：Document／Chunk 共用快照、版本切换期间不混版、身份错误、缺失事实不回退、部分可见与顺序、空输入及后端异常。

测试通过 Reader 公开读取入口验证行为，不直接调用内部 `_resolve`。使用真实 Mongo 内容／publication adapter 与内存 BSON
collection fixture，ACL 接口为显式注入替身，不需要启动服务或加载 Nacos 配置。依赖边界测试在独立 Python 进程导入 snapshot
模块，确认未加载具体检索用例或运行配置。

实际验证命令：

```powershell
uv run --project services/rag pytest -q services/rag/tests/application/test_snapshot_selection.py services/rag/tests/application/test_snapshot_reading.py
uv run --project services/rag pytest -q services/rag/tests
uv run --project services/rag ruff check services/rag/src/rag services/rag/tests
uv run --project services/rag ruff format --check services/rag/src/rag/application/snapshot.py services/rag/tests/application/conftest.py services/rag/tests/application/test_snapshot_selection.py services/rag/tests/application/test_snapshot_reading.py
uv run --project services/rag python -m compileall -q services/rag/src/rag services/rag/tests
git diff --check
```

结果：定向 **48 passed**；全服务 **322 passed、17 skipped**；Ruff check、修改文件 format、compileall 和差异空白检查通过。本轮前基线为
274 passed、17 skipped。

17 个跳过项仍为 11 Mongo、3 Kafka、3 Qdrant 集成测试，未配置测试端点。内存 collection 与驱动替身通过不代表真实数据库或生产部署完成；本轮未新增真实后端配置。

## 6. 文档同步与下一切片

04 更新首次可见性／版本决定、资源快照命名、批量局部失败及销毁期间的边界；10／11／16 更新实际文件、完成状态、用例与 Retriever
分工及后续顺序。旧的“请求 scope 绑定请求级 Retriever 组合”表述已删除。

下一切片为普通混合检索 use case：组合无请求状态的 Dense／Sparse／融合 Retriever，在 application 通过 ResourceSnapshotReader
完成资源版本选择、授权正文回查，再重排及组装。显式查询接口、候选预算、过滤和结果参数在该切片确定；本轮不提前扩展
Common。生产生命周期装配继续留后，Contextualizer、标题树、图谱和清理／重建另行推进。
