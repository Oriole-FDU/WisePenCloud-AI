from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from typing import Sequence

from common.core.exceptions import ServiceException

from sandbox.domain.entities import SandboxSpec
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.core.providers.aio_adapter.models import AdapterConfig


@dataclass(frozen=True)
class ContainerHandle:
    container_id: str
    endpoint: str
    public_vnc_url: str | None = None
    public_websocket_url: str | None = None


class DockerRuntime:
    """通过 Docker CLI 管理 all-in-one-sandbox 容器。"""

    def __init__(self, config: AdapterConfig, runner=subprocess.run) -> None:
        self._config = config
        self._runner = runner
        self._owner_id = f"{config.owner_id}-{uuid.uuid4().hex}"

    @property
    def workdir(self) -> str:
        return self._config.workdir

    @property
    def owner_id(self) -> str:
        return self._owner_id

    def validate_deployment(self) -> None:
        """Fail before readiness when Docker, image, or network is unavailable."""
        self._run([self._config.docker_bin, "version", "--format", "{{.Server.Version}}"])
        self._run([self._config.docker_bin, "image", "inspect", self._config.image])
        if self._config.network:
            self._run([self._config.docker_bin, "network", "inspect", self._config.network])

    def create(self, spec: SandboxSpec) -> ContainerHandle:
        name = f"wisepen-aio-{uuid.uuid4().hex[:12]}"
        args: list[str] = [
            self._config.docker_bin,
            "run",
            "-d",
            *( ["-i", "-t"] if self._config.tty else [] ),
            "--name",
            name,
            "--label",
            "wisepen.managed=true",
            "--label",
            "wisepen.role=aio-worker",
            "--label",
            f"wisepen.owner={self._owner_id}",
            "--label",
            f"wisepen.sandbox_id={name}",
            "-p",
            # 端口映射使用 host::containerPort，让 Docker 分配随机宿主机端口，避免并发预热冲突。
            f"{self._config.host}::{self._config.api_port}",
            "-p",
            f"{self._config.host}::{self._config.vnc_port}",
        ]
        if self._config.e2e_label:
            # 端到端标签只在测试环境开启，方便清理测试容器。
            args[args.index("--label") + 2:args.index("--label") + 2] = [
                "--label",
                "wisepen.e2e=true",
            ]
        if self._config.network:
            args.extend(["--network", self._config.network])
        if spec.cpu_cores is not None:
            args.extend(["--cpus", str(spec.cpu_cores)])
        if spec.memory_mb is not None:
            args.extend(["--memory", f"{spec.memory_mb}m"])
        for key, value in spec.environment.items():
            args.extend(["-e", f"{key}={value}"])
        args.append(spec.image or self._config.image)
        container_id = self._run(args).strip()
        if not container_id:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "容器创建返回了空 id",
            )
        api_host_port = self._host_port(container_id, self._config.api_port)
        vnc_host_port = self._host_port(container_id, self._config.vnc_port)
        endpoint = (
            f"http://{name}:{self._config.api_port}"
            if self._config.network
            else f"http://{self._config.host}:{api_host_port}"
        )
        return ContainerHandle(
            container_id,
            endpoint,
            self._format_public_url(
                self._config.public_vnc_url_template,
                container_id,
                name,
                vnc_host_port,
            ),
            self._format_public_url(
                self._config.public_websocket_url_template,
                container_id,
                name,
                vnc_host_port,
            ),
        )

    def _host_port(self, container_id: str, container_port: int) -> str:
        port = self._run(
            [self._config.docker_bin, "port", container_id, f"{container_port}/tcp"]
        ).strip()
        host_port = port.rsplit(":", 1)[-1]
        if not host_port.isdigit():
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                f"容器端口 {container_port} 未正确映射",
            )
        return host_port

    def _format_public_url(
        self,
        template: str,
        container_id: str,
        container_name: str,
        host_port: str,
    ) -> str | None:
        if not template:
            return None
        try:
            return template.format(
                host=self._config.host,
                port=host_port,
                container_id=container_id,
                container_name=container_name,
            )
        except (KeyError, ValueError) as exc:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "public VNC URL 模板非法",
            ) from exc

    def remove(self, container_id: str) -> None:
        self._run([self._config.docker_bin, "rm", "-f", container_id])

    def cleanup_owned(self) -> int:
        raw = self._run(
            [
                self._config.docker_bin,
                "ps",
                "-aq",
                "--filter",
                "label=wisepen.role=aio-worker",
                "--filter",
                f"label=wisepen.owner={self._owner_id}",
            ]
        )
        container_ids = [value for value in raw.splitlines() if value.strip()]
        for container_id in container_ids:
            self.remove(container_id.strip())
        return len(container_ids)

    def inspect(self, container_id: str) -> dict:
        raw = self._run([self._config.docker_bin, "inspect", container_id])
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "docker inspect 返回了非法 JSON",
            ) from exc
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "docker inspect 未返回容器信息",
            )
        return payload[0]

    def _run(self, args: Sequence[str]) -> str:
        try:
            result = self._runner(
                list(args),
                capture_output=True,
                text=True,
                check=False,
                timeout=self._config.command_timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "未找到 docker 可执行文件",
            ) from exc
        except OSError as exc:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "docker 命令无法启动",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "docker 命令超时",
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                f"docker 命令失败：{' '.join(args[1:3])}：{detail[:500]}",
            )
        return result.stdout or ""
