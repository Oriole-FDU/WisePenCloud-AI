from __future__ import annotations

from .models import (
    KnowledgeNodeKind,
    KnowledgeRelationProfile,
    KnowledgeRelationType,
)

# 基础通用关系：适用于大多数文档知识图谱场景。
CORE_RELATIONS: dict[KnowledgeRelationType, str] = {
    KnowledgeRelationType.ABOUT: "主体内容明确围绕客体",
    KnowledgeRelationType.RELATED_TO: "主体与客体存在正文明确描述的关系",
    KnowledgeRelationType.PART_OF: "主体是客体的组成部分",
    KnowledgeRelationType.USES: "主体明确使用客体",
    KnowledgeRelationType.PRODUCES: "主体明确产生客体",
    KnowledgeRelationType.DEPENDS_ON: "主体明确依赖客体",
    KnowledgeRelationType.DERIVED_FROM: "主体明确来源于客体",
    KnowledgeRelationType.IMPLEMENTS: "主体实现客体",
    KnowledgeRelationType.APPLIES_TO: "主体适用于客体",
    KnowledgeRelationType.CAUSES: "主体导致客体",
    KnowledgeRelationType.COMPARES_WITH: "正文明确比较主体和客体",
    KnowledgeRelationType.CONTRADICTS: "正文明确指出主体和客体冲突",
    KnowledgeRelationType.EXTENDS: "主体扩展客体",
    KnowledgeRelationType.SUPERSEDES: "主体替代客体",
    KnowledgeRelationType.LOCATED_IN: "主体位于客体地点",
    KnowledgeRelationType.AUTHORED_BY: "主体由客体人物或组织创作",
}

# 学习型关系：更偏向教材、论文、知识库中的解释关系。
LEARNING_RELATIONS: dict[KnowledgeRelationType, str] = {
    KnowledgeRelationType.DEFINES: "主体给出客体的正式定义",
    KnowledgeRelationType.EXPLAINS: "主体解释或推导客体",
    KnowledgeRelationType.EXAMPLE_OF: "主体是客体的实例",
    KnowledgeRelationType.REQUIRES: "理解或使用主体需要客体",
}

# 学术关系：面向论文、研究资料等文档。
SCHOLARLY_RELATIONS: dict[KnowledgeRelationType, str] = {
    KnowledgeRelationType.CITES: "主体明确引用客体来源",
    KnowledgeRelationType.PUBLISHED_IN: "主体发表于客体",
    KnowledgeRelationType.USES_DATASET: "主体使用客体数据集",
    KnowledgeRelationType.USES_METHOD: "主体使用客体方法",
    KnowledgeRelationType.SUPPLEMENTS: "主体补充客体文档",
}

# 关系 -> 类型映射，用于根据关系反查所属 profile。
RELATION_PROFILES: dict[KnowledgeRelationType, KnowledgeRelationProfile] = {
    **{relation: KnowledgeRelationProfile.CORE for relation in CORE_RELATIONS},
    **{relation: KnowledgeRelationProfile.LEARNING for relation in LEARNING_RELATIONS},
    **{relation: KnowledgeRelationProfile.SCHOLARLY for relation in SCHOLARLY_RELATIONS},
}

# Resource 节点允许作为 source 的特殊关系。
# Resource -> Entity 是文档级知识入口关系。
_RESOURCE_TO_ENTITY = frozenset(
    {
        KnowledgeRelationType.ABOUT,
        KnowledgeRelationType.AUTHORED_BY,
        KnowledgeRelationType.DEFINES,
        KnowledgeRelationType.EXPLAINS,
        KnowledgeRelationType.EXAMPLE_OF,
    }
)


def relation_descriptions(profiles: frozenset[KnowledgeRelationProfile]) -> dict[KnowledgeRelationType, str]:
    """根据启用的关系类型返回对应关系描述。"""
    descriptions: dict[KnowledgeRelationType, str] = {}

    if KnowledgeRelationProfile.CORE in profiles:
        descriptions.update(CORE_RELATIONS)
    if KnowledgeRelationProfile.LEARNING in profiles:
        descriptions.update(LEARNING_RELATIONS)
    if KnowledgeRelationProfile.SCHOLARLY in profiles:
        descriptions.update(SCHOLARLY_RELATIONS)

    return descriptions


def relation_pattern_allowed(
        source: KnowledgeNodeKind, relation: KnowledgeRelationType, target: KnowledgeNodeKind
) -> bool:
    """检查节点类型和关系组合是否符合 schema 约束。"""
    # 普通实体之间允许建立所有声明过的关系。
    if source is KnowledgeNodeKind.ENTITY and target is KnowledgeNodeKind.ENTITY:
        return True

    # Resource 作为文档根节点，只允许有限的描述关系。
    if source is KnowledgeNodeKind.RESOURCE and target is KnowledgeNodeKind.ENTITY:
        return relation in _RESOURCE_TO_ENTITY

    # 外部来源只能通过引用/来源关系连接。
    if target is KnowledgeNodeKind.EXTERNAL_SOURCE:
        return source in (KnowledgeNodeKind.RESOURCE, KnowledgeNodeKind.ENTITY) and relation in (
            KnowledgeRelationType.CITES,
            KnowledgeRelationType.DERIVED_FROM,
        )

    return False
