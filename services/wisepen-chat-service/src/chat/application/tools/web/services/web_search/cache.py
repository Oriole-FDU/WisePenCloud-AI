import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, NamedTuple

from cachetools import TTLCache

from chat.application.algorithms.hash import stable_hash, stable_hash_json
from chat.application.tools.web.services.web_search.enums import ProviderMode, SearchPurpose, SearcherName
from chat.application.tools.web.services.web_search.models import SearchResponse
from chat.application.tools.web.services.web_search.provider_policy.models import CUSTOM_PROVIDER_NAMES
from chat.application.tools.web.services.web_search.utils.results import is_valid_result


class CacheDescriptor(NamedTuple):
    key: str
    purpose: SearchPurpose

# 绑定缓存场景与各自独立的生存周期。
SEARCH_CACHE_TTL_SECONDS = {
    SearchPurpose.RECALL: 1 * 3600,  # 1 小时
    SearchPurpose.GROUNDING: 24 * 3600,  # 24 小时
}

WEB_SEARCH_CACHE_MAXSIZE = 1024

@dataclass(frozen=True, slots=True)
class CachedSearchResponse:
    """标准内存缓存包装实体"""
    response: SearchResponse
    cached_at: float
    cache_key: str
    cache_hit: bool = True


def make_search_cache_descriptor(
        *,
        source: SearcherName,  # 渠道名（如 "fourget"），直接作为一等公民参与拼装
        query: str,
        max_results: int,
        purpose: SearchPurpose = SearchPurpose.RECALL,
        provider_mode: ProviderMode = ProviderMode.DEFAULT,
        user_id: Optional[str] = None,
) -> CacheDescriptor:
    """
    生成全局唯一的缓存物理键及场景标记。
    """
    # 只保留核心检索契约，让相同查询、相同渠道共享缓存命中。
    payload = {
        "source": source.value,
        "purpose": purpose.value,
        "provider_mode": provider_mode.value,
        "query": " ".join(query.strip().split()),  # 仅保留压缩连续空格，防止多余空格导致哈希错乱
        "max_results": max_results,
    }

    if provider_mode == ProviderMode.CUSTOM or source in CUSTOM_PROVIDER_NAMES:
        if user_id is None:
            raise ValueError("user_id must be provided")
        payload["user_id_hash"] = stable_hash(user_id)

    return CacheDescriptor(
        key=f"search:{purpose.value}:{stable_hash_json(payload)}",
        purpose=purpose
    )


class SearchCache:
    """内存缓存组件"""

    def __init__(self, *, maxsize: int = WEB_SEARCH_CACHE_MAXSIZE) -> None:
        # 直接使用枚举作为底层字典键，划分召回和背景对齐两个独立缓存池。
        """初始化对象依赖。"""
        self._caches: Dict[SearchPurpose, TTLCache] = {
            purpose: TTLCache(maxsize=maxsize, ttl=ttl)
            for purpose, ttl in SEARCH_CACHE_TTL_SECONDS.items()
        }

    def get(self, desc: CacheDescriptor) -> Optional[CachedSearchResponse]:
        """获取当前流程。"""
        cache_pool = self._caches.get(desc.purpose, self._caches[SearchPurpose.RECALL])
        return cache_pool.get(desc.key)

    def set(
            self,
            desc: CacheDescriptor,
            response: SearchResponse,
            *,
            cached_at: Optional[float] = None,
    ) -> None:
        """同步塞入缓存对象（带业务层内容有效性卡口拦截）"""
        if not any(is_valid_result(r) for r in response.results):
            return

        wrapper = CachedSearchResponse(
            response=response,
            cached_at=cached_at or time.time(),
            cache_key=desc.key,
        )
        self._caches.get(desc.purpose, self._caches[SearchPurpose.RECALL])[desc.key] = wrapper

    def get_many(
            self,
            descriptors: List[CacheDescriptor],
    ) -> Tuple[Dict[str, CachedSearchResponse], List[CacheDescriptor]]:
        """同步批量检索缓存

        - hits, misses
        """
        hits: Dict[str, CachedSearchResponse] = {}
        misses: List[CacheDescriptor] = []

        for desc in descriptors:
            cached = self.get(desc)
            if cached is None:
                misses.append(desc)
            else:
                hits[desc.key] = cached
        return hits, misses

    def get_fresh_many(
            self,
            queries: List[str],
            *,
            max_results: int,
            source: SearcherName,
            purpose: SearchPurpose = SearchPurpose.RECALL,
    ) -> Tuple[List[SearchResponse], List[str]]:
        """同步批量变体检索缓存（用于上层批量并发前的缓存过滤拦截）

        Return:
        - hits, misses
        """
        hits: List[SearchResponse] = []
        misses: List[str] = []

        for query in queries:
            desc = make_search_cache_descriptor(
                source=source,
                query=query,
                max_results=max_results,
                purpose=purpose,
            )
            cached = self.get(desc)
            if cached is None:
                misses.append(query)
            else:
                hits.append(cached.response)
        return hits, misses
