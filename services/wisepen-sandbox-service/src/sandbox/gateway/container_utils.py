"""Container addressing utilities for Sandbox Gateway.

Manager and workers share a Docker network (sandbox-net).
Internal communication uses container-name DNS when running inside Docker,
or localhost:mapped_port when running on the host machine.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any
import httpx

VNC_PORT = 8080
WEBSOCKIFY_PORT = 6080
SANDBOX_NETWORK = "sandbox-net"

_client = httpx.AsyncClient(timeout=30.0)


def _inside_docker() -> bool:
    """Detect if we're running inside a Docker container."""
    return os.path.exists("/.dockerenv")


def _container_name(cid: str) -> str:
    """Get the container name (without leading /)."""
    raw = subprocess.run(
        ["docker", "inspect", "-f", "{{.Name}}", cid],
        capture_output=True, text=True, timeout=5,
    )
    name = raw.stdout.strip().lstrip("/")
    if not name:
        raise RuntimeError(f"container {cid[:12]} has no name")
    return name


def container_url(cid: str, port: int = VNC_PORT) -> str:
    """Return http://{host}:{port} — DNS inside Docker, localhost on host."""
    if _inside_docker():
        name = _container_name(cid)
        return f"http://{name}:{port}"
    mapped = container_host_port(cid, port)
    return f"http://127.0.0.1:{mapped}"


def container_ws_url(cid: str, port: int = WEBSOCKIFY_PORT) -> str:
    """Return ws://{host}:{port} — DNS inside Docker, localhost on host."""
    if _inside_docker():
        name = _container_name(cid)
        return f"ws://{name}:{port}"
    mapped = container_host_port(cid, port)
    return f"ws://127.0.0.1:{mapped}"


def container_host_port(cid: str, port: int = VNC_PORT) -> str:
    """Return host-mapped port (for user-facing VNC URLs from outside Docker)."""
    raw = subprocess.run(
        ["docker", "port", cid, str(port)],
        capture_output=True, text=True, timeout=5,
    )
    mapped = raw.stdout.strip()
    if not mapped:
        raise RuntimeError(f"container {cid[:12]} port {port} not mapped")
    return mapped.rsplit(":", 1)[-1].strip()


_executor_override: Any = None


async def execute_on_container(cid: str, method: str,
                               path: str, body: dict) -> dict:
    if _executor_override is not None:
        return await _executor_override(cid, method, path, body)
    base = container_url(cid, VNC_PORT)
    url = f"{base}{path}"
    resp = await _client.request(method, url, json=body)
    resp.raise_for_status()
    return resp.json()
