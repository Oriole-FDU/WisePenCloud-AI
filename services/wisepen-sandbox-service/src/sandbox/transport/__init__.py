from __future__ import annotations

from typing import Any

__all__ = [
    "ExecuteRequestDTO",
    "ExecuteResponseDTO",
    "HttpServer",
    "SandboxHttpApp",
    "StdHttpServer",
    "build_default_http_server",
    "build_sandbox_http_handler",
]


def __getattr__(name: str) -> Any:
    if name in ("ExecuteRequestDTO", "ExecuteResponseDTO"):
        from sandbox.transport.http.schemas import ExecuteRequestDTO, ExecuteResponseDTO

        return locals()[name]

    if name in (
        "HttpServer",
        "SandboxHttpApp",
        "StdHttpServer",
        "build_default_http_server",
        "build_sandbox_http_handler",
    ):
        from sandbox.transport.http.server import (
            HttpServer,
            SandboxHttpApp,
            StdHttpServer,
            build_default_http_server,
            build_sandbox_http_handler,
        )

        return locals()[name]

    raise AttributeError(name)
