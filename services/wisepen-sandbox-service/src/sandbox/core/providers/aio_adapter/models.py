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
    health_timeout_seconds: float = 3.0
    health_retry_interval_seconds: float = 0.5
    # 沙箱镜像将 /home/gem 暴露为可写用户目录。
    workdir: str = "/home/gem"
    # 用户工作区由 DockerWorkspaceTransfer 导入和导出；必须与 PathPolicy 使用同一根目录。
    workspace_root: str = "/home/gem/workspaces"
    command_timeout_seconds: float = 30.0
    create_max_attempts: int = 3
    create_retry_backoff_seconds: float = 0.2
    e2e_label: bool = False
    tty: bool = True
    owner_id: str = "wisepen-sandbox-service"
    browser_no_sandbox: str = ""
    public_vnc_url_template: str = ""
    public_websocket_url_template: str = ""
