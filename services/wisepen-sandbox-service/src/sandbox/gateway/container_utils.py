"""Container addressing utilities for Sandbox Gateway.

Manager and workers share a Docker network (sandbox-net).
Internal communication uses container-name DNS.
External (VNC) uses docker port to find mapped host port.
"""
from __future__ import annotations

import subprocess
from typing import Any
import httpx

VNC_PORT = 8080
WEBSOCKIFY_PORT = 6080
SANDBOX_NETWORK = "sandbox-net"

_client = httpx.AsyncClient(timeout=30.0)


def _container_name(cid: str) -> str:
    """Get the container name (without leading /) for DNS resolution."""
    raw = subprocess.run(
        ["docker", "inspect", "-f", "{{.Name}}", cid],
        capture_output=True, text=True, timeout=5,
    )
    name = raw.stdout.strip().lstrip("/")
    if not name:
        raise RuntimeError(f"container {cid[:12]} has no name")
    return name


def container_url(cid: str, port: int = VNC_PORT) -> str:
    """Return http://{container_name}:{port} via shared Docker network."""
    name = _container_name(cid)
    return f"http://{name}:{port}"


def container_ws_url(cid: str, port: int = WEBSOCKIFY_PORT) -> str:
    """Return ws://{container_name}:{port} for WebSocket via shared network."""
    name = _container_name(cid)
    return f"ws://{name}:{port}"


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


async def execute_on_container(cid: str, method: str,
                               path: str, body: dict) -> dict:
    base = container_url(cid, VNC_PORT)
    url = f"{base}{path}"
    resp = await _client.request(method, url, json=body)
    resp.raise_for_status()
    return resp.json()
