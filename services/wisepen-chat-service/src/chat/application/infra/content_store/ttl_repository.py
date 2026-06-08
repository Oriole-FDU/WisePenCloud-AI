import threading
from typing import Optional

from cachetools import TTLCache

from .models import StoredContent


class TTLContentRepository:
    """
    基于字符预算的 TTL 内容缓存仓库。
    StoredContent 以引用形式返回，调用方不应修改其内容。

    Tool Content Store 目前已经 Redis 化，此处为后续可能的调用方保留
    """

    def __init__(self, *, ttl_seconds: int, max_total_chars: int) -> None:

        self._lock = threading.Lock()
        self._items: TTLCache[str, StoredContent] = TTLCache(
            maxsize=max_total_chars,
            ttl=ttl_seconds,
            getsizeof=lambda item: len(item.text),  # maxsize 是字符预算，不是条目数
        )

    def put(self, content: StoredContent) -> None:
        with self._lock:
            self._items[content.content_id] = content

    def get(self, content_id: str) -> Optional[StoredContent]:
        with self._lock:
            return self._items.get(content_id)

    def delete(self, content_id: str) -> None:
        with self._lock:
            self._items.pop(content_id, None)

    def expire(self) -> None:
        with self._lock:
            self._items.expire()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()