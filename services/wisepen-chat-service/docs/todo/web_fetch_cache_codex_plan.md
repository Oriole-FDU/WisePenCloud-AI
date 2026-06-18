# Web Fetch 公共缓存构造分阶段实施方案

正式实现之前，必须确保已经通过联网查询或本地最小实验验证了相关库的真实 API，必须确保已经理解仓库现有的实现机制，必须确保已经理解并遵守当前任务涉及的所有 skill 规范。

## 任务背景

当前项目已经具备以下基础能力：

```text
web_search
web_fetch
web_crawl
document_parse
fetch -> parse 链路
ToolRunFileStore
```

已确认：

```text
web_search / web_fetch / web_crawl / document_parse 单测通过
fetch -> parse 链路已通
web_fetch 可以将非 HTML 文件 publish_file 到 ToolRunFileStore
ToolRunFileStore 已支持 ref 前缀
web_fetch 产出的文件引用使用 web 前缀，形态为 tfile_web_xxx
```

本任务目标是为 `web_fetch` 构造公共缓存能力，并让公开 URL 文件可以在后续 `document_parse` 中复用解析结果。

核心方向：

```text
Redis 存 URL 索引、新鲜度、过期时间、刷新锁
MongoDB 存 HTML、Markdown、文件引用、公共缓存正文
平台付费搜索写公共缓存池
用户自定义搜索只读公共缓存池
私有浏览和用户私有数据不进入公共缓存池
web_fetch 不直接调用 document_parse
tfile_web_* 文件引用通过 ToolRunFileStore metadata 被 document_parse 识别
```

## 现有实现需要先读懂

开始写代码之前，先完整阅读仓库中这些部分：

```text
web_fetch 内部服务层
web_crawl 内部服务层
document_parse 入口和解析调度
fetch -> parse 链路测试
ToolRunFileStore publish_file / read / metadata 机制
Redis 封装
MongoDB 封装
现有 dataclass / enum / repository 风格
现有测试结构
```

需要特别确认：

```text
web_fetch 当前如何区分 HTML 和非 HTML
web_fetch 当前如何生成 WebFetchResult
web_fetch 当前如何调用网页渲染器
非 HTML 文件当前如何 publish_file
ToolRunFileStore 如何保存 ref_prefix 和 metadata
document_parse 如何读取 tfile_* 引用
测试中如何 fake 外部依赖
```

## Hishel 使用定位

可以评估接入 Hishel，但它只属于 `httpx fetcher` 的底层 HTTP 缓存能力。

Hishel 用于：

```text
Cache-Control
ETag
Last-Modified
304 Not Modified
httpx 过期重抓时减少实际下载成本
```

业务缓存仍由本任务实现：

```text
Redis WebCacheIndex
MongoDB WebCachedDocument
soft_expire_at / hard_expire_at
public_read_write / public_read_only / private_session
ToolRunFileStore 文件引用
document_parse 解析缓存
```

接入原则：

```text
如果仓库当前 httpx fetcher 适合无侵入接入 Hishel，可以接入。
如果接入 Hishel 会造成大范围改动，先保留普通 httpx，并预留 HttpCacheClient 或 HttpTransport 抽象。
Hishel 只影响网络层下载成本，不改变 Redis/Mongo 业务缓存状态机。
```

## 阶段一：缓存模型与仓储接口

### 目标

建立公共缓存的基础模型、枚举和仓储接口，不接入 `web_fetch` 主流程。

### 实现内容

新增缓存作用域：

```text
CacheScope.PUBLIC_POOL
CacheScope.PRIVATE_SESSION
CacheScope.READONLY_PUBLIC
```

新增缓存内容类型：

```text
WebCachedContentKind.HTML
WebCachedContentKind.FILE
```

新增 Redis 索引模型 `WebCacheIndex`：

```text
url_hash
canonical_url
mongo_doc_id
cache_scope
content_kind
soft_expire_at
hard_expire_at
etag
last_modified
```

新增 MongoDB 正文模型 `WebCachedDocument`：

```text
id
canonical_url
final_url
cache_scope
content_kind
status_code
content_type
raw_html
markdown
file_ref
content_hash
fetched_at
metadata
```

新增仓储接口 `WebFetchCacheRepository`：

```text
get_index(url: str) -> WebCacheIndex | None
set_index(index: WebCacheIndex) -> None
get_document(doc_id: str) -> WebCachedDocument | None
save_document(document: WebCachedDocument) -> str
delete_index(url: str) -> None
```

### 实现要求

