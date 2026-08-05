from __future__ import annotations


class DocumentParseError(Exception):
    """文档转换稳定异常基类。"""


class ImageParseError(DocumentParseError):
    """图片解析服务调用或结果处理失败。"""


class RemoteParserError(DocumentParseError):
    """远程文档解析服务返回失败。"""


class RemoteParserTimeoutError(RemoteParserError):
    """远程文档解析服务超时。"""


class DocumentTooLargeError(DocumentParseError):
    """输入或远程解析结果超过允许大小。"""


class ArchiveExtractionError(DocumentParseError):
    """压缩包不安全、损坏或解压失败。"""
