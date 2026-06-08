import asyncio
from pathlib import Path
from typing import List, Optional

from chat.application.tools.document.services.document_parse.enums import DocumentType
from chat.application.tools.document.services.document_parse.errors import (
    DocumentParseError,
    EmptyParsedContentError,
    UnsupportedDocumentFormatError,
)
from chat.application.tools.document.services.document_parse.models import (
    DocumentParseResult,
    DocumentParseResultItem,
)
from chat.application.tools.document.services.document_parse.parser.epub import (
    EpubParser,
)
from chat.application.tools.document.services.document_parse.parser.office.parser import (
    OfficeParser,
)
from chat.application.tools.document.services.document_parse.parser.pdf.parser import (
    PdfParser,
)
from chat.application.tools.document.services.document_parse.parser.spreadsheet import (
    SpreadsheetParser,
)
from chat.application.tools.document.services.document_parse.utils.suffixes import (
    detect_document_type_by_suffix,
)
from common.logger import log_error

_PARSE_CONCURRENCY = 4


class DocumentParseService:
    """
    文档解析服务总入口。

    - 根据文件后缀路由到具体 parser。
    - 对外提供单文件解析 parse_path。
    - 对外提供多文件并发解析 parse_many。
    """

    def __init__(
        self,
        *,
        pdf_parser: PdfParser,
        office_parser: OfficeParser,
        epub_parser: EpubParser,
        spreadsheet_parser: SpreadsheetParser,
    ):
        """初始化 DocumentParseService，注入各文档类型的专用解析器。"""
        self.pdf_parser = pdf_parser
        self.office_parser = office_parser
        self.epub_parser = epub_parser
        self.spreadsheet_parser = spreadsheet_parser

    async def parse_path(self, path: Path) -> DocumentParseResult:
        """解析单个文档文件。

        根据文件后缀将文档路由到对应解析器处理，统一收敛空文本结果为异常。
        """
        if not path.is_file():
            raise FileNotFoundError(f"Document file not found: {path}")

        document_type = detect_document_type_by_suffix(path)

        # 解析器路由层：
        # 文档解析服务不关心具体解析细节，只负责选择解析器。
        if document_type == DocumentType.PDF:
            result = await self.pdf_parser.parse(path)
        elif document_type in {DocumentType.DOCX, DocumentType.PPTX}:
            result = await self.office_parser.parse(path)
        elif document_type == DocumentType.EPUB:
            result = await self.epub_parser.parse(path)
        elif document_type == DocumentType.SPREADSHEET:
            result = await self.spreadsheet_parser.parse(path)
        else:
            raise UnsupportedDocumentFormatError(document_type)

        # 空文本统一在服务出口收敛，避免上层拿到无内容的成功结果。
        if not result.text.strip():
            raise EmptyParsedContentError(str(path))

        return result

    async def parse_many(
        self,
        paths: List[Path],
        *,
        file_refs: Optional[List[str]] = None,
    ) -> List[DocumentParseResultItem]:
        """
        并发解析多个文件，使用信号量控制并发度。

        单个文件解析异常不影响其他文件，file_refs 用于保留调用方传入的原始引用。
        """
        semaphore = asyncio.Semaphore(_PARSE_CONCURRENCY)

        async def _parse_one(index: int, path: Path) -> DocumentParseResultItem:
            # 文件引用优先使用调用方传入值，缺失时降级为真实路径字符串。
            """并发解析单个文件，异常被捕获包装为失败结果。"""
            ref = (
                file_refs[index] if file_refs and index < len(file_refs) else str(path)
            )

            async with semaphore:
                try:
                    result = await self.parse_path(path)
                    return DocumentParseResultItem(
                        file_ref=ref,
                        success=True,
                        result=result,
                    )
                except FileNotFoundError as e:
                    return DocumentParseResultItem(
                        file_ref=ref,
                        success=False,
                        error=str(e),
                    )
                except DocumentParseError as e:
                    return DocumentParseResultItem(
                        file_ref=ref,
                        success=False,
                        error=str(e),
                    )
                except Exception as e:
                    # 未预期异常只影响当前文件，并记录日志便于排查。
                    log_error(
                        "document_parse unexpected error",
                        e,
                        file_ref=ref,
                        path=str(path),
                    )
                    return DocumentParseResultItem(
                        file_ref=ref,
                        success=False,
                        error=f"未预期异常: {e.__class__.__name__}",
                    )

        tasks = [_parse_one(i, p) for i, p in enumerate(paths)]
        return list(await asyncio.gather(*tasks))
