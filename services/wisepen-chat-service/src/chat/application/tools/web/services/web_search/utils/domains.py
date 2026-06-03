from typing import List

from chat.application.tools.web.services.web_search.models import SearchResult
from chat.application.tools.web.utils.domains import extract_domain


def count_unique_domains(results: List[SearchResult]) -> int:
    """计算搜索结果集中不重复的有效裸域名总数。

    Args:
        results: 搜索结果列表。

    Returns:
        不重复的有效域名数量。
    """
    domains = {extract_domain(result.url) for result in results}
    domains.discard("")
    return len(domains)
