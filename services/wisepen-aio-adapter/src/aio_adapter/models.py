from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterConfig:
    docker_bin: str = "docker"
    image: str = "ghcr.io/agent-infra/sandbox:latest"
    host: str = "127.0.0.1"
    api_port: int = 8080
    network: str | None = None
    request_timeout_seconds: float = 30.0
    warmup_timeout_seconds: float = 60.0
    workdir: str = "/workspace"
