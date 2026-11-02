"""离线部署工具独立于线上 src 包，测试按真实入口加入 deploy 路径。"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))


@pytest.fixture(params=[RedisTimeoutError, RedisConnectionError, TimeoutError, "deadline"])
def failed_cache_write(request):
    """在缓存写入边界注入网络故障或阻塞，由真实请求预算终止写入。"""
    if request.param == "deadline":
        async def blocked(*args, **kwargs):
            await asyncio.Event().wait()

        return AsyncMock(side_effect=blocked)
    return AsyncMock(side_effect=request.param("cache write failed"))
