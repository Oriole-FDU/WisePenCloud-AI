from enum import StrEnum


class ConvertSourceFormat(StrEnum):
    """文档转换源格式枚举，定义受支持的输入文件格式。"""
    MARKDOWN = "markdown"
    PLAIN_TEXT = "plain_text"
    HTML = "html"
    PDF = ".pdf"
    DOCX = ".docx"
    DOCM = ".docm"
    PPTX = ".pptx"
    PPTM = ".pptm"
    EPUB = ".epub"
    XLSX = ".xlsx"
    XLS = ".xls"
    XLSM = ".xlsm"
    ODS = ".ods"



