import socket
from collections.abc import Awaitable
from typing import Callable, Optional
from common.logger import error, info
from v2.nacos import (
    NacosConfigService,
    NacosNamingService,
    ClientConfigBuilder,
    GRPCConfig,
    ConfigParam,
    RegisterInstanceParam,
    DeregisterInstanceParam,
)

from common.core.config.bootstrap_settings import BootstrapSettings

_config_client: NacosConfigService | None = None
_naming_client: NacosNamingService | None = None


class  NacosClientManager:
    """
    Nacos 客户端管理器 (单例类)
    封装了 Nacos 的配置拉取、配置监听、服务注册与注销逻辑
    """
    def __init__(self, bootstrap_settings: BootstrapSettings):
        self.bootstrap_settings = bootstrap_settings

        self._config_client: Optional[NacosConfigService] = None
        self._naming_client: Optional[NacosNamingService] = None

    def _build_client_config(self):
        if self.bootstrap_settings.NACOS_USERNAME and self.bootstrap_settings.NACOS_PASSWORD:
            return (
                ClientConfigBuilder()
                .server_address(self.bootstrap_settings.NACOS_SERVER_ADDR)
                .username(self.bootstrap_settings.NACOS_USERNAME)
                .password(self.bootstrap_settings.NACOS_PASSWORD)
                .namespace_id(self.bootstrap_settings.NACOS_NAMESPACE_ID)
                .log_level("INFO")
                .grpc_config(GRPCConfig(grpc_timeout=5000))
                .build()
            )
        else:
            return (
                ClientConfigBuilder()
                .server_address(self.bootstrap_settings.NACOS_SERVER_ADDR)
                .namespace_id(self.bootstrap_settings.NACOS_NAMESPACE_ID)
                .log_level("INFO")
                .grpc_config(GRPCConfig(grpc_timeout=5000))
                .build()
            )

    async def _get_config_client(self) -> NacosConfigService | None:
        if self._config_client is None:
            self._config_client = await NacosConfigService.create_config_service(self._build_client_config())
        return self._config_client

    async def get_naming_client(self) -> NacosNamingService | None:
        if self._naming_client is None:
            self._naming_client = await NacosNamingService.create_naming_service(self._build_client_config())
        return self._naming_client

    async def pull_config(self) -> str:
        """从 Nacos 拉取配置字符串"""
        client = await self._get_config_client()
        return await client.get_config(ConfigParam(
            data_id=self.bootstrap_settings.NACOS_DATA_ID,
            group=self.bootstrap_settings.NACOS_GROUP,
        ))

    def _resolve_host(self) -> str:
        """注册到 Nacos 时使用的 IP
        优先级 NACOS_REGISTER_IP ＞ SERVICE_HOST（非回环地址） ＞ socket.gethostname 兜底
        """

        if self.bootstrap_settings.NACOS_REGISTER_IP:
            return self.bootstrap_settings.NACOS_REGISTER_IP

        host = self.bootstrap_settings.SERVICE_HOST
        if host in ("127.0.0.1", "localhost", "0.0.0.0"):
            try:
                host = socket.gethostbyname(socket.gethostname())
            except Exception:
                pass
        return host

    async def register_instance(self) -> None:
        """向 Nacos 注册当前服务实例。"""
        client = await self.get_naming_client()
        host = self._resolve_host()

        metadata = {"preserved.register.source": "PYTHON_FASTAPI"}
        if self.bootstrap_settings.DEVELOPER_ENABLE and self.bootstrap_settings.DEVELOPER_NAME:
            metadata["developer"] = self.bootstrap_settings.DEVELOPER_NAME

        try:
            await client.register_instance(
                request=RegisterInstanceParam(
                    service_name=self.bootstrap_settings.SERVICE_NAME,
                    group_name=self.bootstrap_settings.NACOS_GROUP,
                    ip=host,
                    port=self.bootstrap_settings.SERVICE_PORT,
                    metadata=metadata,
                    healthy=True,
                    ephemeral=True,
                )
            )
            info(
                "nacos instance registered.",
                service=self.bootstrap_settings.SERVICE_NAME,
                addr=f"{host}:{self.bootstrap_settings.SERVICE_PORT}",
            )
        except Exception as e:
            error("nacos instance register failed.", service=self.bootstrap_settings.SERVICE_NAME, exc=e)

    async def deregister_instance(self) -> None:
        """从 Nacos 注销当前服务实例（优雅关闭）。"""
        client = await self.get_naming_client()
        host = self._resolve_host()
        try:
            await client.deregister_instance(
                request=DeregisterInstanceParam(
                    service_name=self.bootstrap_settings.SERVICE_NAME,
                    group_name=self.bootstrap_settings.NACOS_GROUP,
                    ip=host,
                    port=self.bootstrap_settings.SERVICE_PORT,
                    ephemeral=True,
                )
            )
            info("nacos instance deregistered.", service=self.bootstrap_settings.SERVICE_NAME)
        except Exception as e:
            error("nacos instance deregister failed.", service=self.bootstrap_settings.SERVICE_NAME, )

    async def watch_config(self, callback: Callable[[str, str, str, str], Awaitable[None]]) -> None:
        """订阅配置；SDK 按 namespace、group、data_id、content 调用异步回调。"""
        client = await self._get_config_client()
        # 订阅失败必须传播，避免服务启动后实际没有热更新能力。
        await client.add_listener(
            data_id=self.bootstrap_settings.NACOS_DATA_ID,
            group=self.bootstrap_settings.NACOS_GROUP,
            listener=callback,
        )
        info("nacos config watcher registered.", data_id=self.bootstrap_settings.NACOS_DATA_ID)

    async def unwatch_config(self, callback: Callable[[str, str, str, str], Awaitable[None]]) -> None:
        client = await self._get_config_client()
        await client.remove_listener(
            data_id=self.bootstrap_settings.NACOS_DATA_ID,
            group=self.bootstrap_settings.NACOS_GROUP,
            listener=callback,
        )

    async def close_config(self) -> None:
        """关闭所属事件循环中的配置客户端，也适用于初始拉取或订阅失败后的清理。"""
        if self._config_client is not None:
            await self._config_client.shutdown()
            self._config_client = None
