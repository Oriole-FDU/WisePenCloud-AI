"""Page、Section 和标题树 HTTP 输入与输出。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rag.application.reading import SectionReadMode

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ReadPagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: NonEmptyText = Field(description="要读取的资源标识。")
    page_labels: list[NonEmptyText] = Field(
        min_length=1, max_length=20, description="按请求顺序读取的页标签，最多 20 项。"
    )


class ReadPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_label: str = Field(description="页的稳定标签。")
    content: str = Field(description="该页的 Markdown 正文。")


class ReadPagesResponse(BaseModel):
    resource_id: str = Field(description="资源标识。")
    pages: list[ReadPageResponse] = Field(description="按请求顺序返回的页面。")


class ReadSectionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_ids: list[NonEmptyText] = Field(
        min_length=1, max_length=20, description="要读取的全局 Section ID，最多 20 项。"
    )
    mode: SectionReadMode = Field(description="读取直接正文或递归正文。")
    max_depth: int = Field(default=1, ge=0, description="递归读取时允许展开的最大深度。")


class ReadSectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource_id: str = Field(description="Section 所属资源标识。")
    section_id: str = Field(description="Section 的全局稳定标识。")
    section_path: str = Field(description="从根标题到当前标题，以 ` > ` 连接的路径。")
    content: str = Field(description="Section 的 Markdown 正文。")


class ReadSectionsResponse(BaseModel):
    sections: list[ReadSectionResponse] = Field(description="按请求顺序返回的 Section。")


class GetNeighborhoodRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_ids: list[NonEmptyText] = Field(
        min_length=1, max_length=20, description="需要查找标题邻域的 Section ID。"
    )
    sibling_steps: int = Field(default=1, ge=0, le=5, description="向前后展开的兄弟标题数量。")


class NeighborhoodResponse(BaseModel):
    """当前 Section 的最小定位信息与 Markdown 邻域目录。"""

    model_config = ConfigDict(from_attributes=True)

    resource_id: str = Field(description="Section 所属资源标识。")
    section_id: str = Field(description="当前 Section 的全局稳定标识。")
    section_path: str = Field(description="当前 Section 的完整标题路径。")
    outline: str = Field(description="当前标题邻域的 Markdown 大纲。")


class GetNeighborhoodResponse(BaseModel):
    items: list[NeighborhoodResponse] = Field(description="按请求顺序返回的标题邻域。")


class GetGlobalOutlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: NonEmptyText = Field(description="要读取大纲的资源标识。")
    max_level: int = Field(default=2, ge=0, description="最大标题层级；0 表示展开全部层级。")


class GetGlobalOutlineResponse(BaseModel):
    resource_id: str = Field(description="资源标识。")
    outline: str = Field(description="资源的 Markdown 标题大纲。")
