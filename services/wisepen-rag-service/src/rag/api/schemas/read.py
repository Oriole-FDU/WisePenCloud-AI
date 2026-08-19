"""READ endpoints 的请求与响应 schema。"""

from typing import Annotated

from common.utils.document import OutlineNode
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

# 仅保留 Python 导入层别名，HTTP 路径和公开 schema 使用 read* 身份。
PageContentRequest = ReadPagesRequest
PageContentResponse = ReadPagesResponse
SectionContentRequest = ReadSectionsRequest
SectionContentResponse = ReadSectionsResponse


class DocumentOutlineRequest(ResourceRequest):
    root_section_id: NonEmptyText | None = None
    depth: int | None = Field(default=None, ge=0, le=20)


class DocumentOutlineResponse(BaseModel):
    resource_id: str
    document_version: int
    content_revision: str
    total_length: int
    outline: list[OutlineNode]
