"""把通用图检索、traversal、章节读取组合成任职工具，仍以来源为授权单位。"""

from rag.application.graph import (
    GraphEvidence,
    GraphHit,
    GraphRetrieval,
    GraphTraversal,
)
from rag.application.reading import DocumentReading
from rag.application.retrieval import ChunkHit, VectorRetrieval
from rag.application.snapshot import ResourceSnapshot
from rag.core.schema.filters import FilterOperator, MetadataFilter, MetadataFilters
from rag.retrievers.graph import GraphRetrievalMode
from rag.retrievers.vector import VectorRetrievalMode
from rag.storage.graph import GraphDirection


class EmploymentTools:
    """映射业务筛选参数，编排通用合同、图谱与原文读取用例。"""

    def __init__(
        self,
        *,
        contracts: VectorRetrieval,
        graph_retrieval: GraphRetrieval,
        graph_traversal: GraphTraversal,
        document_reading: DocumentReading,
    ) -> None:
        self._contracts = contracts
        self._graph_retrieval = graph_retrieval
        self._graph_traversal = graph_traversal
        self.reading = document_reading

    async def search_contracts(
        self,
        query: str,
        *,
        snapshot: ResourceSnapshot,
        year_from: int | None = None,
        year_to: int | None = None,
        employee: str | None = None,
        top_k: int = 10,
        candidate_limit: int = 60,
    ) -> list[ChunkHit]:
        # HTTP 请求模型拥有业务参数校验；内部调用方遵守相同合同。
        fields = []
        if year_from is not None:
            fields.append(MetadataFilter("year", year_from, FilterOperator.GTE))
        if year_to is not None:
            fields.append(MetadataFilter("year", year_to, FilterOperator.LTE))
        if employee is not None:
            fields.append(MetadataFilter("employee", employee))
        # 存储履行 filters，通用用例履行授权/版本合同；工具不重复候选准备。
        return await self._contracts.retrieve(
            query,
            snapshot=snapshot,
            mode=VectorRetrievalMode.DENSE,
            filters=MetadataFilters(fields) if fields else None,
            top_k=top_k,
            candidate_limit=candidate_limit,
        )

    async def search_graph(
        self,
        query: str,
        *,
        snapshot: ResourceSnapshot,
        mode: GraphRetrievalMode = GraphRetrievalMode.HYBRID,
        top_k: int = 10,
    ) -> list[GraphHit]:
        return await self._graph_retrieval.retrieve(
            query,
            snapshot=snapshot,
            mode=mode,
            top_k=top_k,
            neighbor_relation_types=["in_department"],
            filters=MetadataFilters([MetadataFilter("ontology_name", "employment")]),
        )

    async def follow(
        self,
        node_ids: list[str],
        *,
        snapshot: ResourceSnapshot,
        direction: GraphDirection | None = GraphDirection.OUTGOING,
    ) -> list[GraphEvidence]:
        return await self._graph_traversal.traverse(
            node_ids,
            snapshot=snapshot,
            direction=direction,
            relation_types=["in_department"],
            filters=MetadataFilters([MetadataFilter("ontology_name", "employment")]),
        )
