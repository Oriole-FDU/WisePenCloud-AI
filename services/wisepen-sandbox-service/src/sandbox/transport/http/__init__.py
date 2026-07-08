from sandbox.core.lazy import make_getattr

__all__ = [
    "ExecuteRequestDTO",
    "ExecuteResponseDTO",
    "HttpServer",
    "SandboxHttpApp",
    "StdHttpServer",
    "build_default_http_server",
    "build_sandbox_http_handler",
]

_LAZY = {
    "ExecuteRequestDTO": "sandbox.transport.http.schemas",
    "ExecuteResponseDTO": "sandbox.transport.http.schemas",
    "HttpServer": "sandbox.transport.http.server",
    "SandboxHttpApp": "sandbox.transport.http.server",
    "StdHttpServer": "sandbox.transport.http.server",
    "build_default_http_server": "sandbox.transport.http.server",
    "build_sandbox_http_handler": "sandbox.transport.http.server",
}

__getattr__ = make_getattr(_LAZY)
