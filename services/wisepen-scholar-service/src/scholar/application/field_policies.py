"""领域能力配置；分类过滤仍由 providers/field_mapping.py 统一维护。"""

from dataclasses import dataclass, field

from scholar.domain.models.fields import AcademicField
from scholar.domain.models.paper import PaperSource


@dataclass
class FieldPolicy:
    """计划中的来源均须实际执行；专有富化与发现独立于检索计划。"""

    default_search_plan: list[str]
    without_credentials_plan: list[str] | None = None
    multi_source_plan: list[str] | None = None
    search_enrichers: list[str] = field(default_factory=list)
    discoverer: str | None = None


# 这里显式登记计划，不从“有 arXiv 分类”推断多源能力。
# 搜索 S2 绑定只有公共缓存读取能力；在线 S2 仅注入 lookup 和独立 ETL。
FIELD_POLICIES = {
    AcademicField.GENERAL: FieldPolicy(
        default_search_plan=[PaperSource.OPENALEX],
        without_credentials_plan=[PaperSource.ARXIV],
        multi_source_plan=[PaperSource.ARXIV, PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.COMPUTER_SCIENCE: FieldPolicy(
        default_search_plan=[PaperSource.ARXIV],
        multi_source_plan=[PaperSource.ARXIV, PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2, PaperSource.HUGGINGFACE],
        discoverer=PaperSource.HUGGINGFACE,
    ),
    AcademicField.MEDICINE: FieldPolicy(
        default_search_plan=[PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.BIOLOGY: FieldPolicy(
        default_search_plan=[PaperSource.OPENALEX],
        without_credentials_plan=[PaperSource.ARXIV],
        multi_source_plan=[PaperSource.ARXIV, PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.ENGINEERING: FieldPolicy(
        default_search_plan=[PaperSource.OPENALEX],
        without_credentials_plan=[PaperSource.ARXIV],
        multi_source_plan=[PaperSource.ARXIV, PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.ECONOMICS_BUSINESS: FieldPolicy(
        default_search_plan=[PaperSource.OPENALEX],
        without_credentials_plan=[PaperSource.ARXIV],
        multi_source_plan=[PaperSource.ARXIV, PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.MATHEMATICS: FieldPolicy(
        default_search_plan=[PaperSource.ARXIV],
        multi_source_plan=[PaperSource.ARXIV, PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.PHYSICS: FieldPolicy(
        default_search_plan=[PaperSource.ARXIV],
        multi_source_plan=[PaperSource.ARXIV, PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.CHEMISTRY: FieldPolicy(
        default_search_plan=[PaperSource.OPENALEX],
        without_credentials_plan=[PaperSource.ARXIV],
        multi_source_plan=[PaperSource.ARXIV, PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.PSYCHOLOGY: FieldPolicy(
        default_search_plan=[PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.SOCIAL_SCIENCES: FieldPolicy(
        default_search_plan=[PaperSource.OPENALEX],
        without_credentials_plan=[PaperSource.ARXIV],
        multi_source_plan=[PaperSource.ARXIV, PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.ARTS_HUMANITIES: FieldPolicy(
        default_search_plan=[PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.LAW: FieldPolicy(
        default_search_plan=[PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
    AcademicField.MATERIALS_ENVIRONMENTAL_SCIENCES: FieldPolicy(
        default_search_plan=[PaperSource.OPENALEX],
        without_credentials_plan=[PaperSource.ARXIV],
        multi_source_plan=[PaperSource.ARXIV, PaperSource.OPENALEX],
        search_enrichers=[PaperSource.ARXIV, PaperSource.S2],
    ),
}
