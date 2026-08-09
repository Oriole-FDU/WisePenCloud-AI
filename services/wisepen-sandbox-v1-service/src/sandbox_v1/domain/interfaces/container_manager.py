from enum import StrEnum


class ContainerStatus(StrEnum):
    """容器运行状态"""

    RUNNING = "running"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"

class ContainerManager:
    """容器管理器的空实现"""

    async def create(self, sandbox_img: str) -> str:...

    async def destroy(self, container_id: str) -> None:...

    async def check_container_status(self, container_id: str) -> ContainerStatus:...