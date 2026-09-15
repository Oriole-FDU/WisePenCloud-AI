"""垂类图谱的实体、关系和端点约束。"""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from rag.application.graph.models import GraphEdge, GraphNode, GraphNodeKind


class EntitySpec(BaseModel):
    """一个插件允许的实体类别。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(description="实体在本体中的类别标识。")
    description: str = Field(description="实体类别的中文或业务说明。")
    node_type: GraphNodeKind = Field(
        default=GraphNodeKind.ENTITY,
        description="实体在图谱中的节点层级类型。",
    )


class RelationSpec(BaseModel):
    """一个插件允许的关系谓词与端点类别。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relation_type: str = Field(description="关系在本体中的谓词标识。")
    description: str = Field(description="关系语义的业务说明。")
    allowed_sources: list[str] = Field(
        default_factory=list,
        description="允许作为关系起点的实体类别；为空表示不限制。",
    )
    allowed_targets: list[str] = Field(
        default_factory=list,
        description="允许作为关系终点的实体类别；为空表示不限制。",
    )


class Ontology(BaseModel):
    """垂类图谱的实体、关系及端点约束。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str = Field(description="本体所属的垂类领域标识。")
    description: str = Field(default="", description="本体整体的业务说明。")
    entity_specs: dict[str, EntitySpec] = Field(
        default_factory=dict,
        description="按实体类别标识索引的实体约束。",
    )
    relation_specs: dict[str, RelationSpec] = Field(
        default_factory=dict,
        description="按关系谓词索引的关系约束。",
    )

    def validate_node(self, node: GraphNode) -> None:
        if node.category not in self.entity_specs:
            raise ValueError(f"unknown entity category: {node.category}")

    def validate_edge(self, edge: GraphEdge, nodes: Mapping[str, GraphNode]) -> None:
        spec = self.relation_specs.get(edge.relation_type)
        if spec is None:
            raise ValueError(f"unknown relation type: {edge.relation_type}")
        source = nodes.get(edge.source_node_id)
        target = nodes.get(edge.target_node_id)
        if source is None or target is None:
            raise ValueError("relation endpoint is missing")
        if spec.allowed_sources and source.category not in spec.allowed_sources:
            raise ValueError("relation source category is not allowed")
        if spec.allowed_targets and target.category not in spec.allowed_targets:
            raise ValueError("relation target category is not allowed")
