from __future__ import annotations


class DocumentParseError(Exception):
    """文档解析异常基类，携带监控名称和原始异常。"""

    def __init__(
        self,
        message: str,
        *,
        parser_name: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.parser_name = parser_name
        self.cause = cause


class PrimaryParserError(DocumentParseError):
    """主解析候选内部失败。"""


class FallbackParserError(DocumentParseError):
    """MarkItDown 兜底解析失败。"""


class DocumentParseFailedError(DocumentParseError):
    """解析计划中的所有候选都失败。"""
