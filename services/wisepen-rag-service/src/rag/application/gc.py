"""销毁资源的通用物理回收；不选择 Pipeline、不删除永久发布标记。"""

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from common.logger import error, info
from rag.application.acl.store import ResourceAclRepository
from rag.application.publication.store import PublicationStore
from rag.core.schema.filters import MetadataFilter, MetadataFilters
from rag.storage.content import ContentWriter
from rag.storage.graph import GraphWriter
from rag.storage.vector import VectorWriter


@dataclass(slots=True)
class GcReport:
    """本轮实际执行计数，不表示资源以后不会出现迟到贡献。"""

    scanned: int = 0
    cleaned: int = 0
    errors: int = 0


class GcStage(StrEnum):
    SCAN = "scan"
    VECTOR = "vector"
    GRAPH = "graph"
    ACL = "acl"
    CONTENT = "content"


class ResourceGarbageCollector:
    """平台统一清理已配置存储；删除幂等，失败留给下轮重新扫描。"""

    def __init__(
            self,
            *,
            store: PublicationStore,
            content_writer: ContentWriter,
            resource_acls: ResourceAclRepository,
            vector_writers: list[VectorWriter],
            graph_writers: list[GraphWriter],
            batch_size: int = 100,
            interval_seconds: float = 600,
    ) -> None:
        self._store = store
        self._content_writer = content_writer
        self._resource_acls = resource_acls
        self._vector_writers = vector_writers
        self._graph_writers = graph_writers
        self._batch_size = batch_size
        self._interval_seconds = interval_seconds

    async def collect(self) -> GcReport:
        """一轮游标扫描；资源失败继续，扫描错误传播，标记永远保留。"""
        report = GcReport()
        cursor = None
        while True:
            resource_ids = await self._store.list_destroyed_resource_ids(
                after_resource_id=cursor, limit=self._batch_size
            )
            if not resource_ids:
                return report
            for resource_id in resource_ids:
                report.scanned += 1
                filters = MetadataFilters([MetadataFilter("resource_id", resource_id)])
                stage = GcStage.VECTOR
                try:
                    for writer in self._vector_writers:
                        await writer.delete(filters)
                    stage = GcStage.GRAPH
                    for writer in self._graph_writers:
                        await writer.delete_sources(filters)
                    stage = GcStage.ACL
                    await self._resource_acls.delete(resource_id)
                    stage = GcStage.CONTENT
                    await self._content_writer.delete_resources([resource_id])
                except Exception as exc:  # noqa: BLE001 - 按资源/服务注册边界记录错误并继续
                    error(
                        "resource_gc_failed",
                        exc=exc,
                        resource_id=resource_id,
                        stage=stage,
                    )
                    report.errors += 1
                else:
                    report.cleaned += 1
            # 标记不可恢复，无需写前再次观察状态；迟到写入由下轮回收。
            cursor = resource_ids[-1]

    async def run(self) -> None:
        """首轮立即执行，完成后等待；不重叠，不吞取消或进程终止信号。"""
        while True:
            try:
                report = await self.collect()
                info(
                    "resource_gc_completed",
                    scanned=report.scanned,
                    cleaned=report.cleaned,
                    errors=report.errors,
                )
            except Exception as exc:  # noqa: BLE001 - 按资源/服务注册边界记录错误并继续
                error("resource_gc_scan_failed", exc=exc)
            await asyncio.sleep(self._interval_seconds)
