"""已发布知识图谱共享的稳定领域模型。"""

from dataclasses import dataclass, field
from enum import StrEnum

from common.utils.document import SourceSpan


class KnowledgeNodeKind(StrEnum):
    ENTITY = "Entity"
    RESOURCE = "Resource"
    EXTERNAL_SOURCE = "ExternalSource"


class KnowledgeEntityType(StrEnum):
    CONCEPT = "concept"
    PERSON = "person"
    ORGANIZATION = "organization"
    PRODUCT = "product"
    TECHNOLOGY = "technology"
    METHOD = "method"
    DATASET = "dataset"
    EVENT = "event"
    PLACE = "place"
    DOCUMENT = "document"
    OTHER = "other"


class KnowledgeRelationType(StrEnum):
    ABOUT = "ABOUT"
    RELATED_TO = "RELATED_TO"
    PART_OF = "PART_OF"
    USES = "USES"
    PRODUCES = "PRODUCES"
    DEPENDS_ON = "DEPENDS_ON"
    DERIVED_FROM = "DERIVED_FROM"
    IMPLEMENTS = "IMPLEMENTS"
    APPLIES_TO = "APPLIES_TO"
    CAUSES = "CAUSES"
    COMPARES_WITH = "COMPARES_WITH"
    CONTRADICTS = "CONTRADICTS"
    EXTENDS = "EXTENDS"
    SUPERSEDES = "SUPERSEDES"
    LOCATED_IN = "LOCATED_IN"
    AUTHORED_BY = "AUTHORED_BY"
    DEFINES = "DEFINES"
    EXPLAINS = "EXPLAINS"
    EXAMPLE_OF = "EXAMPLE_OF"
    REQUIRES = "REQUIRES"
    CITES = "CITES"
    PUBLISHED_IN = "PUBLISHED_IN"
    USES_DATASET = "USES_DATASET"
    USES_METHOD = "USES_METHOD"
    SUPPLEMENTS = "SUPPLEMENTS"
    RETRACTS = "RETRACTS"


@dataclass(slots=True)
class KnowledgeNode:
    """经过规范化和等价合并的稳定知识节点。"""

    node_id: str
    kind: KnowledgeNodeKind
    label: str
    entity_type: KnowledgeEntityType | None = None
    resource_id: str | None = None


@dataclass(slots=True)
class GraphEvidence:
    """图事实在权威 Markdown 与 ReadingBlock 中的稳定证据。"""

    evidence_id: str
    resource_id: str
    content_revision: str
    reading_block_id: str
    # Python 字符半开区间，坐标系属于当前 revision 的权威 Markdown。
    source_span: SourceSpan
    quote: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.evidence_id,
                self.resource_id,
                self.content_revision,
                self.reading_block_id,
                self.quote,
            )
        ):
            raise ValueError("graph evidence identity and quote must not be empty")
        if (
            self.source_span.end_offset - self.source_span.start_offset
            != len(self.quote)
        ):
            raise ValueError("graph evidence span length must match quote length")


@dataclass(slots=True)
class KnowledgeMention:
    """一个知识节点在当前资源权威原文中的有证据出现。"""

    mention_id: str
    node_id: str
    evidence: GraphEvidence


@dataclass(slots=True)
class KnowledgeRelation:
    """多个窗口中的等价关系及其去重后的证据。"""

    edge_id: str
    source_node_id: str
    target_node_id: str
    relation_type: KnowledgeRelationType
    evidence: list[GraphEvidence] = field(default_factory=list)
    predicate: str | None = None


@dataclass(slots=True)
class KnowledgeGraph:
    """一个内容 revision 合并完成、等待发布的资源知识图谱。"""

    resource_id: str
    content_revision: str
    graph_revision: str
    nodes: list[KnowledgeNode] = field(default_factory=list)
    mentions: list[KnowledgeMention] = field(default_factory=list)
    relations: list[KnowledgeRelation] = field(default_factory=list)


class TraversalDirection(StrEnum):
    IN = "in"
    OUT = "out"
    BOTH = "both"
