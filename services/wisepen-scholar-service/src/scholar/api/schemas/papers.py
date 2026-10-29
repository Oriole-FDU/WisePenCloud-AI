"""HTTP 请求校验；封闭参数沿用完整能力契约，不创建阶段专用 API。"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scholar.application.field_policies import FIELD_POLICIES
from scholar.application.discovery import PaperDiscoveryQuery
from scholar.application.lookup import PaperLookup
from scholar.application.search import PaperSearch, SourceScope
from scholar.domain.interfaces.paper_discoverer import DiscoveryPeriod, DiscoverySort
from scholar.domain.models.fields import AcademicField


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1)
    field: AcademicField = AcademicField.GENERAL
    published_after: date | None = None
    published_before: date | None = None
    limit: int = Field(default=10, ge=1, le=20)
    source_scope: SourceScope = SourceScope.DEFAULT

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @model_validator(mode="after")
    def date_range(self):
        if (
            self.published_after
            and self.published_before
            and self.published_after > self.published_before
        ):
            raise ValueError("published_after must not exceed published_before")
        return self

    def to_query(self) -> PaperSearch:
        return PaperSearch(**self.model_dump())


class LookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifiers: list[str] = Field(min_length=1, max_length=20)
    field: AcademicField = AcademicField.GENERAL

    def to_query(self) -> PaperLookup:
        return PaperLookup(**self.model_dump())


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
