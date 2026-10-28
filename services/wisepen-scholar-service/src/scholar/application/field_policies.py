"""领域能力配置。

按学术领域声明检索来源与领域专用富化能力；
分类过滤规则仍由 ``各 provider 内部`` 统一维护。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from scholar.domain.models.fields import AcademicField
from scholar.domain.models.paper import PaperSource


@dataclass
class FieldPolicy:
    """单个学术领域的检索与富化策略。

    Attributes:
        searchers: 检索来源，按优先级从高到低排列。
        field_enrichers: 领域专用增量富化来源。平台不融合同名字段、
            不做语义去重，因此 provider 须保证键唯一且提供增量价值。
        discoverer: 可选的发现器来源，用于拓展检索。
    """

    searchers: list[str]
    field_enrichers: list[str] = field(default_factory=list)
    discoverer: str | None = None


# 通用富化来源互为替代：优先读取本地离线事实，缺失时再回退到在线来源。
COMMON_ENRICHERS = [PaperSource.ARXIV, PaperSource.S2, PaperSource.OPENALEX]

# 各领域检索来源的优先级配置：同一领域内越靠前越优先。
FIELD_POLICIES = {
    # 通用 / 跨学科
    AcademicField.GENERAL: FieldPolicy([PaperSource.OPENALEX, PaperSource.ARXIV]),
    # 计算机科学：HuggingFace 同时承担检索、富化与发现
    AcademicField.COMPUTER_SCIENCE: FieldPolicy(
        [PaperSource.HUGGINGFACE, PaperSource.ARXIV],
        field_enrichers=[PaperSource.HUGGINGFACE],
        discoverer=PaperSource.HUGGINGFACE,
    ),
    # 生命科学
    AcademicField.MEDICINE: FieldPolicy([PaperSource.OPENALEX]),
    AcademicField.BIOLOGY: FieldPolicy([PaperSource.OPENALEX, PaperSource.ARXIV]),
    # 工程 / 经济
    AcademicField.ENGINEERING: FieldPolicy([PaperSource.OPENALEX, PaperSource.ARXIV]),
    AcademicField.ECONOMICS_BUSINESS: FieldPolicy(
        [PaperSource.OPENALEX, PaperSource.ARXIV],
    ),
    # 数理
    AcademicField.MATHEMATICS: FieldPolicy([PaperSource.ARXIV, PaperSource.OPENALEX]),
    AcademicField.PHYSICS: FieldPolicy([PaperSource.ARXIV, PaperSource.OPENALEX]),
    AcademicField.CHEMISTRY: FieldPolicy([PaperSource.OPENALEX, PaperSource.ARXIV]),
    # 心理 / 社科
    AcademicField.PSYCHOLOGY: FieldPolicy([PaperSource.OPENALEX]),
    AcademicField.SOCIAL_SCIENCES: FieldPolicy(
        [PaperSource.OPENALEX, PaperSource.ARXIV],
    ),
    # 人文 / 法学
    AcademicField.ARTS_HUMANITIES: FieldPolicy([PaperSource.OPENALEX]),
    AcademicField.LAW: FieldPolicy([PaperSource.OPENALEX]),
    # 材料与环境科学
    AcademicField.MATERIALS_ENVIRONMENTAL_SCIENCES: FieldPolicy(
        [PaperSource.OPENALEX, PaperSource.ARXIV],
    ),
}
