import socket
from typing import Callable, Optional
from common.logger import log_ok, log_error
from v2.nacos import (
    NacosConfigService,
    NacosNamingService,
    ClientConfigBuilder,
    GRPCConfig,
    ConfigParam,
    RegisterInstanceParam,
    DeregisterInstanceParam,
)

from chat.core.config.bootstrap_settings import bootstrap_settings

_config_client: NacosConfigService | None = None
_naming_client: NacosNamingService | None = None


class NacosClientManager:
    """
    Nacos 客户端管理器 (单例类)
    封装了 Nacos 的配置拉取、配置监听、服务注册与注销逻辑
    """
    def __init__(self):
        # 使用实例属性替代原来的 global 变量
        self._config_client: Optional[NacosConfigService] = None
        self._naming_client: Optional[NacosNamingService] = None

    def _build_client_config(self):
        return (
            ClientConfigBuilder()
            .server_address(bootstrap_settings.NACOS_SERVER_ADDR)
            .username(bootstrap_settings.NACOS_USERNAME)
            .password(bootstrap_settings.NACOS_PASSWORD)
            .namespace_id(bootstrap_settings.NACOS_NAMESPACE_ID)
            .log_level("INFO")
            .grpc_config(GRPCConfig(grpc_timeout=5000))
            .build()
        )

    async def _get_config_client(self) -> NacosConfigService:
        if self._config_client is None:
            self._config_client = await NacosConfigService.create_config_service(self._build_client_config())
        return self._config_client

    async def get_naming_client(self) -> NacosNamingService:
        if self._naming_client is None:
            self._naming_client = await NacosNamingService.create_naming_service(self._build_client_config())
        return self._naming_client

    async def pull_config(self) -> str:
        """从 Nacos 拉取配置字符串"""
        client = await self._get_config_client()
        return await client.get_config(ConfigParam(
            data_id=bootstrap_settings.NACOS_DATA_ID,
            group=bootstrap_settings.NACOS_GROUP,
        ))

    def _resolve_host(self) -> str:
        """注册到 Nacos 时使用的 IP
        优先级 NACOS_REGISTER_IP ＞ SERVICE_HOST（非回环地址） ＞ socket.gethostname 兜底
        """

        if bootstrap_settings.NACOS_REGISTER_IP:
            return bootstrap_settings.NACOS_REGISTER_IP

        host = bootstrap_settings.SERVICE_HOST
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
        try:
            await client.register_instance(
                request=RegisterInstanceParam(
                    service_name=bootstrap_settings.SERVICE_NAME,
                    group_name=bootstrap_settings.NACOS_GROUP,
                    ip=host,
                    port=bootstrap_settings.SERVICE_PORT,
                    metadata={"version": "0.1.0", "framework": "fastapi"},
                    healthy=True,
                    ephemeral=True,
                )
            )
            log_ok(
                "Nacos 服务注册",
                service=bootstrap_settings.SERVICE_NAME,
                addr=f"{host}:{bootstrap_settings.SERVICE_PORT}",
            )
        except Exception as e:
            log_error("Nacos 服务注册", e, service=bootstrap_settings.SERVICE_NAME)

    async def deregister_instance(self) -> None:
        """从 Nacos 注销当前服务实例（优雅关闭）。"""
        client = await self.get_naming_client()
        host = self._resolve_host()
        try:
            await client.deregister_instance(
                request=DeregisterInstanceParam(
                    service_name=bootstrap_settings.SERVICE_NAME,
                    group_name=bootstrap_settings.NACOS_GROUP,
                    ip=host,
                    port=bootstrap_settings.SERVICE_PORT,
                    ephemeral=True,
                )
            )
            log_ok("Nacos 服务注销", service=bootstrap_settings.SERVICE_NAME)
        except Exception as e:
            log_error("Nacos 服务注销", e, service=bootstrap_settings.SERVICE_NAME)

    async def watch_config(self, callback: Callable[[dict], None]) -> None:
        """启动 Nacos 配置监听"""
        client = await self._get_config_client()
        try:
            # 注册监听器，当 Nacos 上的配置文件发生变化时，触发回调
            await client.add_config_watcher(
                data_id=bootstrap_settings.data_id,
                group=bootstrap_settings.NACOS_GROUP,
                cb=callback
            )
            log_ok("Nacos 配置热更新监听", data_id=bootstrap_settings.data_id)
        except Exception as e:
            log_error("Nacos 配置热更新监听", e, data_id=bootstrap_settings.data_id)

nacos_client_manager = NacosClientManager()