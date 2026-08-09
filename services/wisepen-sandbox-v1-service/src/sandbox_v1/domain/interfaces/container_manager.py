import asyncio
from enum import StrEnum


class ContainerStatus(StrEnum):
    """容器运行状态"""

    RUNNING = "running"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


class ContainerManager:
    """阶段 1 容器管理器：创建、强制停止容器并读取状态。"""

    async def create(self, sandbox_img: str) -> str:
        """创建并启动容器，返回容器 ID。"""
        returncode, stdout, stderr = await self._docker("run", "-d", sandbox_img)
        if returncode != 0:
            raise RuntimeError(f"docker run failed: {stderr}")
        return stdout.strip()

    async def destroy(self, container_id: str) -> None:
        """强制停止并删除容器；容器不存在时视为已完成。"""
        returncode, _, stderr = await self._docker("rm", "-f", container_id)
        if returncode != 0 and not self._is_not_found(stderr):
            raise RuntimeError(f"docker rm failed: {stderr}")

    async def check_container_status(self, container_id: str) -> ContainerStatus:
        """获取容器状态。"""
        returncode, stdout, stderr = await self._docker("inspect", "--format", "{{.State.Running}}", container_id)
        if returncode != 0:
            if self._is_not_found(stderr):
                return ContainerStatus.NOT_FOUND
            return ContainerStatus.UNKNOWN

        return ContainerStatus.RUNNING if stdout.strip().lower() == "true" else ContainerStatus.UNKNOWN

    async def get_container_ip(self, container_id: str) -> str:
        """读取容器在 Docker 网络中的 IP。"""
        returncode, stdout, stderr = await self._docker(
            "inspect",
            "--format",
            "{{range .NetworkSettings.Networks}}{{println .IPAddress}}{{end}}",
            container_id,
        )
        if returncode != 0:
            raise RuntimeError(f"docker inspect failed: {stderr}")

        container_ip = next((line.strip() for line in stdout.splitlines() if line.strip()), "")
        if not container_ip:
            raise RuntimeError(f"docker inspect did not return container ip: {container_id}")
        return container_ip

    async def _docker(self, *args: str) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return process.returncode or 0, stdout, stderr.strip()

    @staticmethod
    def _is_not_found(stderr: str) -> bool:
        normalized = stderr.lower()
        return "no such container" in normalized or "no such object" in normalized
