"""来源贡献与拓扑的读写端口，不解释本体、ACL 或发布状态。"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from rag.core.schema import GraphEdge, GraphNode
from rag.core.schema.filters import MetadataFilters


class GraphDirection(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class GraphSourceProjection:
    """一个 Chunk 的完整投影输入；空事实也表达移除该来源旧贡献。"""

    chunk_id: str
    metadata: dict[str, object]  # 当前包含资源/revision，后端只执行普通属性过滤。
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)


class GraphReader(Protocol):
    async def get_nodes(
        self, node_ids: list[str], *, filters: MetadataFilters | None = None
    ) -> list[GraphNode]:
        """按请求 ID、来源 Chunk ID 排序返回来源事实，空 IDs 无 I/O。"""
        ...

    async def get_relations(
        self, edge_ids: list[str], *, filters: MetadataFilters | None = None
    ) -> list[GraphEdge]:
        """同一逻辑关系的各来源分别返回，描述不汇总。"""
        ...

    async def get_neighbors(
        self,
        node_ids: list[str],
        *,
        direction: GraphDirection = GraphDirection.OUTGOING,
        relation_types: list[str] | None = None,
        filters: MetadataFilters | None = None,
        limit: int = 100,
    ) -> list[GraphEdge]:
        """一跳读取；limit 限制逻辑关系数，选中关系的匹配来源完整返回。"""
        ...


class GraphWriter(Protocol):
    async def replace_sources(self, projections: list[GraphSourceProjection]) -> None:
        """只替换明确给出的来源；全部批次确认后返回，前批成功不补偿。"""
        ...

    async def delete_sources(self, filters: MetadataFilters) -> None:
        """非空条件删除来源贡献并清理受影响孤儿；不撤销资源可见性。"""
        ...


class GraphStorage(GraphReader, GraphWriter, Protocol):
    """一个实例绑定一个物理图，核心 ID 不包含后端图命名空间。"""