```text
遵循仓库现有 Redis / MongoDB 封装风格
dataclass 使用 frozen=True, slots=True
类型标注完整
URL hash 逻辑集中封装
模型序列化和反序列化可测试
```

### 验收标准

```text
模型序列化测试通过
URL hash 测试通过
repository fake 测试通过
现有 web_fetch / web_crawl / document_parse 测试保持通过
```

## 阶段二：TTL 与新鲜度策略

### 目标

实现公共缓存的新鲜度计算模块，不接入真实抓取流程。

### 实现内容

新增 `WebCacheFreshnessPolicy`，根据以下输入计算过期时间：

```text
url
content_type
headers
content_kind
fetched_at
```

输出：

```text
soft_expire_at
hard_expire_at
```

新增状态判断：

```text
fresh: now <= soft_expire_at
stale: soft_expire_at < now <= hard_expire_at
expired: now > hard_expire_at
```

### TTL 规则

公开文件直链：

```text
content_kind = file
或 content_type 属于 pdf/docx/pptx/xlsx/csv 等
soft TTL = 7 天
hard TTL = 30 天
```

`Cache-Control: no-store`：

```text
soft TTL = 0
hard TTL = 1 分钟
```

`Cache-Control: no-cache`：

```text
soft TTL = 0
hard TTL = 10 分钟
```

`Cache-Control: max-age=N`：

```text
soft TTL = min(N, 7 天)
hard TTL = soft TTL * 2
```

`Last-Modified` 超过 1 年：

```text
soft TTL = 7 天
hard TTL = 14 天
```

`Last-Modified` 小于 24 小时：

```text
soft TTL = 10 分钟
hard TTL = 1 小时
```

普通网页兜底：

```text
soft TTL = 30 分钟
hard TTL = 2 小时
```

### 实现要求

```text
HTTP date 使用标准库解析
Cache-Control 解析要容错
content_type 需要去掉 charset 等参数后判断
规则顺序保持稳定
测试覆盖每条规则
```

### 验收标准

```text
max-age 测试通过
no-store 测试通过
no-cache 测试通过
Last-Modified 测试通过
公开文件 TTL 测试通过
兜底网页 TTL 测试通过
现有测试保持通过
```

## 阶段三：接入 web_fetch 读缓存与写缓存

### 目标

把公共缓存接入 `web_fetch` 服务层，实现 fresh 命中、miss 抓取、抓取成功后写缓存。

### 实现内容

`fetch_one` 开始时读取 Redis index：

```text
index 存在且状态 fresh:
    读取 MongoDB document
    构造 WebFetchResult
    标记 cache_state=fresh

index 不存在:
    走现有抓取流程

index 存在但 document 不存在:
    走现有抓取流程

index 状态 stale 或 expired:
    本阶段先同步重新抓取
```

抓取成功后写缓存：

```text
HTML:
    保存 raw_html / markdown / status_code / content_type / final_url

FILE:
    保存 file_ref / content_type / final_url
    file_ref 应保持 tfile_web_xxx 形态
```

写入时：

```text
计算 content_hash
调用 WebCacheFreshnessPolicy
保存 WebCachedDocument 到 MongoDB
保存 WebCacheIndex 到 Redis
```

### 实现要求

```text
不改变 tool 层 schema
不改变 document_parse 调用方式
fetch_many 可以复用 fetch_one
测试使用 fake repository
cache hit 时不得触发真实抓取
```

如果 httpx fetcher 已经安全接入 Hishel，同步重抓时可以复用 Hishel 的 HTTP 条件验证；服务层仍以 `WebFetchCacheRepository` 作为业务缓存依据。

### 验收标准

```text
cache miss 走原抓取流程
fresh cache hit 不触发真实抓取
HTML 抓取成功后写缓存
file 抓取成功后写缓存
file_ref 保持 tfile_web_xxx
fetch_many 部分失败结构保持可用
现有 fetch -> parse 测试保持通过
```

## 阶段四：公私分流策略

### 目标

实现平台公共池、用户自定义搜索、私有会话三种缓存模式。

### 实现内容

新增 `WebFetchCacheMode`：

```text
public_read_write
public_read_only
private_session
```

`public_read_write`：

```text
先读公共池
miss 后抓取
抓取成功后写公共池
```

`public_read_only`：

```text
先读公共池
miss 后抓取
抓取结果不写公共池
```

`private_session`：

```text
使用现有抓取链路
不读取公共池
不写入公共池
```

### 实现要求

