from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from chat.application.tools.document.services.document_parse.enums import DocumentType, PageType


@dataclass(slots=True)
class ParsedTable:
    """文档解析得到的结构化表格。"""

    table_id: str
    source: str
    rows: List[List[str]]
    page_index: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedPage:
    """文档解析得到的单页结果。"""

    page_index: int
    text: str
    page_type: PageType
    tables: List[ParsedTable] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DocumentParseResult:
    """文档解析工具的统一结果。"""

    text: str
    source: str
    file_type: DocumentType
    pages: List[ParsedPage] = field(default_factory=list)
    tables: List[ParsedTable] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@dataclass(slots=True)
class DocumentParseResultItem:
    """批量解析结果中单条记录的包装，包含 file_ref、成功标志及结果/错误信息。"""
    file_ref: str
    success: bool
    result: Optional[DocumentParseResult] = None
    error: Optional[str] = None
