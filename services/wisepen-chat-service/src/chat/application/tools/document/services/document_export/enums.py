from enum import StrEnum


class ExportSourceFormat(StrEnum):
    """导出源格式枚举，控制输入内容的解析方式。"""
    MARKDOWN = "markdown"
    PLAIN_TEXT = "plain_text"

class ExportFormat(StrEnum):
    """导出目标格式枚举，包含内容类型和后缀映射。"""
    MARKDOWN = "markdown"
    HTML = "html"
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"

    @property
    def content_type(self) -> str:
        """返回对应格式的 HTTP Content-Type。"""
        if self == ExportFormat.MARKDOWN:
            return "text/markdown; charset=utf-8"
        if self == ExportFormat.HTML:
            return "text/html; charset=utf-8"
        if self == ExportFormat.PDF:
            return "application/pdf"
        if self == ExportFormat.DOCX:
            return (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            )
        return "text/plain; charset=utf-8"

    @property
    def extension(self) -> str:
        """返回对应格式的文件扩展名（含点）。"""
        if self == ExportFormat.MARKDOWN:
            return ".md"
        return f".{self.value}"
