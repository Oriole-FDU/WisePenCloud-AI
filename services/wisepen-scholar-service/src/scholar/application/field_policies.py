"""领域能力配置；分类过滤仍由 providers/field_mapping.py 统一维护。"""

from dataclasses import dataclass, field

from scholar.domain.models.fields import AcademicField
from scholar.domain.models.paper import PaperSource


@dataclass
class FieldPolicy:
    """检索来源按优先级排列；富化列表只登记领域专用增量能力。"""

    searchers: list[str]
    # 平台不融合同名字段、不做语义去重；provider 应保证键唯一并提供增量价值。
    enrichers: list[str] = field(default_factory=list)
    discoverer: str | None = None


# 通用来源互为替代，优先读取本地离线事实，再使用在线来源。
COMMON_ENRICHERS = [PaperSource.ARXIV, PaperSource.S2, PaperSource.OPENALEX]

FIELD_POLICIES = {
    AcademicField.GENERAL: FieldPolicy([PaperSource.OPENALEX, PaperSource.ARXIV]),
    AcademicField.COMPUTER_SCIENCE: FieldPolicy(
        [PaperSource.ARXIV, PaperSource.OPENALEX],
        enrichers=[PaperSource.HUGGINGFACE],
        discoverer=PaperSource.HUGGINGFACE,
    ),
    AcademicField.MEDICINE: FieldPolicy([PaperSource.OPENALEX]),
    AcademicField.BIOLOGY: FieldPolicy([PaperSource.OPENALEX, PaperSource.ARXIV]),
    AcademicField.ENGINEERING: FieldPolicy([PaperSource.OPENALEX, PaperSource.ARXIV]),
    AcademicField.ECONOMICS_BUSINESS: FieldPolicy([PaperSource.OPENALEX, PaperSource.ARXIV]),
    AcademicField.MATHEMATICS: FieldPolicy([PaperSource.ARXIV, PaperSource.OPENALEX]),
    AcademicField.PHYSICS: FieldPolicy([PaperSource.ARXIV, PaperSource.OPENALEX]),
    AcademicField.CHEMISTRY: FieldPolicy([PaperSource.OPENALEX, PaperSource.ARXIV]),
    AcademicField.PSYCHOLOGY: FieldPolicy([PaperSource.OPENALEX]),
    AcademicField.SOCIAL_SCIENCES: FieldPolicy([PaperSource.OPENALEX, PaperSource.ARXIV]),
    AcademicField.ARTS_HUMANITIES: FieldPolicy([PaperSource.OPENALEX]),
    AcademicField.LAW: FieldPolicy([PaperSource.OPENALEX]),
    AcademicField.MATERIALS_ENVIRONMENTAL_SCIENCES: FieldPolicy(
        [PaperSource.OPENALEX, PaperSource.ARXIV]
    ),
}
