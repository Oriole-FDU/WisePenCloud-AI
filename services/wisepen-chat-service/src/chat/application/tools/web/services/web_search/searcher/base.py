from abc import ABC, abstractmethod

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.models import SearchResponse


class WebSearcher(ABC):
    """Web 搜索器抽象基类。

    所有搜索器实现此接口，遵循统一流程：
      1. 构建请求实体
      2. 调用 fetch_search_json 发送 HTTP 请求
      3. 校验响应为 dict
      4. 调用 map_*_response 映射为统一 SearchResponse
      5. 空结果时抛出 SearchProviderTransientError 触发重试
    """

    @property
    @abstractmethod
    def name(self) -> SearcherName:
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        max_results: int,
    ) -> SearchResponse:
        """执行一次 Web 搜索。

        Args:
            query: 搜索关键词。
            max_results: 最大返回结果数。

        Returns:
            包含搜索结果列表和来源信息的 SearchResponse。
        """
        ...
