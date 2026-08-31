"""用生产应用算法生成 LOCATE 与 Graph EXPAND 的评审输出。"""

import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path

from _demo_documents import (
    DemoDocument,
    build_demo_document,
    flat_text_markdown,
    sectioned_markdown,
)

from rag.api.schemas import CandidateLocateResponse, GraphExpandResponse
from rag.application.rag.acl import PermissionAuthorizer
from rag.application.rag.navigate import (
    GraphEvidenceVerifier,
    KnowledgeGraphExpander,
    ReadingCandidateLocator,
    SourceEvidenceVerifier,
)
from rag.domain.models.acl import PermissionScope, ResourceAcl
from rag.domain.models.graph import (
    GraphEvidence,
    KnowledgeEntityType,
    KnowledgeMention,
    KnowledgeNode,
    KnowledgeNodeKind,
    KnowledgeRelationType,
)
from rag.domain.models.provenance import SourceEvidence
from rag.domain.models.retrieval import RetrievalCandidate
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedGraphEvidence,
)
from rag.domain.repositories.neo4j import GraphQuerySubgraph, TraversedEdge, TraversedPath
from rag.domain.repositories.redis import NavigationState
from common.utils.document import SourceSpan
from common.utils.ranking import RankingPipeline
from common.utils.ranking.fusion import WeightedRrfFusion
from common.utils.ranking.rank_gates import (
    HighLowRelevanceGate,
    HighLowRelevanceGateConfig,
)
from common.utils.ranking.scorers import BM25Scorer
from common.utils.ranking.tokenizer import JiebaRankingTokenizer


@dataclass(slots=True)
class _EmbeddingResult:
    embeddings: list[list[float]]


class _EmbeddingClient:
    async def aembed(self, values):
        return _EmbeddingResult([[0.31, 0.67] for _ in values])


class _CandidateSearch:
    """代替 Qdrant；返回值仍是生产 RetrievalCandidate。"""

    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self._candidates = candidates

    async def search(
        self,
        *,
        lexical_query,
        semantic_vector,
        permission_scope,
        limit,
    ):
        tokens = JiebaRankingTokenizer().tokenize(lexical_query)
        ranked = sorted(
            self._candidates,
            key=lambda candidate: sum(
                token in candidate.raw_text for token in tokens if token.strip()
            ),
            reverse=True,
        )
        return [
            replace(
                candidate,
                score=candidate.score
                + sum(token in candidate.raw_text for token in tokens if token.strip()),
            )
            for candidate in ranked[:limit]
        ]


class _AclStore:
    async def get_resource_acl(self, resource_id):
        return _acl(resource_id)

    async def get_resource_acls(self, resource_ids):
        return {resource_id: _acl(resource_id) for resource_id in resource_ids}


class _RevisionReader:
    def __init__(self, documents: list[DemoDocument]) -> None:
        self._revisions = {
            document.resource_id: document.revision.content_revision
            for document in documents
        }

    async def get_content_revision(self, resource_id):
        return self._revisions.get(resource_id)


class _PublishedResourceReader:
    """代替 Mongo 查询；两个生产 verifier 仍负责各自证据边界。"""

    def __init__(
        self,
        records: list[SourceEvidence],
        documents: list[DemoDocument],
    ) -> None:
        self._records = {
            (record.source_ref.resource_id, record.source_ref.ref_id): record
            for record in records
        }
        self._documents = {
            document.resource_id: document for document in documents
        }

    async def get_source_evidence(
        self,
        resource_id,
        content_revision,
        source_ref_ids,
    ):
        records = {
            ref_id: self._records[(resource_id, ref_id)]
            for ref_id in source_ref_ids
            if (resource_id, ref_id) in self._records
        }
        if any(
            record.source_ref.content_revision != content_revision
            for record in records.values()
        ):
            return None
        return records

    async def get_graph_evidence(
        self,
        resource_id,
        content_revision,
        evidence,
    ):
        document = self._documents.get(resource_id)
        if (
            document is None
            or document.revision.content_revision != content_revision
        ):
            return None
        blocks_by_id = {
            block.block_id: block for block in document.reading_blocks
        }
        sections_by_id = {
            section.section_id: section for section in document.sections
        }
        result = {}
        for item in evidence:
            block = blocks_by_id[item.reading_block_id]
            local_start = block.raw_text.index(item.quote)
            assert document.markdown[
                item.source_span.start_offset : item.source_span.end_offset
            ] == item.quote
            result[item.evidence_id] = PublishedGraphEvidence(
                evidence=item,
                reading_block=block,
                section=_section_for_evidence(
                    block.section_ids,
                    sections_by_id,
                    item.source_span,
                ),
                block_range=SourceSpan(
                    local_start,
                    local_start + len(item.quote),
                ),
                reading_block_sections=[
                    sections_by_id[section_id] for section_id in block.section_ids
                ],
            )
        return result


