"""Page、Section 和标题树 HTTP 输入与输出。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rag.application.reading import SectionReadMode

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ReadPagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: NonEmptyText
    page_labels: list[NonEmptyText] = Field(min_length=1, max_length=20)


class ReadPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_label: str
    content: str


class ReadPagesResponse(BaseModel):
    resource_id: str
    pages: list[ReadPageResponse]


class ReadSectionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_ids: list[NonEmptyText] = Field(min_length=1, max_length=20)
    mode: SectionReadMode = SectionReadMode.DIRECT
    max_depth: int = Field(default=1, ge=0)


class ReadSectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource_id: str
    section_id: str
    section_path: str
    content: str


class ReadSectionsResponse(BaseModel):
    sections: list[ReadSectionResponse]


class GetNeighborhoodRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_ids: list[NonEmptyText] = Field(min_length=1, max_length=20)
    sibling_steps: int = Field(default=1, ge=0, le=5)


class NeighborhoodResponse(BaseModel):
    """当前 Section 的最小定位信息与 Markdown 邻域目录。"""

    model_config = ConfigDict(from_attributes=True)

    resource_id: str
    section_id: str
    section_path: str
    outline: str


class GetNeighborhoodResponse(BaseModel):
    items: list[NeighborhoodResponse]


class GetGlobalOutlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: NonEmptyText
    max_level: int = Field(default=2, ge=0)


class GetGlobalOutlineResponse(BaseModel):
    resource_id: str
    outline: str
