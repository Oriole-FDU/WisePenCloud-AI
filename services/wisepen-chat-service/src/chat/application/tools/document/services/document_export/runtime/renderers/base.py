from abc import ABC, abstractmethod

from chat.application.tools.document.services.document_export.enums import ExportFormat
from chat.application.tools.document.services.document_export.runtime.models import ExportRequest


class DocumentRenderer(ABC):
    """文档渲染器抽象基类，定义所有格式渲染器的统一接口。"""

    @property
    @abstractmethod
    def target_format(self) -> ExportFormat:
        """返回渲染器支持的目标格式。"""
        ...

    @abstractmethod
    async def render(self, request: ExportRequest) -> None:
        """执行文档渲染，将 Markdown 内容输出为指定格式文件。"""
        ...