def _section_for_evidence(section_ids, sections_by_id, source_span):
    """解析多 Section ReadingBlock 中证据 span 的实际 Section 归属。"""
    for section_id in section_ids:
        section = sections_by_id[section_id]
        if any(
            source_span.start_offset >= content_span.start_offset
            and source_span.end_offset <= content_span.end_offset
            for content_span in section.content_spans
        ):
            return section
    raise ValueError("graph evidence does not belong to its ReadingBlock sections")


class _MentionGraph:
    """代替 Neo4j mention 查询；flat text 按生产规则没有图节点。"""

    async def find_nodes(self, *, reading_blocks, permission_scope, limit):
        if not any(
            block.resource_id == "demo-wisepen-rag"
            for block in reading_blocks
        ):
            return []
        return [
            KnowledgeNode(
                node_id="kn_demo_wisepen_rag",
                label="WisePen RAG",
                kind=KnowledgeNodeKind.ENTITY,
                entity_type=KnowledgeEntityType.PRODUCT,
            )
        ][:limit]


class _StateStore:
    def __init__(self) -> None:
        self._states: dict[str, NavigationState] = {}

    async def create(self, **kwargs):
        state = NavigationState(
            state_id=f"nav_demo_{len(self._states) + 1}",
            **kwargs,
        )
        self._states[state.state_id] = state
        return state

    async def get(self, state_id):
        return self._states.get(state_id)

    async def add_known_nodes(self, *, state_id, node_ids):
        state = self._states[state_id]
        added = [node_id for node_id in node_ids if node_id not in state.known_node_ids]
        state.known_node_ids.extend(added)
        return added


class _TraversalGraph:
    """代替 Neo4j 查询，返回一次 LLM 抽取并发布后的固定图事实。"""

    def __init__(
        self,
        path: TraversedPath,
        mentions: list[KnowledgeMention],
    ) -> None:
        self._path = path
        self._mentions = mentions

    async def find_subgraph(self, *, seed_node_ids, **kwargs):
        return GraphQuerySubgraph(
            paths=[self._path]
            if "kn_demo_wisepen_rag" in seed_node_ids
            else [],
            mentions=self._mentions,
        )


async def main() -> None:
    sectioned = build_demo_document(
        resource_id="demo-wisepen-rag",
        markdown=sectioned_markdown(),
    )
    flat_text = build_demo_document(
        resource_id="demo-orchard-frost-log",
        markdown=flat_text_markdown(),
    )
    sectioned_phrase = "向量检索召回相关 ReadingBlock"
    flat_phrase = "日出前后最容易出现当夜最低温"
    sectioned_candidate = _candidate_containing(sectioned, sectioned_phrase, 0.93)
    flat_candidate = _candidate_containing(flat_text, flat_phrase, 0.89)
    records = _evidence_records([sectioned, flat_text])
    published_reader = _PublishedResourceReader(records, [sectioned, flat_text])
    evidence_verifier = SourceEvidenceVerifier(reader=published_reader)
    graph_evidence_verifier = GraphEvidenceVerifier(reader=published_reader)
    state_store = _StateStore()
    authorizer = PermissionAuthorizer(local_store=_AclStore())
    locator = ReadingCandidateLocator(
        embedding_client=_EmbeddingClient(),
        candidate_search=_CandidateSearch([sectioned_candidate, flat_candidate]),
        ranking_pipeline=RankingPipeline(
            fusion=WeightedRrfFusion(),
            gate=HighLowRelevanceGate(
                HighLowRelevanceGateConfig(
                    low_watermark=0.005,
                    high_watermark=0.015,
                    uncertain_limit=2,
                )
            ),
        ),
        authorizer=authorizer,
        evidence_verifier=evidence_verifier,
        knowledge_graph=_MentionGraph(),
        published_resources=_RevisionReader([sectioned, flat_text]),
        state_store=state_store,
    )
    scope = PermissionScope(user_id="demo-reviewer")
    sectioned_result = await locator.locate(
        session_id="demo-session",
        semantic_query="向量检索如何召回 ReadingBlock？",
        permission_scope=scope,
        max_results=1,
    )
    flat_result = await locator.locate(
        session_id="demo-session",
        semantic_query="果园为什么在日出前仍要持续监测温度？",
        permission_scope=scope,
        max_results=1,
    )

    graph_quote = "WisePen RAG 使用 GraphRAG 技术补充实体关系导航"
    graph_related_quote = "GraphRAG 使用知识图谱表示实体之间的关系"
    graph_result = await KnowledgeGraphExpander(
        knowledge_graph=_TraversalGraph(
            _graph_path(
                document=sectioned,
                quote=graph_quote,
                related_quote=graph_related_quote,
            ),
            _graph_mentions(sectioned),
        ),
        ranking_pipeline=RankingPipeline(
            scorers=(BM25Scorer(tokenizer=JiebaRankingTokenizer()),),
            fusion=WeightedRrfFusion(),
        ),
        evidence_verifier=graph_evidence_verifier,
        authorizer=authorizer,
        state_store=state_store,
    ).expand(
        state_id=sectioned_result.state_id,
        session_id="demo-session",
        permission_scope=scope,
        seed_node_ids=["kn_demo_wisepen_rag"],
        query="WisePen RAG 如何通过图谱继续读取知识？",
    )

    sectioned_payload = CandidateLocateResponse.model_validate(
        sectioned_result
    ).model_dump(mode="json", exclude_none=True)
    flat_payload = CandidateLocateResponse.model_validate(flat_result).model_dump(
        mode="json",
        exclude_none=True,
    )
    graph_payload = GraphExpandResponse.model_validate(graph_result).model_dump(
        mode="json",
        exclude_none=True,
    )
    _write_outputs(
        sectioned=sectioned,
        flat_text=flat_text,
        sectioned_payload=sectioned_payload,
        flat_payload=flat_payload,
        graph_payload=graph_payload,
        graph_quote=graph_quote,
        graph_related_quote=graph_related_quote,
    )


