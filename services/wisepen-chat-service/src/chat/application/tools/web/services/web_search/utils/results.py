from typing import List, Dict

from chat.application.tools.web.services.web_search.models import SearchResult, SearchResponse
from chat.application.tools.web.utils.domains import extract_domain


def deduplicate_results_by_domain(
    results: List[SearchResult],
    *,
    max_per_domain: int = 2,
) -> List[SearchResult]:
    """按域名对搜索结果去重，每个域名最多保留指定数量的结果。

    从 URL 中提取域名，对结果按原始顺序遍历，遇到同一域名
    达到上限时跳过，保证来源多样性的同时保留排名顺序。

    Args:
        results: 待去重的搜索结果列表。
        max_per_domain: 每个域名最多保留的结果数，默认 2 条。

    Returns:
        去重后的搜索结果列表。
    """
    domain_counts: Dict[str, int] = {}
    deduped: List[SearchResult] = []

    for result in results:
        domain = extract_domain(result.url)

        if not domain:
            deduped.append(result)
            continue

        count = domain_counts.get(domain, 0)
        if count >= max_per_domain:
            continue

        domain_counts[domain] = count + 1
        deduped.append(result)

    return deduped


def is_valid_result(result: SearchResult) -> bool:
    """检查搜索结果是否包含有效内容。

    至少需要满足以下条件：
      1. URL 不能为空
      2. 标题和摘要不能同时为空

    Args:
        result: 待检查的搜索结果。

    Returns:
        结果有效返回 True，否则返回 False。
    """
    title = result.title.strip()
    url = result.url.strip()
    snippet = result.snippet.strip()

    if not url or (not title and not snippet):
        return False

    return True

def has_response_content(response: SearchResponse) -> bool:
    """检查 SearchResponse 中是否包含至少一条有效搜索结果。

    遍历 response.results，只要有一条通过 is_valid_result 检查即返回 True。

    Args:
        response: 待检查的搜索响应对象。

    Returns:
        包含有效结果返回 True，否则返回 False。
    """
    return any(
        is_valid_result(result)
        for result in response.results
    )

