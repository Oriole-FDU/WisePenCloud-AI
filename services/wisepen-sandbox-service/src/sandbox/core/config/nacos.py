from common.cloud.nacos_client import NacosClientManager
from common.logger import info
from v2.nacos import RegisterInstanceParam

from sandbox.core.config.bootstrap_settings import bootstrap_settings


class StrictNacosClientManager(NacosClientManager):
    """Nacos client whose registration failures are fatal to service startup."""

    async def register_instance(self) -> None:
        client = await self.get_naming_client()
        host = self._resolve_host()
        metadata = {"preserved.register.source": "PYTHON_FASTAPI"}
        if self.bootstrap_settings.DEVELOPER_ENABLE and self.bootstrap_settings.DEVELOPER_NAME:
            metadata["developer"] = self.bootstrap_settings.DEVELOPER_NAME
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


nacos_client_manager = StrictNacosClientManager(bootstrap_settings)
