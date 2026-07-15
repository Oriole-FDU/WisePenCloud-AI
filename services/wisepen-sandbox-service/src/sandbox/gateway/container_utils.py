"""Shared container utilities for Sandbox Gateway endpoints.

On Docker Desktop (Windows/Mac), containers don't get a routable IP.
Use port mapping: docker run -p 127.0.0.1::8080 maps container port 8080
to a random host port.  Use `docker port` to look up the mapping.
"""
from __future__ import annotations

import subprocess
from typing import Any
import httpx

VNC_PORT = 8080
WEBSOCKIFY_PORT = 6080

_client = httpx.AsyncClient(timeout=30.0)


def container_url(cid: str, port: int = VNC_PORT) -> str:
    """Return http://127.0.0.1:{mapped_port} for the container's exposed port."""
    raw = subprocess.run(
        ["docker", "port", cid, str(port)],
        capture_output=True, text=True, timeout=5,
    )
    mapped = raw.stdout.strip()
    if not mapped:
        raise RuntimeError(
            f"container {cid[:12]} port {port} not mapped (add -p to docker run)"
        )
    # docker port returns "0.0.0.0:32768" or "[::]:32768"
    host_port = mapped.rsplit(":", 1)[-1].strip()
    return f"http://127.0.0.1:{host_port}"


def container_ws_url(cid: str, port: int = WEBSOCKIFY_PORT) -> str:
    """Return ws://127.0.0.1:{mapped_port} for WebSocket connections."""
    raw = subprocess.run(
        ["docker", "port", cid, str(port)],
        capture_output=True, text=True, timeout=5,
    )
    mapped = raw.stdout.strip()
    if not mapped:
        raise RuntimeError(f"container {cid[:12]} port {port} not mapped")
    host_port = mapped.rsplit(":", 1)[-1].strip()
    return f"ws://127.0.0.1:{host_port}"


async def execute_on_container(cid: str, method: str,
                               path: str, body: dict) -> dict:
    base = container_url(cid, VNC_PORT)
    url = f"{base}{path}"
    resp = await _client.request(method, url, json=body)
    resp.raise_for_status()
    return resp.json()
