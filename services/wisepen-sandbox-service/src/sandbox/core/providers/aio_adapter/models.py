from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterConfig:
    docker_bin: str = "docker"
    image: str = "ghcr.io/agent-infra/sandbox:latest"
    host: str = "127.0.0.1"
    api_port: int = 8080
    vnc_port: int = 6080
    network: str | None = None
    request_timeout_seconds: float = 30.0
    warmup_timeout_seconds: float = 60.0
    # 沙箱镜像将 /home/gem 暴露为可写用户目录。
    workdir: str = "/home/gem"
    command_timeout_seconds: float = 30.0
    e2e_label: bool = False
    tty: bool = True
    owner_id: str = "wisepen-sandbox-service"
    public_vnc_url_template: str = ""
    public_websocket_url_template: str = ""
