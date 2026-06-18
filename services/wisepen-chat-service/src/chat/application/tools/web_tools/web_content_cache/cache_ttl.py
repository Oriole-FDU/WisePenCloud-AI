"""基于 HTTP 响应头与 hishel RFC 9111 逻辑的智能缓存 TTL 计算。

将 HTTP 响应头中的 Cache-Control / Expires / Last-Modified 等信息
映射到业务层的 soft_expire_at / hard_expire_at 双层 TTL 体系。

- soft_expire_at：内容新鲜期，过期后触发 stale-while-revalidate
- hard_expire_at：硬过期，过期后丢弃缓存
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from hishel._core._headers import Headers, parse_cache_control
from hishel._core._spec import get_freshness_lifetime
from hishel._core.models import Response

from chat.application.tools.tool_settings import tool_settings

# ---------------------------------------------------------------------------
# 业务默认值与上限（全部通过 tool_settings 调参控制）
# ---------------------------------------------------------------------------

_DEFAULT_SOFT_TTL = timedelta(seconds=tool_settings.CACHE_DEFAULT_SOFT_TTL_SECONDS)
_DEFAULT_HARD_TTL = timedelta(seconds=tool_settings.CACHE_DEFAULT_HARD_TTL_SECONDS)
_DEFAULT_STALE_WINDOW = _DEFAULT_HARD_TTL - _DEFAULT_SOFT_TTL

_MAX_SOFT_TTL = timedelta(seconds=tool_settings.CACHE_MAX_SOFT_TTL_SECONDS)
_MAX_HARD_TTL = timedelta(seconds=tool_settings.CACHE_MAX_HARD_TTL_SECONDS)


# ---------------------------------------------------------------------------
# 公开数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CacheTTL:
    """智能计算出的缓存过期时间对。"""

    soft_expire_at: datetime
    hard_expire_at: datetime
    no_store: bool = False  # 当响应头含 no-store 时为 True


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------

def compute_ttl(
    *,
    headers: dict[str, str],
    now: datetime,
    is_shared_cache: bool = False,
    status_code: int = 200,
) -> CacheTTL:
    """从 HTTP 响应头计算 soft/hard 过期时间。

    Parameters
    ----------
    headers:
        HTTP 响应头字典（小写 key）。
    now:
        当前 UTC 时间。
    is_shared_cache:
        当前缓存是否为共享缓存（PUBLIC 域）。为 True 时会优先使用 s-maxage。
    status_code:
        HTTP 状态码，用于启发式缓存判断。

    Returns
    -------
    CacheTTL
        含 soft_expire_at / hard_expire_at / no_store 的计算结果。
    """
    cc = parse_cache_control(headers.get("cache-control"))

    # no-store：不缓存，调用方应跳过写缓存
    if cc.no_store:
        return CacheTTL(
            soft_expire_at=now,
            hard_expire_at=now,
            no_store=True,
        )

    # 利用 hishel 的 RFC 9111 新鲜度计算
    hishel_response = Response(
        status_code=status_code,
        headers=Headers(headers),
    )
    freshness_seconds = get_freshness_lifetime(hishel_response, is_shared_cache)

    if freshness_seconds is not None and freshness_seconds >= 0:
        effective_freshness = timedelta(seconds=freshness_seconds)
    else:
        # freshness_seconds 为 None（无法计算）或 -1（已过期 / 无效 Expires）
        effective_freshness = timedelta(0)

    # must-revalidate / no-cache / max-age=0：立即 stale
    if cc.must_revalidate or cc.no_cache is True or (
        cc.max_age is not None and cc.max_age == 0
    ):
        effective_freshness = timedelta(0)

    # stale window：优先使用 stale-while-revalidate，否则用默认值
    stale_window = (
        timedelta(seconds=cc.stale_while_revalidate)
        if cc.stale_while_revalidate is not None
        else _DEFAULT_STALE_WINDOW
    )

    soft = now + min(effective_freshness, _MAX_SOFT_TTL)
    hard = now + min(effective_freshness + stale_window, _MAX_HARD_TTL)

    # hard 必须 > soft
    if hard <= soft:
        hard = soft + _DEFAULT_STALE_WINDOW

    return CacheTTL(
        soft_expire_at=soft,
        hard_expire_at=hard,
        no_store=False,
    )
