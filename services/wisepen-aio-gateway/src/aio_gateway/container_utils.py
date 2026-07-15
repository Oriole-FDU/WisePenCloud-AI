"""Shared container utilities for AIO Gateway endpoints.

Replaces duplicated _container_ip and _execute_on_container across file.py / shell.py / vnc.py.
"""
from __future__ import annotations

import subprocess
from typing import Any, Dict
import httpx

VNC_PORT = 8080
WEBSOCKIFY_PORT = 6080

_client = httpx.AsyncClient(timeout=30.0)


def container_ip(cid: str) -> str:
    raw = subprocess.run(
        ["docker", "inspect", "-f", "{{.NetworkSettings.IPAddress}}", cid],
        capture_output=True, text=True, timeout=5,
    )
    return raw.stdout.strip()


async def execute_on_container(cid: str, method: str,
                               path: str, body: dict) -> dict:
    ip = container_ip(cid)
    url = f"http://{ip}:{VNC_PORT}{path}"
    resp = await _client.request(method, url, json=body)
    resp.raise_for_status()
    return resp.json()
