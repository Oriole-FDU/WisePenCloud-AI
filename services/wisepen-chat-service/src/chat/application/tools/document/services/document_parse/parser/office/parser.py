from dataclasses import replace
from pathlib import Path
from typing import List, Tuple

from chat.application.tools.document.services.document_parse.enums import (
    DocumentType,
    ParserName,
)
from chat.application.tools.document.services.document_parse.errors import DocumentParseError
from chat.application.tools.document.services.document_parse.models import DocumentParseResult
from chat.application.tools.document.services.document_parse.parser.base import (
    BaseDocumentParser,
)
from chat.application.tools.document.services.document_parse.parser.office.fallback import (
    OfficeFallbackParser,
)
from chat.application.tools.document.services.document_parse.parser.office.primary import (
    OfficePrimaryParser,
)

_SUFFIX_TO_FILE_TYPE = {
    ".docx": DocumentType.DOCX,
    ".docm": DocumentType.DOCX,
    ".pptx": DocumentType.PPTX,
    ".pptm": DocumentType.PPTX,
}


class OfficeParser(BaseDocumentParser):
    """Office 文档编排解析器，优先使用 docling 解析，失败时自动降级到 MarkItDown。"""

    @property
    def name(self) -> ParserName:
        """返回编排解析器名称 `OfficeParser`。"""
        return ParserName.OFFICE

    @property
    def supported_extensions(self) -> Tuple[str, ...]:
        """支持 Word 和 PowerPoint 格式：.docx, .docm, .pptx, .pptm。"""
        return (".docx", ".docm", ".pptx", ".pptm")

    def __init__(
        self,
        *,
        primary_parser: OfficePrimaryParser,
        fallback_parser: OfficeFallbackParser,
    ):
        """初始化 OfficeParser，注入主解析器与兜底解析器。"""
        self.primary_parser = primary_parser
        self.fallback_parser = fallback_parser

    async def parse(self, path: Path) -> DocumentParseResult:
        """先使用 docling 主链路解析，失败时经 MarkItDown 兜底，最终补充编排元数据。"""
        file_type = _SUFFIX_TO_FILE_TYPE[path.suffix.lower()]
        warnings: List[str] = []

        # 主链路：优先使用 OfficePrimaryParser。
        # 失败后不直接中断，记录 warning 并进入 MarkItDown fallback。
        try:
            result = self.primary_parser.parse(path, file_type=file_type)
            selected_parser = ParserName.DOCLING
        except Exception as e:
            warnings.append(f"docling_failed: {type(e).__name__}: {e}")

            # 兜底链路：主解析器失败时使用 OfficeFallbackParser。
            # 两层都失败才对外抛 DocumentParseError。
            try:
                result = self.fallback_parser.parse(path, file_type=file_type)
                selected_parser = ParserName.MARKITDOWN
            except Exception as e:
                warnings.append(f"markitdown_failed: {type(e).__name__}: {e}")
                raise DocumentParseError(
                    "Office parsing failed after primary and fallback parsers: "
                    + " | ".join(warnings)
                ) from e

        # 统一补充 OfficeParser 的编排元数据：
        # - parser: 对外暴露的总 parser 名称。
        # - selected_parser: 本次实际成功的底层 parser。
        # - fallback_chain: 本 parser 的固定解析链路。
        result_metadata = {
            **result.metadata,
            "parser": self.name,
            "selected_parser": selected_parser,
            "fallback_chain": [ParserName.DOCLING, ParserName.MARKITDOWN],
            "page_count": len(result.pages),
            "table_count": len(result.tables),
        }

        return replace(
            result,
            metadata=result_metadata,
            warnings=warnings + result.warnings,
        )
