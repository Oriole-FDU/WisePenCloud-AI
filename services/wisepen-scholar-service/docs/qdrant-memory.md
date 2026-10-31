# 论文集合的内存缓存

当前服务使用 Qdrant 1.18.3。论文混合集合的稠密向量和 HNSW 采用加载时预热缓存：

```json
{
  "vectors": {"dense": {"on_disk": false}},
  "hnsw_config": {"on_disk": false}
}
```

这里的 `false` 对应新版文档的 `cached`：文件仍持久化在磁盘，加载时预热 mmap 页，资源足够时由操作系统保留在内存，内存压力下允许回收。它不是锁页，不保证数据永久驻留。`memory: cached/pinned/cold` 是 1.19 起的接口，不能直接用于当前 1.18.3。参见 [Qdrant 官方内存分层及旧配置对应关系](https://qdrant.tech/documentation/ops-configuration/memory-tiers/#legacy-settings)。

`ensure_collection()` 对新集合使用上述默认值。已有集合须通过 `PATCH /collections/{collection_name}` 提交上述 JSON；建库函数不会覆盖已有集合的存储策略。修改可能触发后台优化和索引重建，无需重新上传论文。等待集合恢复 green 后，还须执行实际混合检索验收；仅看到配置更新或 green 不代表检索时延已经达标。

当前 3,232,977 条有效稠密向量，768 维 float32 原始数据约 9.25 GiB，另需 HNSW、ID 映射、payload 索引及运行内存。Qdrant 遥测中的分配器 `memory.resident_bytes` 不包含全部 mmap 页，不能把它当作容器总内存使用量；宿主机总内存也不能证明容器限额足够。

三个 BM25 索引保持 `on_disk: true`，完整论文 payload 保持 `on_disk_payload: true`。本次观测的 BM25 查询为毫秒级，主要冷读发生在稠密通道；不将全部论文 JSON 和稀疏索引一并改成常驻，避免额外占用内存。缓存模式不改变向量精度或检索参数。

2026-10-31 的观测：首次完整混合检索曾因稠密分支超过 60 秒而失败，随后 Dashboard ANN 恢复，生产仓储代码的两个不同混合查询分别耗时 63/31 毫秒。该时序与冷页读取后进入缓存一致，尚不能证明重启或缓存回收后的时延。预热缓存把加载成本前移；服务启动预热和真实检索验收仍须保留。

后台转换使用原看板包装器运行 `deploy/migrate_qdrant.py --resume` 监督：断点已越过上传阶段时只等待索引并重新验收，不重传论文，也不自动切换别名。
