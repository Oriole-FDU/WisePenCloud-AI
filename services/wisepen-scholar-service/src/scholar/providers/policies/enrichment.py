"""按来源注册内部富化策略和出口投影；供应商字段语义由各 provider 实现。"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from scholar.domain.models.identifiers import IdentifierKind, PaperIdentifier
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.paper_lookup_cache import CachedPaperLookup
from scholar.providers import arxiv, huggingface, openalex, s2


@dataclass
class EnrichmentPolicy:
    """来源能力、内部完整性策略和模型可见 metadata 的投影入口。"""

    identifier_kinds: list[IdentifierKind]
    project_metadata: Callable[[Paper], dict[str, Any]]
    has_signals: Callable[[dict[str, Any]], bool] | None = None
    aliases: Callable[[dict[str, Any]], list[PaperIdentifier]] | None = None
    short_lived: Callable[[CachedPaperLookup], bool] | None = None
    # 用于区分列表半成品（如 HF 搜索缺产物字段）与完整详情；
    # 字段存在（即使值为 None 也属已确认事实）即视为齐全，避免因空值反复请求详情。
    required_fields: list[str] = field(default_factory=list)


ENRICHMENT_POLICIES = {
    "arxiv": EnrichmentPolicy(
        identifier_kinds=[IdentifierKind.ARXIV, IdentifierKind.DOI],
        project_metadata=arxiv.project_metadata,
        has_signals=arxiv.has_signals,
    ),
    "s2": EnrichmentPolicy(
        identifier_kinds=[
            IdentifierKind.ARXIV,
            IdentifierKind.DOI,
            IdentifierKind.CORPUS_ID,
            IdentifierKind.S2,
        ],
        project_metadata=s2.project_metadata,
        has_signals=s2.has_signals,
        aliases=s2.aliases,
        short_lived=s2.short_lived,
    ),
    "openalex": EnrichmentPolicy(
        identifier_kinds=[
            IdentifierKind.OPENALEX, IdentifierKind.DOI, IdentifierKind.ARXIV
        ],
        project_metadata=openalex.project_metadata,
        has_signals=openalex.has_signals,
        aliases=openalex.aliases,
    ),
    "huggingface": EnrichmentPolicy(
        identifier_kinds=[IdentifierKind.ARXIV],
        project_metadata=huggingface.project_metadata,
        has_signals=huggingface.has_signals,
        # HF 搜索列表只返回概要；通过该字段集判断是否已获取完整产物详情（其他通用源数据一次性给全故无需配置）
        required_fields=[
            "ai_summary", "ai_keywords", "upvotes", "github_repo", "github_stars",
            "linked_models", "linked_datasets",
        ],
    ),
}

# 兼容别名
EnrichmentRules = EnrichmentPolicy
ENRICHMENT_RULES = ENRICHMENT_POLICIES
