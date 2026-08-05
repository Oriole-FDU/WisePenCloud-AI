from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: NonEmptyText


class PageContentRequest(ResourceRequest):
    page_labels: tuple[NonEmptyText, ...] = Field(min_length=1, max_length=20)


class SectionContentRequest(ResourceRequest):
    section_ids: tuple[NonEmptyText, ...] = Field(min_length=1, max_length=20)
