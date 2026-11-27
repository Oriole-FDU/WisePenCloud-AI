import os
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"websockets\.legacy")

from common.logger import error, info, setup_logging_intercept
from common.observability import instrument_fastapi_app, setup_observability
from wisepen_mcp.core.config.bootstrap_settings import bootstrap_settings

setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from starlette.routing import Route

from common.mcp import assert_tool_catalog_consistent, build_tool_catalog_router
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from wisepen_mcp.capabilities import build_mcp_server
from wisepen_mcp.container import container
from wisepen_mcp.core.config.app_settings import settings
from wisepen_mcp.core.config.nacos import nacos_client_manager
from wisepen_mcp.oauth import build_oauth_router

no_proxy = ",".join(filter(None, [
    os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
    "localhost, 127.0.0.1",
]))
os.environ["no_proxy"] = no_proxy
os.environ["NO_PROXY"] = no_proxy


mcp_server = build_mcp_server(
    ai_asset_client=container.ai_asset_client(),
    note_collab_client=container.note_collab_client(),
    resource_client=container.resource_client(),
    document_client=container.document_client(),
    note_client=container.note_client(),
    user_client=container.user_client(),
)
mcp_app = mcp_server.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)
    async with mcp_server.session_manager.run():
        tools = await mcp_server.list_tools()
        assert_tool_catalog_consistent({tool.name for tool in tools})
        try:
            await nacos_client_manager.register_instance()
        except Exception as e:
            error("nacos instance register failed.", exc=e)

        info("service ready.", service=bootstrap_settings.SERVICE_NAME, port=bootstrap_settings.SERVICE_PORT)
        yield

        info("service stopping.", service=bootstrap_settings.SERVICE_NAME)
        try:
            await container.rpc_client().aclose()
        except Exception as e:
            error("rpc client close failed.", exc=e)
        try:
            await container.redis_client().aclose()
        except Exception as e:
            error("redis client close failed.", exc=e)
        try:
            await container.service_discovery().close()
        except Exception as e:
            error("service discovery close failed.", exc=e)
        try:
            await nacos_client_manager.deregister_instance()
        except Exception as e:
            error("nacos instance deregister failed.", exc=e)


app = FastAPI(title=bootstrap_settings.APP_NAME, lifespan=lifespan, docs_url="/docs")
instrument_fastapi_app(app)
app.add_middleware(SecurityHeaderMiddleware, from_source_secret=settings.security.from_source_secret)
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": bootstrap_settings.SERVICE_NAME}


app.include_router(build_tool_catalog_router())
app.include_router(build_oauth_router(container.redis_client()))


class _McpAsgiEndpoint:
    """把 StreamableHTTP 的 handler 暴露成 ASGI endpoint，供 Starlette Route 直接挂载"""

    def __init__(self, handler) -> None:
        self._handler = handler

    async def __call__(self, scope, receive, send) -> None:
        await self._handler(scope, receive, send)


# 外部 MCP 客户端（Delta 等）请求的是 `/mcp`（无尾斜杠）
# app.mount 只覆盖 `/mcp/`，FastAPI 的 Router 会对 `/mcp` 做 307 重定向
# 而 MCP 客户端可能不跟随重定向（Delta不跟随），导致工具发现一直失败
# 这里把 streamable HTTP handler 直接注册到 `/mcp`，并保留 mount 以兼容内部调用方的 `/mcp/`
app.mount("/mcp", mcp_app)
app.router.routes.append(
    Route(
        "/mcp",
        # 把进来的请求原样转给 MCP 的 handler
        endpoint=_McpAsgiEndpoint(mcp_server.session_manager.handle_request),
        methods=["GET", "POST", "DELETE"],
    )
)


if __name__ == "__main__":
    uvicorn.run(
        "wisepen_mcp.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )
