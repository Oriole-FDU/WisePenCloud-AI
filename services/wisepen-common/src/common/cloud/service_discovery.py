"""
基于 Nacos 的客户端服务发现 + 客户端侧负载均衡
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, Dict, Iterable, List, Literal, Optional

from v2.nacos import (
    Instance,
    ListInstanceParam,
    NacosNamingService,
    SubscribeServiceParam,
)

from common.core.constants import CommonConstants
from common.core.exceptions import ServiceUnavailableError
from common.gray.context import GrayContextHolder
from common.logger import error, info, warn

NamingClientProvider = Callable[[], Awaitable[NacosNamingService]]

# 允许的负载均衡策略
LoadBalancingStrategy = Literal["weighted_random", "round_robin", "random"]
# 缓存 TTL
_DEFAULT_CACHE_TTL_SECONDS = 30.0


class ServiceDiscovery:
    """
    Nacos NamingService 的轻量化封装
    """

    def __init__(
        self,
        naming_client_provider: NamingClientProvider,
        *,
        group_name: str,
        default_strategy: LoadBalancingStrategy = "weighted_random",
        cache_ttl_seconds: float = _DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        # 懒加载 Nacos 客户端
        self._naming_provider = naming_client_provider
        self._naming: NacosNamingService | None = None
        self._naming_lock = asyncio.Lock()

        # 服务组名
        self._group = group_name
        # 默认负载均衡策略
        self._default_strategy: LoadBalancingStrategy = default_strategy
        self._ttl = cache_ttl_seconds
        # 本地服务实例缓存
        self._cache: Dict[str, List[Instance]] = {}
        # 服务上次刷新时间，用于 TTL 判断
        self._fetched_at: Dict[str, float] = {}
        # 轮询策略游标
        self._rr_cursor: Dict[str, int] = {}
        # 记录已经订阅过 Nacos 变更的服务
        self._subscribed: set[str] = set()
        # 锁，避免同一个服务在高并发下同时刷新 Nacos
        self._locks: Dict[str, asyncio.Lock] = {}

    # 懒加载 Nacos 客户端    
    async def _get_naming(self) -> NacosNamingService:
        if self._naming is not None:
            return self._naming
        async with self._naming_lock:
            # 如果 Nacos 客户端未初始化，加锁并初始化
            if self._naming is None:
                self._naming = await self._naming_provider()
            return self._naming


    @staticmethod
    def _developer_of(instance: Instance) -> str:
        metadata = getattr(instance, "metadata", None) or {}
        return str(metadata.get(CommonConstants.GRAY_METADATA_DEV_KEY) or "").strip()

    # 选择灰度池（开发者隔离）
    def _select_gray_pool(self, service_name: str, instances: List[Instance]) -> List[Instance]:
        developer = (GrayContextHolder.get_developer_tag() or "").strip()

        baseline = [i for i in instances if not self._developer_of(i)]

        if developer:
            matched = [i for i in instances if self._developer_of(i) == developer]
            if matched:
                return matched
            if baseline:
                return baseline
            raise ServiceUnavailableError(service_name, self._group)

        if baseline:
            return baseline

        raise ServiceUnavailableError(service_name, self._group)

    # 从本地缓存挑一个可用实例
    async def pick(
        self,
        service_name: str,
        *,
        strategy: Optional[LoadBalancingStrategy] = None,
        exclude: Optional[Iterable[str]] = None,
    ) -> Instance:
        """
        从本地缓存挑一个可用实例
        strategy: 覆盖默认策略
        exclude: {ip:port} 集合，用于故障转移时跳过已失败的实例
        """
        await self._ensure_ready(service_name)

        instances = self._cache.get(service_name, [])
        instances = self._select_gray_pool(service_name, instances)

        if exclude:
            deny = set(exclude)
            instances = [i for i in instances if f"{i.ip}:{i.port}" not in deny]

        if not instances:
            raise ServiceUnavailableError(service_name, self._group)

        chosen_strategy = strategy or self._default_strategy
        if chosen_strategy == "round_robin":
            return self._pick_round_robin(service_name, instances)
        if chosen_strategy == "random":
            return random.choice(instances)
        # 默认 weighted_random
        return self._pick_weighted_random(instances)

    async def close(self) -> None:
        """
        进程退出时调用
        这里只清本地缓存；Nacos SDK 侧的连接由 NacosClientManager 统一负责
        """
        self._cache.clear()
        self._fetched_at.clear()
        self._rr_cursor.clear()
        self._subscribed.clear()


    async def _ensure_ready(self, service_name: str) -> None:
        # TTL 没过期就直接用缓存
        now = time.monotonic()
        if (
            service_name in self._cache
            and (now - self._fetched_at.get(service_name, 0.0)) < self._ttl
        ):
            return

        # 给当前 service 加锁避免同一个服务被并发重复刷新
        lock = self._locks.setdefault(service_name, asyncio.Lock())
        async with lock:
            # 双检：防止在等待锁的时候别的协程刷新好了缓存
            now = time.monotonic()
            if (
                service_name in self._cache
                and (now - self._fetched_at.get(service_name, 0.0)) < self._ttl
            ):
                return

            try:
                await self._refresh(service_name)
            except Exception as e:
                # list_instances 失败时保留旧缓存以支持降级
                # 无旧缓存则抛出 ServiceUnavailableError
                if service_name not in self._cache:
                    error("nacos instances refresh failed.", service=service_name, group=self._group, exc=e)
                    raise ServiceUnavailableError(service_name, self._group) from e
                warn("nacos instances refresh degraded to cache.", service=service_name, group=self._group, exc=e)
                return

            # 首次成功拉取后注册订阅，靠推送增量刷新，失败不致命
            if service_name not in self._subscribed:
                try:
                    naming = await self._get_naming()
                    await naming.subscribe(
                        SubscribeServiceParam(
                            service_name=service_name,
                            group_name=self._group,
                            subscribe_callback=self._build_callback(service_name),
                        )
                    )
                    self._subscribed.add(service_name)
                    info("nacos service subscribed.", service=service_name, group=self._group)
                except Exception as e:
                    warn("nacos service subscribe failed.", service=service_name, group=self._group, exc=e)

    async def _refresh(self, service_name: str) -> None:
        naming = await self._get_naming()
        instances: List[Instance] = await naming.list_instances(
            ListInstanceParam(
                service_name=service_name,
                group_name=self._group,
                healthy_only=True,
            )
        )
        self._cache[service_name] = [i for i in (instances or []) if self._is_usable(i)]
        self._fetched_at[service_name] = time.monotonic()

    def _build_callback(self, service_name: str):
        """
        Nacos subscribe 回调
        如果推送过来的列表为空则保守保留旧缓存，下一次 TTL 触发强制 refresh
        """
        async def _on_change(instance_list: List[Instance]) -> None:
            usable = [i for i in (instance_list or []) if self._is_usable(i)]
            if not usable:
                warn(
                    "nacos pushed empty instance list, keep previous cache.",
                    service=service_name,
                    group=self._group,
                )
                return
            self._cache[service_name] = usable
            self._fetched_at[service_name] = time.monotonic()
            info(
                "nacos instance list updated.",
                service=service_name,
                group=self._group,
                count=len(usable),
            )

        return _on_change

    @staticmethod
    def _is_usable(instance: Instance) -> bool:
        healthy = getattr(instance, "healthy", True)
        enabled = getattr(instance, "enabled", True)
        return bool(healthy) and bool(enabled)

    # 按权重随机
    @staticmethod
    def _pick_weighted_random(instances: List[Instance]) -> Instance:
        weights = [max(float(getattr(i, "weight", 1.0) or 0.0), 0.0) for i in instances]
        total = sum(weights)
        if total <= 0:
            return random.choice(instances)
        return random.choices(instances, weights=weights, k=1)[0]

    def _pick_round_robin(self, service_name: str, instances: List[Instance]) -> Instance:
        cursor = self._rr_cursor.get(service_name, 0) % len(instances)
        self._rr_cursor[service_name] = cursor + 1
        return instances[cursor]

