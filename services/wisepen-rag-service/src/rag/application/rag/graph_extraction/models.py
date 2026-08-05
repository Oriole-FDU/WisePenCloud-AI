from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rag.application.rag.ingestion import RagSourceRef
from rag.utils.chunkers import SourceSpan


class KnowledgeNodeKind(StrEnum):
    """知识图谱节点种类。"""

    ENTITY = "Entity"  # 通用实体，可跨文档导航。
    RESOURCE = "Resource"  # 当前私有资源，作为文档级根节点。
    EXTERNAL_SOURCE = "ExternalSource"  # 文中提及但尚未解析为私有资源的来源。


class KnowledgeEntityType(StrEnum):
    """通用实体的语义类型。"""

    CONCEPT = "concept"  # 抽象概念。
    PERSON = "person"  # 人物。
    ORGANIZATION = "organization"  # 组织/机构。
    PRODUCT = "product"  # 产品。
    TECHNOLOGY = "technology"  # 技术/框架/工具。
    METHOD = "method"  # 方法/流程。
    DATASET = "dataset"  # 数据集。
    EVENT = "event"  # 事件。
    PLACE = "place"  # 地点。
    DOCUMENT = "document"  # 文档/出版物。
    OTHER = "other"  # 不属于以上类别的实体。


class KnowledgeRelationProfile(StrEnum):
    """关系类型所属的 profile，用于按场景裁剪 schema。"""

    CORE = "core"  # 通用知识图谱场景。
    LEARNING = "learning"  # 教材/讲义场景。
    SCHOLARLY = "scholarly"  # 学术/论文场景。


class KnowledgeRelationType(StrEnum):
    """业务定义的全部关系类型。"""

    MENTIONS = "MENTIONS"  # 主体引用客体。
    ABOUT = "ABOUT"  # 主体内容明确围绕客体。
    RELATED_TO = "RELATED_TO"  # 主体与客体存在正文明确描述的关系（需 predicate 限定）。
    PART_OF = "PART_OF"  # 主体是客体的组成部分。
    USES = "USES"  # 主体明确使用客体。
    PRODUCES = "PRODUCES"  # 主体明确产生客体。
    DEPENDS_ON = "DEPENDS_ON"  # 主体明确依赖客体。
    DERIVED_FROM = "DERIVED_FROM"  # 主体明确来源于客体。
    IMPLEMENTS = "IMPLEMENTS"  # 主体实现客体。
    APPLIES_TO = "APPLIES_TO"  # 主体适用于客体。
    CAUSES = "CAUSES"  # 主体导致客体。
    COMPARES_WITH = "COMPARES_WITH"  # 正文明确比较主体和客体。
    CONTRADICTS = "CONTRADICTS"  # 正文明确指出主体和客体冲突。
    EXTENDS = "EXTENDS"  # 主体扩展客体。
    SUPERSEDES = "SUPERSEDES"  # 主体替代客体。
    LOCATED_IN = "LOCATED_IN"  # 主体位于客体地点。
    AUTHORED_BY = "AUTHORED_BY"  # 主体由客体人物或组织创作。
    DEFINES = "DEFINES"  # 主体给出客体的正式定义。
    EXPLAINS = "EXPLAINS"  # 主体解释或推导客体。
    EXAMPLE_OF = "EXAMPLE_OF"  # 主体是客体的实例。
    REQUIRES = "REQUIRES"  # 理解或使用主体需要客体。
    CITES = "CITES"  # 主体明确引用客体来源。
    PUBLISHED_IN = "PUBLISHED_IN"  # 主体发表于客体。
    USES_DATASET = "USES_DATASET"  # 主体使用客体数据集。
    USES_METHOD = "USES_METHOD"  # 主体使用客体方法。
    SUPPLEMENTS = "SUPPLEMENTS"  # 主体补充客体文档。
    RETRACTS = "RETRACTS"  # 主体撤销客体先前声明。


class KnowledgeAssertion(StrEnum):
    """关系的断言状态。"""

    AFFIRMED = "affirmed"  # 正文明确肯定。
    NEGATED = "negated"  # 正文明确否定。
    CONDITIONAL = "conditional"  # 仅在特定条件成立时存在。
    UNCERTAIN = "uncertain"  # 表达不确定或推测。


