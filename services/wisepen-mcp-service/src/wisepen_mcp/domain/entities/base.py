from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """MCP 服务内所有请求/响应实体的基类
    Java 后端 DTO 使用 camelCase，Python 侧使用 snake_case
    本基类通过 alias_generator 自动双向转换，并允许按字段名或别名填充，忽略后端多出的字段
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


T = TypeVar("T")


class PageResult(ApiModel, Generic[T]):
    """后端 PageR<T> 的分页结果
    `list` 字段映射为 `items` 以避开内置名
    """

    items: list[T] = Field(default_factory=list, alias="list")
    total: int = 0
    page: int = 1
    size: int = 20
    total_page: int = 0

