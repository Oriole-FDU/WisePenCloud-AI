import asyncio
import re
from enum import StrEnum

from common.core.exceptions import ServiceException

from sandbox.domain.error_codes import SandboxErrorCode


class ContainerStatus(StrEnum):
    """容器运行状态"""

    RUNNING = "running"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


class ContainerManager:
    """阶段 1 容器管理器：创建、强制停止容器并读取状态。"""

    _PORT_MAPPING_PATTERN = re.compile(r"^(?P<container_port>\d+)/(?P<protocol>tcp|udp)\s+->\s+(?P<binding>.+)$")

    def __init__(self, endpoint_host: str = "127.0.0.1") -> None:
        self._endpoint_host = endpoint_host

    async def create(self, sandbox_img: str) -> str:
        """创建并启动容器，返回容器 ID。"""
        returncode, stdout, stderr = await self._docker("run", "-d", "-i", "-t", "-P", sandbox_img)
        if returncode != 0:
            raise ServiceException(SandboxErrorCode.DOCKER_RUNTIME_FAILED,f"docker run failed: {stderr}")
        container_id = stdout.strip()
        if not container_id:
            raise ServiceException(SandboxErrorCode.DOCKER_RUNTIME_FAILED, "docker run did not return a container id")
        return container_id

    async def destroy(self, container_id: str) -> None:
        """强制停止并删除容器；容器不存在时视为已完成。"""
        returncode, _, stderr = await self._docker("rm", "-f", container_id)
        if returncode != 0 and not self._is_not_found(stderr):
            raise ServiceException(SandboxErrorCode.DOCKER_RUNTIME_FAILED,f"docker rm failed: {stderr}")

    async def check_container_status(self, container_id: str) -> ContainerStatus:
        """获取容器状态。"""
        returncode, stdout, stderr = await self._docker("inspect", "--format", "{{.State.Running}}", container_id)
        if returncode != 0:
            if self._is_not_found(stderr):
                return ContainerStatus.NOT_FOUND
            return ContainerStatus.UNKNOWN

        return ContainerStatus.RUNNING if stdout.strip().lower() == "true" else ContainerStatus.UNKNOWN

    async def get_container_base_url(self, container_id: str) -> str:
        """读取 Docker 自动发布的唯一容器端口并构造访问地址。"""
        returncode, stdout, stderr = await self._docker("port", container_id)
        if returncode != 0:
            raise ServiceException(SandboxErrorCode.DOCKER_RUNTIME_FAILED,f"docker port failed: {stderr}")

        binding_hosts: set[str] = set()
        host_ports: set[int] = set()

        for line in stdout.splitlines():
            stripped_line = line.strip()
            match = self._PORT_MAPPING_PATTERN.fullmatch(stripped_line)
            binding_host, separator, host_port = match.group("binding").rpartition(":") # 取最右侧冒号分离端口号
            binding_hosts.add(binding_host)
            host_ports.add(host_port)

        concrete_hosts = binding_hosts - {"0.0.0.0", "[::]"}
        if len(host_ports) != 1 or len(concrete_hosts) > 1:
            raise ServiceException(SandboxErrorCode.DOCKER_RUNTIME_FAILED,f"docker port did not return one unambiguous mapping: {container_id}")

        endpoint_host = concrete_hosts.pop() if concrete_hosts else self._endpoint_host
        return f"http://{endpoint_host}:{host_ports.pop()}"

    async def _docker(self, *args: str) -> tuple[int, str, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ServiceException(SandboxErrorCode.DOCKER_RUNTIME_FAILED,f"docker command could not be started: {exc}") from exc
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return process.returncode or 0, stdout, stderr.strip()

    @staticmethod
    def _is_not_found(stderr: str) -> bool:
        normalized = stderr.lower()
        return "no such container" in normalized or "no such object" in normalized
