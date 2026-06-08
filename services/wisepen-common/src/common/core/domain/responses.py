from typing import Generic, TypeVar, Optional, Any, List
from pydantic import BaseModel

from .enums import ResultCode, IErrorCode

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    """通用分页结果，对齐 Java PageResult<T>"""
    list: List[T] = []
    total: int
    page: int
    size: int
    total_page: int

    @classmethod
    def of(cls, items: List[T], total: int, page: int, size: int) -> "PageResult[T]":
        total_page = 0 if size == 0 else -(-total // size)
        return cls(list=items, total=total, page=page, size=size, total_page=total_page)


class R(BaseModel, Generic[T]):
    """类似 Java 的 R<T> 通用返回体"""
    code: int
    msg: str
    data: Optional[T] = None

    @classmethod
    def success(cls, data: Optional[T] = None) -> "R[T]":
        return cls(code=ResultCode.SUCCESS.code, msg=ResultCode.SUCCESS.msg, data=data)

    @classmethod
    def fail(cls, error_code: IErrorCode, custom_msg: Optional[str] = None) -> "R[Any]":
        return cls(
            code=error_code.code,
            msg=custom_msg if custom_msg else error_code.msg,
            data=None
        )