def _candidate_containing(
    document: DemoDocument,
    phrase: str,
    score: float,
) -> RetrievalCandidate:
    chunk = next(
        chunk for chunk in document.retrieval_chunks if phrase in chunk.raw_text
    )
    source_ref = next(
        ref for ref in document.source_refs if ref.chunk_id == chunk.chunk_id
    )
    return RetrievalCandidate(
        chunk_id=chunk.chunk_id,
        reading_block_id=chunk.reading_block_id,
        section_id=chunk.section_id,
        section_path=list(chunk.section_path),
        resource_id=document.resource_id,
        content_revision=document.revision.content_revision,
        raw_text=chunk.raw_text,
        source_spans=list(chunk.source_spans),
        page_labels=list(chunk.page_labels),
        anchor_labels=list(chunk.anchor_labels),
        source_ref_id=source_ref.ref_id,
        score=score,
    )


def _evidence_records(documents: list[DemoDocument]) -> list[SourceEvidence]:
    records = []
    for document in documents:
        chunks_by_id = {chunk.chunk_id: chunk for chunk in document.retrieval_chunks}
        blocks_by_id = {block.block_id: block for block in document.reading_blocks}
        sections_by_id = {section.section_id: section for section in document.sections}
        for source_ref in document.source_refs:
            chunk = chunks_by_id[source_ref.chunk_id]
            block = blocks_by_id[source_ref.reading_block_id]
            records.append(
                SourceEvidence(
                    source_ref=source_ref,
                    reading_block=block,
                    section=sections_by_id[source_ref.section_id],
                    source_text=chunk.raw_text,
                    reading_block_sections=[
                        sections_by_id[section_id] for section_id in block.section_ids
                    ],
                )
            )
    return records


def _graph_path(
    *,
    document: DemoDocument,
    quote: str,
    related_quote: str,
) -> TraversedPath:
    relation_evidence = _graph_evidence(
        document,
        "knev_demo_compaction_delays_infiltration",
        quote,
    )
    related_evidence = _graph_evidence(
        document,
        "knev_demo_compaction_common_in_traffic_area",
        related_quote,
    )
    return TraversedPath(
        nodes=[
            KnowledgeNode(
                node_id="kn_demo_wisepen_rag",
                label="WisePen RAG",
                kind=KnowledgeNodeKind.ENTITY,
                entity_type=KnowledgeEntityType.PRODUCT,
            ),
            KnowledgeNode(
                node_id="kn_demo_graphrag",
                label="GraphRAG",
                kind=KnowledgeNodeKind.ENTITY,
                entity_type=KnowledgeEntityType.TECHNOLOGY,
            ),
            KnowledgeNode(
                node_id="kn_demo_knowledge_graph",
                label="知识图谱",
                kind=KnowledgeNodeKind.ENTITY,
                entity_type=KnowledgeEntityType.CONCEPT,
            ),
        ],
        edges=[
            TraversedEdge(
                edge_id="ke_demo_wisepen_uses_graphrag",
                source_node_id="kn_demo_wisepen_rag",
                target_node_id="kn_demo_graphrag",
                relation_type=KnowledgeRelationType.USES,
                evidence=[relation_evidence],
            ),
            TraversedEdge(
                edge_id="ke_demo_graphrag_uses_knowledge_graph",
                source_node_id="kn_demo_graphrag",
                target_node_id="kn_demo_knowledge_graph",
                relation_type=KnowledgeRelationType.USES,
                evidence=[related_evidence],
            ),
        ],
    )


