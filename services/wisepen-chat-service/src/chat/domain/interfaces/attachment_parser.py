from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AttachmentParseResult:
    """附件解析结果"""

    summary: str
    content_excerpt: str
    extracted_text: str


class AttachmentParser(ABC):
    """附件解析器接口"""

    @abstractmethod
    async def parse(
        self,
        object_key: str,
        filename: str,
        extension: str,
    ) -> AttachmentParseResult:
        pass
