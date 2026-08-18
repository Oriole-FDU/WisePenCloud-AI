"""READ endpoints 的请求与响应 schema。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from common.utils.document import OutlineNode

from rag.application.rag.read.content import SectionContentView

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1)
]


class ResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: NonEmptyText


class PageContentRequest(ResourceRequest):
    page_labels: list[NonEmptyText] = Field(min_length=1, max_length=20)


class SectionContentRequest(ResourceRequest):
    section_ids: list[NonEmptyText] = Field(min_length=1, max_length=20)
    # False 时响应保留完整 navigation 但省略 text，用于轻量级目录漫游。
    include_body: bool = True
    # 导航方向黑名单（parent/previous/next/children）；未知方向静默忽略。
    exclude_directions: list[str] = Field(default_factory=list, max_length=4)


PageContentResponse = dict[str, str]
SectionContentResponse = dict[str, SectionContentView]


class DocumentOutlineResponse(BaseModel):
    resource_id: str
    document_version: int
    content_revision: str
    total_length: int
    outline: list[OutlineNode]
