from abc import ABC, abstractmethod
from typing import Optional, Union

from chat.application.tools.web.services.web_fetch.enums import FetcherName
from chat.application.tools.web.services.web_fetch.models import (
    FetchedDocument,
    FetchedPage,
    FetchedRedirect,
)

FetchedContent = Union[FetchedDocument, FetchedPage, FetchedRedirect]


class BaseFetcher(ABC):
    """网页抓取器抽象基类，定义统一的抓取接口。"""

    @property
    @abstractmethod
    def name(self) -> FetcherName:
        """返回当前抓取器的唯一标识名称。"""
        pass

    @abstractmethod
    async def fetch(self, url: str) -> Optional[FetchedContent]:
        """异步抓取指定 URL 的网页内容，返回结构化的抓取结果。"""
        pass