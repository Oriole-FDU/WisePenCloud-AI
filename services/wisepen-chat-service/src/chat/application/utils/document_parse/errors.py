from __future__ import annotations


class DocumentParseError(Exception):
    """文档转换稳定异常基类。"""


class UnsupportedDocumentFormatError(DocumentParseError):
    def __init__(
        self,
        *,
        file_name: str,
        extension: str,
        mime_type: str | None,
    ) -> None:
        self.file_name = file_name
        self.extension = extension
        self.mime_type = mime_type
        super().__init__(
            f"Unsupported document format: file={file_name}, "
            f"extension={extension or '<none>'}, "
            f"mime_type={mime_type or '<unknown>'}."
        )


class DocumentDecodeError(DocumentParseError):
    """文件内容无法可靠解码为文本。"""


class DocumentParserError(DocumentParseError):
    """本地格式转换器执行失败。"""


class RemoteParserError(DocumentParseError):
    """远程文档解析服务返回失败。"""


class RemoteParserTimeoutError(RemoteParserError):
    """远程文档解析服务超时。"""


class DocumentTooLargeError(DocumentParseError):
    """输入或远程解析结果超过允许大小。"""
