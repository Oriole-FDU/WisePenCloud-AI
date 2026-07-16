from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from typing import Sequence

from sandbox.domain.entities import SandboxSpec
from sandbox.core.providers.aio_adapter.errors import ContainerError
from sandbox.core.providers.aio_adapter.models import AdapterConfig


@dataclass(frozen=True)
class ContainerHandle:
    container_id: str
    endpoint: str


class DockerRuntime:
    def __init__(self, config: AdapterConfig, runner=subprocess.run) -> None:
        self._config = config
        self._runner = runner

    @property
    def workdir(self) -> str:
        return self._config.workdir

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
            f"wisepen.sandbox_id={name}",
            "-p",
            f"{self._config.host}::{self._config.api_port}",
        ]
        if self._config.e2e_label:
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
            raise ContainerError("container creation returned an empty id", retryable=True)
        port = self._run(
            [self._config.docker_bin, "port", container_id, f"{self._config.api_port}/tcp"]
        ).strip()
        host_port = port.rsplit(":", 1)[-1]
        return ContainerHandle(container_id, f"http://{self._config.host}:{host_port}")

    def remove(self, container_id: str) -> None:
        try:
            self._run([self._config.docker_bin, "rm", "-f", container_id])
        except ContainerError as exc:
            if "No such container" not in str(exc):
                raise

    def inspect(self, container_id: str) -> dict:
        raw = self._run([self._config.docker_bin, "inspect", container_id])
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ContainerError("docker inspect returned invalid JSON") from exc
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise ContainerError("docker inspect returned no container")
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
            raise ContainerError("docker binary not found") from exc
        except OSError as exc:
            raise ContainerError("docker command could not be started", retryable=True) from exc
        except subprocess.TimeoutExpired as exc:
            raise ContainerError("docker command timed out", retryable=True) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise ContainerError(
                f"docker command failed: {' '.join(args[1:3])}: {detail[:500]}",
                retryable=True,
            )
        return result.stdout or ""
