"""后续严格抽取与图索引共享的最小节点、关系合同。"""

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """实体的语义身份与单次抽取来源，不携带资源可见性投影。"""

    name: str
    ontology_name: str | None = None
    category: str | None = None
    description: str | None = None
    source_id: str  # 产生此节点事实的来源 Chunk ID。

    @property
    def id(self) -> str:
        # 只规范化实体名；缺失类别保留空槽，ontology/category 保持原值。
        normalized_name = " ".join(self.name.strip().casefold().split())
        category = self.category or ""
        if self.ontology_name:
            return f"{self.ontology_name}:{category}:{normalized_name}"
        return f"{category}:{normalized_name}"


class GraphEdge(BaseModel):
    """有向关系及其来源；端点 ID 与 provenance 分别表达拓扑和来源。"""

    source_id: str  # 起点 GraphNode.id，不是来源 Chunk ID。
    target_id: str  # 终点 GraphNode.id。
    relation_type: str
    description: str | None = None
    keywords: list[str] | None = None
    source_ids: list[str] = Field(default_factory=list)  # 来源 Chunk ID 列表。
    metadata: dict[str, object] = Field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.source_id}::{self.relation_type}::{self.target_id}"
