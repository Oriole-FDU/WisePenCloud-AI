from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Sequence

from common.core.exceptions import ServiceException
from common.logger import debug, error, info, warn

from sandbox.domain.entities import SandboxSpec
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.core.providers.aio_adapter.models import AdapterConfig


@dataclass(frozen=True)
class ContainerHandle:
    container_id: str
    endpoint: str
    public_vnc_url: str | None = None
    public_websocket_url: str | None = None


@dataclass(frozen=True)
class ManagedContainer:
    container_id: str
    container_name: str
    labels: dict[str, str]
    running: bool
    endpoint: str | None = None
    public_vnc_url: str | None = None
    public_websocket_url: str | None = None


class DockerCommandFailure(ServiceException):
    """保留 Docker CLI 的退出状态，供存在副作用的命令判定是否可恢复。"""

    def __init__(self, message: str, returncode: int) -> None:
        super().__init__(SandboxErrorCode.SANDBOX_UNAVAILABLE, message)
        self.returncode = returncode


class DockerRuntime:
    """通过 Docker CLI 管理 all-in-one-sandbox 容器。"""

    def __init__(self, config: AdapterConfig, runner=subprocess.run, sleeper=sleep) -> None:
        self._config = config
        self._runner = runner
        self._sleeper = sleeper
        self._owner_id = f"{config.owner_id}-{uuid.uuid4().hex}"

    @property
    def workdir(self) -> str:
        return self._config.workdir

    @property
    def owner_id(self) -> str:
        return self._owner_id

    def validate_deployment(self) -> None:
        """Fail before readiness when Docker, image, or network is unavailable."""
        info(
            "Docker 部署预检开始",
            docker_bin=self._config.docker_bin,
            image=self._config.image,
            network=self._config.network,
        )
        try:
            self._run(
                [self._config.docker_bin, "version", "--format", "{{.Server.Version}}"]
            )
            self._run([self._config.docker_bin, "image", "inspect", self._config.image])
            if self._config.network:
                self._run(
                    [self._config.docker_bin, "network", "inspect", self._config.network]
                )
        except ServiceException as exc:
            error(
                "Docker 部署预检失败",
                exc=exc,
                image=self._config.image,
                network=self._config.network,
            )
            raise
        info(
            "Docker 部署预检完成",
            image=self._config.image,
            network=self._config.network,
        )

    def create(self, spec: SandboxSpec) -> ContainerHandle:
        name = f"wisepen-aio-{uuid.uuid4().hex[:12]}"
        image = spec.image or self._config.image
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
            # 该 label 只表示创建时未绑定用户；用户绑定以 Repository/Mongo 为准。
            "wisepen.binding=unbound",
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
        args.append(image)
        info(
            "Docker 开始创建 AIO 容器",
            container_name=name,
            image=image,
            docker_bin=self._config.docker_bin,
            docker_host=self._config.host,
            container_api_port=self._config.api_port,
            tty=self._config.tty,
            e2e_label=self._config.e2e_label,
            network=self._config.network,
            environment_keys=sorted(spec.environment),
        )
        container_id = self._run_create(args, name).strip()
        if not container_id:
            error(
                "Docker 创建 AIO 容器返回空容器 ID",
                container_name=name,
                image=image,
            )
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "容器创建返回了空 id",
            )
        info(
            "Docker 创建 AIO 容器完成",
            container_name=name,
            container_id=container_id,
            image=image,
        )
        handle = self._container_handle(container_id, name)
        info(
            "Docker 解析 AIO 容器连接信息完成",
            container_id=container_id,
            endpoint=handle.endpoint,
            network=self._config.network,
            has_public_vnc_url=handle.public_vnc_url is not None,
            has_public_websocket_url=handle.public_websocket_url is not None,
        )
        return handle

    def _run_create(self, args: Sequence[str], container_name: str) -> str:
        """运行 docker run，并处理 CLI 在结果返回前被信号中断的情况。"""
        had_ambiguous_result = False
        for attempt in range(1, self._config.create_max_attempts + 1):
            try:
                return self._run(args)
            except DockerCommandFailure as exc:
                if exc.returncode >= 0 and not had_ambiguous_result:
                    raise

                container_id = self._find_container_id(container_name)
                if container_id:
                    info(
                        "Docker 创建命令未返回结果但容器已创建，复用容器",
                        container_name=container_name,
                        container_id=container_id,
                        returncode=exc.returncode,
                        attempt=attempt,
                    )
                    return container_id

                if exc.returncode >= 0 or attempt == self._config.create_max_attempts:
                    raise

                had_ambiguous_result = True
                delay_seconds = self._config.create_retry_backoff_seconds * attempt
                warn(
                    "Docker 创建命令被信号中断，确认容器不存在后重试",
                    container_name=container_name,
                    returncode=exc.returncode,
                    attempt=attempt,
                    max_attempts=self._config.create_max_attempts,
                    delay_seconds=delay_seconds,
                )
                self._sleeper(delay_seconds)

        raise AssertionError("docker create retry loop exited unexpectedly")

    def _find_container_id(self, container_name: str) -> str | None:
        """不确定 docker run 是否提交时，按唯一容器名确认实际结果。"""
        try:
            result = self._runner(
                [
                    self._config.docker_bin,
                    "container",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    container_name,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=self._config.command_timeout_seconds,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            warn(
                "Docker 创建结果确认命令无法执行",
                exc=exc,
                container_name=container_name,
            )
            return None
        if result.returncode != 0:
            debug(
                "Docker 创建结果确认未找到容器",
                container_name=container_name,
                returncode=result.returncode,
                detail=((result.stderr or result.stdout or "").strip())[:500],
            )
            return None
        container_id = (result.stdout or "").strip()
        if not container_id:
            warn("Docker 创建结果确认返回空容器 ID", container_name=container_name)
            return None
        return container_id

    def _host_port(self, container_id: str, container_port: int) -> str:
        port = self._run(
            [self._config.docker_bin, "port", container_id, f"{container_port}/tcp"]
        ).strip()
        host_port = port.rsplit(":", 1)[-1]
        if not host_port.isdigit():
            error(
                "Docker 容器端口映射非法",
                container_id=container_id,
                container_port=container_port,
                raw_port=port[:500],
            )
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                f"容器端口 {container_port} 未正确映射",
            )
        info(
            "Docker 查询容器动态端口完成",
            container_id=container_id,
            container_port=container_port,
            host_port=host_port,
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
            url = template.format(
                host=self._config.host,
                port=host_port,
                container_id=container_id,
                container_name=container_name,
            )
            info(
                "Docker 生成公开访问地址完成",
                container_id=container_id,
                container_name=container_name,
                host_port=host_port,
            )
            return url
        except (KeyError, ValueError) as exc:
            error(
                "Docker 公开访问 URL 模板非法",
                exc=exc,
                container_id=container_id,
                container_name=container_name,
                template_configured=True,
            )
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "public VNC URL 模板非法",
            ) from exc

    def _container_handle(self, container_id: str, container_name: str) -> ContainerHandle:
        api_host_port = self._host_port(container_id, self._config.api_port)
        vnc_host_port = self._host_port(container_id, self._config.vnc_port)
        endpoint = (
            f"http://{container_name}:{self._config.api_port}"
            if self._config.network
            else f"http://{self._config.host}:{api_host_port}"
        )
        return ContainerHandle(
            container_id,
            endpoint,
            self._format_public_url(
                self._config.public_vnc_url_template,
                container_id,
                container_name,
                vnc_host_port,
            ),
            self._format_public_url(
                self._config.public_websocket_url_template,
                container_id,
                container_name,
                vnc_host_port,
            ),
        )

    def _optional_container_handle(
        self, container_id: str, container_name: str
    ) -> ContainerHandle | None:
        try:
            return self._container_handle(container_id, container_name)
        except ServiceException as exc:
            # 启动认领只需要发现和销毁能力；退出或端口异常的容器不应阻断孤儿清理。
            warn(
                "Docker 发现容器但无法解析 endpoint，启动对账将按不可用处理",
                exc=exc,
                container_id=container_id,
                container_name=container_name,
            )
            return None

    def remove(self, container_id: str) -> None:
        info("Docker 开始销毁 AIO 容器", container_id=container_id)
        try:
            self._run([self._config.docker_bin, "rm", "-f", container_id])
        except ServiceException as exc:
            # 销毁按幂等语义处理，容器已不存在时视为清理成功。
            if "No such container" not in str(exc):
                error("Docker 销毁 AIO 容器失败", exc=exc, container_id=container_id)
                raise
            info("Docker AIO 容器已不存在，跳过销毁", container_id=container_id)
        info("Docker 销毁 AIO 容器完成", container_id=container_id)

    def cleanup_owned(self) -> int:
        info("Docker 开始清理当前服务拥有的 AIO 容器", owner_id=self._owner_id)
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
        info(
            "Docker 清理当前服务拥有的 AIO 容器完成",
            owner_id=self._owner_id,
            count=len(container_ids),
        )
        return len(container_ids)

    def list_managed(self) -> list[ManagedContainer]:
        """List managed AIO containers for startup reconciliation.

        This intentionally scans all Wisepen managed AIO workers instead of the
        current owner id.  A restarted process gets a new owner label, while the
        repository remains the authority that decides whether a discovered
        container is current or orphaned.
        """

        raw = self._run(
            [
                self._config.docker_bin,
                "ps",
                "-aq",
                "--filter",
                "label=wisepen.managed=true",
                "--filter",
                "label=wisepen.role=aio-worker",
            ]
        )
        managed: list[ManagedContainer] = []
        for container_id in [value.strip() for value in raw.splitlines() if value.strip()]:
            try:
                payload = self.inspect(container_id)
            except ServiceException as exc:
                warn(
                    "Docker 启动对账跳过无法 inspect 的容器",
                    exc=exc,
                    container_id=container_id,
                )
                continue
            labels = payload.get("Config", {}).get("Labels") or {}
            if not isinstance(labels, dict):
                labels = {}
            state = payload.get("State") or {}
            running = bool(state.get("Running")) or state.get("Status") == "running"
            container_name = str(payload.get("Name") or "").lstrip("/") or container_id
            handle = self._optional_container_handle(container_id, container_name)
            managed.append(
                ManagedContainer(
                    container_id=container_id,
                    container_name=container_name,
                    labels={str(key): str(value) for key, value in labels.items()},
                    running=running,
                    endpoint=handle.endpoint if handle else None,
                    public_vnc_url=handle.public_vnc_url if handle else None,
                    public_websocket_url=handle.public_websocket_url if handle else None,
                )
            )
        info("Docker 启动对账发现 AIO 容器", count=len(managed))
        return managed

    def inspect(self, container_id: str) -> dict:
        info("Docker 开始检查 AIO 容器", container_id=container_id)
        raw = self._run([self._config.docker_bin, "inspect", container_id])
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            error("Docker inspect 返回非法 JSON", exc=exc, container_id=container_id)
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "docker inspect 返回了非法 JSON",
            ) from exc
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            error("Docker inspect 未返回容器信息", container_id=container_id)
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "docker inspect 未返回容器信息",
            )
        info("Docker 检查 AIO 容器完成", container_id=container_id)
        return payload[0]

    def _run(self, args: Sequence[str]) -> str:
        command = self._redact_args(args)
        started = monotonic()
        debug(
            "Docker 命令开始执行",
            command=" ".join(command),
            timeout_seconds=self._config.command_timeout_seconds,
        )
        try:
            result = self._runner(
                list(args),
                capture_output=True,
                text=True,
                check=False,
                timeout=self._config.command_timeout_seconds,
            )
        except FileNotFoundError as exc:
            error("Docker 命令未找到可执行文件", exc=exc, command=" ".join(command))
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "未找到 docker 可执行文件",
            ) from exc
        except OSError as exc:
            error("Docker 命令无法启动", exc=exc, command=" ".join(command))
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "docker 命令无法启动",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            error(
                "Docker 命令执行超时",
                exc=exc,
                command=" ".join(command),
                elapsed_ms=round((monotonic() - started) * 1000, 2),
            )
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "docker 命令超时",
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            error(
                "Docker 命令执行失败",
                command=" ".join(command),
                returncode=result.returncode,
                detail=detail[:500],
                elapsed_ms=round((monotonic() - started) * 1000, 2),
            )
            raise DockerCommandFailure(
                f"docker 命令失败：{' '.join(args[1:3])}：{detail[:500]}",
                result.returncode,
            )
        debug(
            "Docker 命令执行完成",
            command=" ".join(command),
            returncode=result.returncode,
            stdout_length=len(result.stdout or ""),
            elapsed_ms=round((monotonic() - started) * 1000, 2),
        )
        return result.stdout or ""

    @staticmethod
    def _redact_args(args: Sequence[str]) -> list[str]:
        """隐藏 docker -e 参数值，避免调试命令意外输出敏感配置。"""
        redacted: list[str] = []
        redact_next = False
        for arg in args:
            value = str(arg)
            if redact_next:
                redacted.append(f"{value.split('=', 1)[0]}=<redacted>")
                redact_next = False
                continue
            if value.startswith("--env="):
                redacted.append(f"--env={value.removeprefix('--env=').split('=', 1)[0]}=<redacted>")
                continue
            redacted.append(value)
            redact_next = value in ("-e", "--env")
        return redacted
