# 永久销毁资源的物理回收

[ResourceGarbageCollector](../src/rag/application/gc.py) 清理被永久标记 destroyed 的资源。标记由[发布用例](usecases-publication.md)创建；内容和投影术语见[模型](models.md)。

## 1. 依赖与调用

注入 PublicationStore、ContentWriter、ResourceAclRepository，以及已配置的 VectorWriter/GraphWriter 列表。batch_size 默认 100，interval_seconds 默认 600，均为正数；平台使用 GC_BATCH_SIZE/GC_INTERVAL_SECONDS。

```python
async def collect_once(collector):
    # 真实调用会删除已永久销毁资源的数据；此处仅展示维护入口。
    report = await collector.collect()
    return {"scanned": report.scanned, "cleaned": report.cleaned, "errors": report.errors}
```

正常服务始终运行 `run()`，无需调用方另建调度。run 首轮立即 collect，完成后等待 interval，不重叠；首轮不参与 ready 等待，KAFKA_ENABLED=false 也不关闭 GC。

## 2. 清理顺序与报告

collect 按 resource_id 游标扫描全部永久标记，逐资源执行：

```text
全部已配置向量集合 → 全部已配置图来源 → 本地 ACL → 全部 Document/Chunk revisions
```

删除条件为 resource_id，覆盖所有 revision；拓扑删除会清理受影响孤儿图元。ResourcePublicationState 和 destroyed 标记永久保留，迟到写入不能恢复发布，下一轮仍可清理。

GcReport.scanned 为本轮扫描资源数，cleaned 为本轮全部步骤完成数，errors 为资源级失败数。cleaned 不承诺以后没有迟到写入；每轮会重新扫描永久标记，无“一次成功后停止扫描”状态。

## 3. 失败与扩展注意事项

资源某阶段失败记录 resource_id/stage，停止该资源后续阶段，继续其他资源，部分删除保留；下轮从头重新扫描。扫描错误从 collect 传播，run 记录后等待下一轮；取消传播，退出时统一等待任务再关闭共享客户端。

error 日志携带异常对象并输出 traceback；`GcReport.errors` 保持资源级计数。本轮不增加告警或连续失败阈值，也不因 GC 故障撤销 ready。

扩展登记会把额外 storage 追加到共享 writer 列表，collector 先构造也能看到新增存储。停用扩展后未声明集合不在清理范围，停用前应完成其数据回收；无需在扩展里重复实现 GC。

本用例不清理活跃资源旧 revision、非销毁历史图贡献或更换分块后的残留，也不操作上游资源库。维护窗口 repair 与 GC 是不同入口，见[运维手册](operations.md)。

验证入口：[test_gc.py](../tests/application/test_gc.py)。
