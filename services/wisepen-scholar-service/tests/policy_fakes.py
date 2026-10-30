"""用例测试显式提供策略，不依赖线上 Nacos 或文档中的配置内容。"""

from copy import deepcopy

from scholar.core.config.policy_settings import ScholarPolicyStore
from scholar.domain.models.fields import FieldPolicy, ScholarPolicies
from scholar.domain.models.fields import AcademicField


TEST_FIELD_POLICIES = {
    AcademicField.GENERAL: FieldPolicy(["openalex", "arxiv"]),
    AcademicField.COMPUTER_SCIENCE: FieldPolicy(
        ["arxiv", "huggingface"], ["huggingface"], "huggingface"
    ),
    AcademicField.MEDICINE: FieldPolicy(["openalex"]),
    AcademicField.BIOLOGY: FieldPolicy(["openalex", "arxiv"]),
    AcademicField.ENGINEERING: FieldPolicy(["openalex", "arxiv"]),
    AcademicField.ECONOMICS_BUSINESS: FieldPolicy(["openalex", "arxiv"]),
    AcademicField.MATHEMATICS: FieldPolicy(["arxiv", "openalex"]),
    AcademicField.PHYSICS: FieldPolicy(["arxiv", "openalex"]),
    AcademicField.CHEMISTRY: FieldPolicy(["openalex", "arxiv"]),
    AcademicField.PSYCHOLOGY: FieldPolicy(["openalex"]),
    AcademicField.SOCIAL_SCIENCES: FieldPolicy(["openalex", "arxiv"]),
    AcademicField.ARTS_HUMANITIES: FieldPolicy(["openalex"]),
    AcademicField.LAW: FieldPolicy(["openalex"]),
    AcademicField.MATERIALS_ENVIRONMENTAL_SCIENCES: FieldPolicy(["openalex", "arxiv"]),
}


def make_policy_store(fields=None, *, common_enrichers=None):
    return ScholarPolicyStore(ScholarPolicies(
        common_enrichers=["arxiv", "s2", "openalex"] if common_enrichers is None else common_enrichers,
        fields=deepcopy(TEST_FIELD_POLICIES | ({} if fields is None else fields)),
    ))
