"""只暴露当前领域策略登记的论文发现能力。"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from scholar.application.discovery import PaperDiscoveryQuery
from scholar.application.field_policies import FIELD_POLICIES
from scholar.domain.interfaces.paper_discoverer import DiscoveryPeriod, DiscoverySort
from scholar.domain.models.fields import AcademicField

DiscoveryField = StrEnum(
    "DiscoveryField",
    {
        field.name.upper(): field.value
        for field, policy in FIELD_POLICIES.items()
        if policy.discoverer is not None
    },
    module=__name__,
)


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: DiscoveryField
    period: DiscoveryPeriod = DiscoveryPeriod.DAILY
    sort: DiscoverySort = DiscoverySort.TRENDING
    limit: int = Field(default=10, ge=1, le=15)

    def to_query(self) -> PaperDiscoveryQuery:
        return PaperDiscoveryQuery(
            field=AcademicField(self.field.value),
            period=self.period,
            sort=self.sort,
            limit=self.limit,
        )