@dataclass(frozen=True, slots=True)
class KnowledgeExtractionBlock:
    """图抽取使用的父级阅读块视图。"""

    block_id: str
    block_index: int
    section_id: str
    section_path: tuple[str, ...]
    raw_text: str
    source_spans: tuple[SourceSpan, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeExtractionSource:
    """当前 applied revision 的图抽取输入。"""

    resource_id: str
    document_title: str
    document_version: int
    content_revision: str
    markdown: str
    blocks: tuple[KnowledgeExtractionBlock, ...]
    source_refs: tuple[RagSourceRef, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeWindowSourceSpan:
    """窗口内父块 raw_text 区间到原文 Markdown 区间的双向映射。"""

    local_start: int  # 在父块 raw_text 中的起始 offset。
    local_end: int  # 在父块 raw_text 中的结束 offset。
    source_start: int  # 在原始 Markdown 中的起始 offset。
    source_end: int  # 在原始 Markdown 中的结束 offset。


@dataclass(frozen=True, slots=True)
class KnowledgeExtractionWindow:
    """知识抽取窗口，作为 LLM 的最小工作单元。"""

    resource_id: str  # 当前私有资源 ID。
    document_title: str  # 从标题树推导出的文档标题，缺失时为空。
    document_version: int  # 资源文档版本号。
    content_revision: str  # 内容投影的版本哈希。
    parent_id: str  # 窗口对应的 Section ReadingBlock ID。
    parent_index: int  # 父块在文档中的顺序。
    window_id: str  # 本次 SDK 调用的窗口 ID；parent 内滑窗时与 parent_id 分离。
    window_index: int  # 同一父块内的滑窗顺序。
    current_text: str  # 窗口内的父块 raw_text（local_start/local_end 坐标基准）。
    source_mappings: tuple[KnowledgeWindowSourceSpan, ...]  # local -> source 区间映射。
    source_refs: tuple[RagSourceRef, ...]  # 父块覆盖的 SourceRef，用于 evidence 落位。
    section_paths: tuple[tuple[str, ...], ...] = ()  # 窗口所在 section 路径集合。
    previous_context: str = ""  # 同 section 上一父块末尾上下文，仅用于消歧。
    next_context: str = ""  # 同 section 下一父块开头上下文，仅用于消歧。


@dataclass(frozen=True, slots=True)
class KnowledgeEvidence:
    """知识图谱节点或关系在原文中的精确证据。"""

    evidence_ref_id: str  # evidence 的稳定 ID，跨抽取运行保持一致。
    source_ref_ids: tuple[str, ...]  # 覆盖该 evidence 的 SourceRef ID。
    parent_id: str  # evidence 所属 Section ReadingBlock。
    quote: str  # 已按原文 offset 校验的连续证据文本。


@dataclass(frozen=True, slots=True)
class ExtractedKnowledgeNode:
    """单窗口内由 SDK 抽取并经 Mapper 校验后的节点。"""

    local_id: str  # 节点在当前窗口中的本地 ID（通常为 parent_id:UUID）。
    kind: KnowledgeNodeKind  # 节点种类。
    label: str  # 节点展示名。
    entity_type: KnowledgeEntityType | None = None  # 实体类型；Resource 与 ExternalSource 不设置。
    evidence: KnowledgeEvidence | None = None  # 节点在原文中的证据；Resource 节点不需要。


@dataclass(frozen=True, slots=True)
class ExtractedKnowledgeRelation:
    """单窗口内的关系，带有证据和断言状态。"""

    source_local_id: str  # 关系源节点的 local_id。
    target_local_id: str  # 关系目标节点的 local_id。
    relation_type: KnowledgeRelationType  # 关系类型。
    evidence: KnowledgeEvidence  # 关系的精确原文证据。
    predicate: str | None = None  # RELATED_TO 的具体谓词；其他关系为 None。


@dataclass(frozen=True, slots=True)
class KnowledgeWindowExtraction:
    """一个抽取窗口的完整结果：经过校验的节点与关系。"""

    window: KnowledgeExtractionWindow  # 对应的抽取窗口。
    nodes: tuple[ExtractedKnowledgeNode, ...]  # 窗口内通过校验的节点。
    relations: tuple[ExtractedKnowledgeRelation, ...]  # 窗口内通过校验的关系。
