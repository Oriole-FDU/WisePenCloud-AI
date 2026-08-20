"""READ endpoints 的请求与响应 schema。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rag.application.rag.read.content import SectionContentView

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: NonEmptyText


class ReadPagesRequest(ResourceRequest):
    page_labels: list[NonEmptyText] = Field(min_length=1, max_length=20)


class ReadSectionsRequest(ResourceRequest):
    section_ids: list[NonEmptyText] = Field(min_length=1, max_length=20)


ReadPagesResponse = dict[str, str]
ReadSectionsResponse = dict[str, SectionContentView]


class SurroundingOutlineRequest(ResourceRequest):
    section_id: NonEmptyText
    window_size: int = Field(default=2, ge=0, le=5)


class SectionMetadataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    section_id: str
    title: str
    section_path: str
    has_children: bool
    page_range: str | None = None
    anchor_labels: list[str] = Field(default_factory=list)
    is_current: bool | None = None


class SurroundingOutlineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    parent: SectionMetadataResponse | None = None
    siblings: list[SectionMetadataResponse] = Field(default_factory=list)
    children: list[SectionMetadataResponse] = Field(default_factory=list)