```text
优先从现有 ToolContext / execution context / user context 中传递 cache mode
不改 tool 层入参
默认模式与平台公共抓取路径一致
私有/登录态/用户上传数据使用 private_session
```

### 验收标准

```text
public_read_write 可读写公共池
public_read_only 可读公共池但不写公共池
private_session 完全绕过公共池
现有测试保持通过
```

## 阶段五：tfile_web_* 到 document_parse 的公共解析缓存

### 目标

让 `document_parse` 能识别由 `web_fetch` 产生的公开 URL 文件引用，并复用公共解析缓存。

### 实现内容

`web_fetch publish_file` 时写入 ToolRunFileStore metadata：

```text
source_kind = web_fetch
source_scope = public_url
source_ref = WebCachedDocument id 或 url_hash
source_url
final_url
content_type
```

`document_parse` 入口读取 file metadata：

```text
file_ref 前缀为 web
且 metadata.source_scope = public_url:
    尝试读取公共 parse cache
    命中则返回解析结果
    未命中则正常解析
    解析成功后写公共 parse cache
```

公共 parse cache key：

```text
source_ref
parser_name
parser_version
```

普通用户文件：

```text
tfile_* 且无 web 前缀
或 metadata.source_scope 不是 public_url
使用现有私有解析链路
```

### 实现要求

```text
document_parse 只依赖 ToolRunFileStore metadata
document_parse 不依赖 web_fetch 内部实现
parser_version 变化应触发重新解析
```

### 验收标准

```text
tfile_web_* 命中 parse cache 时直接返回
tfile_web_* 未命中时解析并写入 parse cache
普通 tfile_* 不写公共 parse cache
parser_version 变化会重新解析
fetch -> parse 链路保持通过
```

## 阶段六：Stale-While-Revalidate

### 目标

实现软过期异步刷新。

### 实现内容

当缓存状态为 `stale`：

```text
直接读取 MongoDB 旧 document 返回
标记 cache_state=stale
尝试获取 Redis 刷新锁
获取成功后投递后台刷新任务
获取失败则说明已有任务在刷新
```

刷新锁：

```text
refresh_lock:{url_hash}
NX
EX 120
```

后台刷新任务：

```text
输入 url、cache_mode、旧 index
重新抓取 URL
成功后保存新的 WebCachedDocument
更新 WebCacheIndex
失败时保留旧缓存
```

`expired` 状态：

```text
同步重新抓取
```

如果 httpx fetcher 已经安全接入 Hishel，后台刷新执行 httpx 请求时也复用 Hishel。Hishel 只影响网络下载成本，不改变 fresh/stale/expired 状态机。

### 实现要求

```text
复用仓库现有后台任务/队列机制
如果暂时没有统一队列，抽象 RefreshTaskPublisher Protocol
测试中使用 fake publisher
重复 stale 请求不会重复投递刷新任务
```

### 验收标准

```text
stale 命中立即返回旧内容
stale 命中会投递刷新任务
已有刷新锁时不重复投递
expired 仍同步重抓
刷新成功后缓存指向新 document
现有测试保持通过
```

## 阶段七：回归整理

### 目标

整理代码并完成全链路回归。

### 检查项目

`web_fetch`：

```text
cache miss
fresh hit
stale hit
expired refetch
HTML 缓存
file 缓存
fetch_many 部分失败
```

`document_parse`：

```text
普通文件解析
tfile_web_* 解析
parse cache 命中
parser_version 变化重新解析
```

公私分流：

```text
public_read_write
public_read_only
private_session
```

文件链路：

```text
非 HTML URL
ToolRunFileStore publish_file
tfile_web_xxx
document_parse 读取成功
```

代码整理：

```text
命名统一
日志清晰
异常结构化
测试 fake 清楚
没有破坏现有 tool 层接口
```

### 验收标准

运行并通过：

```text
web_fetch 测试
web_crawl 测试
document_parse 测试
fetch -> parse 链路测试
新增 cache 测试
```

最终输出：

```text
修改了哪些模块
每个阶段完成情况
测试运行结果
残留风险
后续建议
```

## 总执行顺序

按以下顺序逐阶段执行，每阶段完成后先跑对应测试，再进入下一阶段：

```text
阶段一：缓存模型与仓储接口
阶段二：TTL 与新鲜度策略
阶段三：接入 web_fetch 读写缓存
阶段四：公私分流策略
阶段五：tfile_web_* 到 document_parse 的公共解析缓存
阶段六：Stale-While-Revalidate
阶段七：回归整理
```