def _graph_mentions(document: DemoDocument) -> list[KnowledgeMention]:
    return [
        KnowledgeMention(
            mention_id="knm_demo_graphrag",
            node_id="kn_demo_graphrag",
            evidence=_graph_evidence(
                document,
                "knev_demo_graphrag_mention",
                "GraphRAG 技术",
            ),
        ),
        KnowledgeMention(
            mention_id="knm_demo_knowledge_graph",
            node_id="kn_demo_knowledge_graph",
            evidence=_graph_evidence(
                document,
                "knev_demo_knowledge_graph_mention",
                "知识图谱",
            ),
        ),
    ]


def _graph_evidence(
    document: DemoDocument,
    evidence_id: str,
    quote: str,
) -> GraphEvidence:
    start = document.markdown.index(quote)
    end = start + len(quote)
    block = next(
        block
        for block in document.reading_blocks
        if any(
            start >= span.start_offset and end <= span.end_offset
            for span in block.source_spans
        )
    )
    return GraphEvidence(
        evidence_id=evidence_id,
        resource_id=document.resource_id,
        content_revision=document.revision.content_revision,
        reading_block_id=block.block_id,
        source_span=SourceSpan(start, end),
        quote=quote,
    )


def _write_outputs(
    *,
    sectioned,
    flat_text,
    sectioned_payload,
    flat_payload,
    graph_payload,
    graph_quote,
    graph_related_quote,
) -> None:
    directory = Path(__file__).parent
    locate_output = "\n".join(
        [
            "=== Review notes ===",
            "- 文档先经过真实 Section/ReadingBlock/RetrievalChunk/SourceRef 构造链。",
            "- embedding 与 Qdrant 召回用确定性 fake；WeightedRRF、相关性 gate、ACL、revision 过滤、VERIFY 和提升算法使用生产实现。",
            "- sectioned 与 flat text 都从 RetrievalChunk 命中提升为完整 ReadingBlock。",
            "- flat text 不抽取知识图，因此 nodes 为空，但仍可作为确定性 Section READ 锚点。",
            "",
            "=== SECTIONED source text ===",
            sectioned.markdown,
            "=== SECTIONED locate ===",
            json.dumps(sectioned_payload, ensure_ascii=False, indent=2),
            "",
            "=== FLAT_TEXT source text ===",
            flat_text.markdown,
            "=== FLAT_TEXT locate ===",
            json.dumps(flat_payload, ensure_ascii=False, indent=2),
        ]
    )
    graph_output = "\n".join(
        [
            "=== Review notes ===",
            "- Neo4j traversal 和 LLM 图谱抽取结果用项目预定义实体类型表示。",
            "- seed 校验、BM25/WeightedRRF 路径排序、GraphEvidenceVerifier、状态扩展和 ReadingBlock 回补使用生产实现。",
            "- LOCATE、关系证据和新节点 mention 分别来自不同 ReadingBlock，EXPAND 不复述检索命中字段。",
            "- flat text 在生产索引阶段跳过图谱抽取，因此没有伪造 expandGraph 结果。",
            "",
            "=== Simulated extracted graph fact ===",
            f"quote: {graph_quote}",
            f"quote: {graph_related_quote}",
            "entity anchors: kn_demo_wisepen_rag(product), kn_demo_graphrag(technology), kn_demo_knowledge_graph(concept)",
            "relations:",
            "  WisePen RAG -[USES]-> GraphRAG",
            "  GraphRAG -[USES]-> 知识图谱",
            "",
            "=== SECTIONED expandGraph ===",
            json.dumps(graph_payload, ensure_ascii=False, indent=2),
            "",
            "=== FLAT_TEXT graph behavior ===",
            "structure_mode: flat_text",
            "graph extraction: skipped",
            "expandGraph: not applicable",
        ]
    )
    locate_path = directory / "locate_demo_output.txt"
    graph_path = directory / "graph_expand_demo_output.txt"
    locate_path.write_text(locate_output, encoding="utf-8")
    graph_path.write_text(graph_output, encoding="utf-8")
    print(locate_path)
    print(graph_path)


def _acl(resource_id: str) -> ResourceAcl:
    return ResourceAcl(
        resource_id=resource_id,
        acl_revision=1,
        owner_id="demo-reviewer",
    )


if __name__ == "__main__":
    asyncio.run(main())
