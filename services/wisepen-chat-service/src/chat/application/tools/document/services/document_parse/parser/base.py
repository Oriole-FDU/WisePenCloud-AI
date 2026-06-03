from abc import ABC, abstractmethod
from pathlib import Path
from typing import Tuple

from chat.application.tools.document.services.document_parse.enums import ParserName
from chat.application.tools.document.services.document_parse.models import DocumentParseResult


class BaseDocumentParser(ABC):
    """文档解析器抽象基类，定义所有解析器的统一接口。"""

    @property
    @abstractmethod
    def name(self) -> ParserName:
        """返回解析器名称，用于元数据标注。"""
        ...

    @property
    @abstractmethod
    def supported_extensions(self) -> Tuple[str, ...]:
        """返回解析器支持的文件后缀元组。"""
        ...

    @abstractmethod
    async def parse(self, path: Path) -> DocumentParseResult:
        """解析文档文件，返回统一的 DocumentParseResult。"""
        ...
