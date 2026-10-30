"""检索命中结果投影、实体连接装配与准备阶段运行时载体。"""

from dataclasses import dataclass, field, replace

from common.logger import warning
from common.utils.retrieval import Candidate
from rag.application.common.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.domain.models import Chunk, GraphRelationOption, parse_graph_facts
from rag.domain.models.graph import GraphFactValidationError
from rag.domain.storage.graph import GraphReader

# ChunkHit 响应中排除的内部字段；不修改存储中的 metadata。
_CHUNK_HIT_EXCLUDED_METADATA_KEYS = {"entities", "relationships"}


@dataclass(frozen=True, slots=True)
class PreparedChunk:
    """排序阶段的运行时 payload；chunk 已通过资源授权、版本及投影身份检查。"""

    chunk: Chunk
    embedding: list[float] | None  # 仅用于多样化，不进入应用结果。


@dataclass(frozen=True, slots=True)
class GraphNodeReference:
    """命中 Chunk 中可供调用方选择的实体；来源身份沿用外层 ChunkHit。"""

    id: str  # GraphNode.id：本体、类别与规范化名称联合确定。
    relations: list[GraphRelationOption] | None = None  # None 未查询；[] 无可见连接。


@dataclass(frozen=True, slots=True)
class ChunkHit:
    """应用可见的正文命中；score 表示最终排序阶段，不限定为重排分数。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    section_id: str | None
    section_path: list[str]
    text: str  # Chunk.raw_text；上下文不拼接到正文，业务 metadata 单独返回。
    rank: int
    score: float
    seed_entities: list[GraphNodeReference] = field(default_factory=list)
    metadata: dict[str, object] = field(
        default_factory=dict
    )  # 当前已授权 Chunk 的 metadata，排除模块常量指定的内部字段。


def _chunk_seed_entities(chunk: Chunk) -> list[GraphNodeReference]:
    """从已授权正文补充实体候选；图事实损坏不撤销正文命中。"""
    if "entities" not in chunk.metadata and "relationships" not in chunk.metadata:
        return []
    try:
        nodes, _ = parse_graph_facts(chunk)
    except GraphFactValidationError as exc:
        warning("chunk_seed_entities_rejected", chunk_id=chunk.chunk_id, error=str(exc))
        return []
    return [GraphNodeReference(node.id) for node in nodes.values()]


async def build_chunk_hits(
    ranked: list[Candidate[PreparedChunk]],
    *,
    snapshot_reader: ResourceSnapshotReader,
    graph_reader: GraphReader | None,
    snapshot: ResourceSnapshot,
) -> list[ChunkHit]:
    """两类检索共用结果组装；只为最终命中的实体批量补充全图可见连接。"""
    seeds = [_chunk_seed_entities(item.payload.chunk) for item in ranked]
    connections = None
    if graph_reader is not None:
        # graph 包公开图检索，而图检索复用本模块的 Chunk 结果；运行时加载避免循环导入。
        from rag.application.graph.graph_facts import GraphFactLoader

        loader = GraphFactLoader(
            snapshot_reader=snapshot_reader,
            graph_reader=graph_reader,
            snapshot=snapshot,
        )
        # 不继承正文业务筛选；导航面向整个已授权图，读取失败不能伪装为空连接。
        connections = await loader.visible_connections(
            [seed.id for chunk_seeds in seeds for seed in chunk_seeds]
        )
    return [
        ChunkHit(
            chunk_id=item.payload.chunk.chunk_id,
            resource_id=item.payload.chunk.resource_id,
            content_revision=item.payload.chunk.content_revision,
            section_id=item.payload.chunk.section_id,
            section_path=item.payload.chunk.section_path,
            text=item.payload.chunk.raw_text,
            rank=item.rank,
            score=item.score,
            seed_entities=[
                replace(seed, relations=connections[seed.id])
                if connections is not None
                else seed
                for seed in chunk_seeds
            ],
            # 响应单独投影；种子解析、图事实回查及存储仍使用完整 metadata。
            metadata={
                key: value
                for key, value in item.payload.chunk.metadata.items()
                if key not in _CHUNK_HIT_EXCLUDED_METADATA_KEYS
            },
        )
        for item, chunk_seeds in zip(ranked, seeds, strict=True)
    ]
