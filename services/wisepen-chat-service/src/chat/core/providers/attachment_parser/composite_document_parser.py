from chat.domain.interfaces import AttachmentParser, AttachmentParseResult
from chat.core.providers.attachment_parser.simple_document_parser import (
    SimpleDocumentAttachmentParser,
)
from chat.core.providers.attachment_parser.legacy_office_parser import (
    LegacyOfficeAttachmentParser,
)
from chat.core.providers.attachment_parser.text_code_parser import (
    TextCodeAttachmentParser,
)


class CompositeDocumentAttachmentParser(AttachmentParser):
    """组合解析器：根据扩展名将解析请求分发至对应子解析器"""

    def __init__(
        self,
        simple_parser: SimpleDocumentAttachmentParser,
        legacy_office_parser: LegacyOfficeAttachmentParser,
        text_code_parser: TextCodeAttachmentParser,
    ):
        self._simple_parser = simple_parser
        self._legacy_parser = legacy_office_parser
        self._text_code_parser = text_code_parser

    async def parse(
        self,
        object_key: str,
        filename: str,
        extension: str,
    ) -> AttachmentParseResult:
        if extension in {"doc", "ppt", "xls"}:
            return await self._legacy_parser.parse(
                object_key=object_key,
                filename=filename,
                extension=extension,
            )
        if self._text_code_parser.supports_extension(extension):
            return await self._text_code_parser.parse(
                object_key=object_key,
                filename=filename,
                extension=extension,
            )
        return await self._simple_parser.parse(
            object_key=object_key,
            filename=filename,
            extension=extension,
        )
