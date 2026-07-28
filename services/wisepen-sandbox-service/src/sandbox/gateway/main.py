from common.logger import setup_logging_intercept, info, error
from sandbox.gateway.bootstrap import bootstrap_settings
setup_logging_intercept(bootstrap_settings.LOG_LEVEL)

import asyncio
import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sandbox.gateway.nacos import nacos_client_manager, register_mcp_service, deregister_mcp_service
from sandbox.gateway.settings import settings
from sandbox.gateway.api.router import api_router
from sandbox.gateway.api import deps
from sandbox.gateway.api.session_pool import SessionPool
from sandbox.gateway.api.vnc_binding import ContainerBinding
from sandbox.gateway.dev_mode import docker_available, build_mock_sandbox
from sandbox.gateway.isolation import configure_workspace
from sandbox.Queue.pool_manager import PoolConfig, ContainerPoolManager
from sandbox.mcp.server import build_sandbox_mcp
from common.web.middleware import SecurityHeaderMiddleware
from common.web.exception_handlers import setup_global_exception_handlers
from common.core.domain.responses import R

no_proxy = ",".join(filter(None, [
    os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
    "localhost, 127.0.0.1"
]))
os.environ["no_proxy"] = no_proxy
os.environ["NO_PROXY"] = no_proxy

_use_nacos = str(os.getenv("CHAT_USE_NACOS") or "").strip().lower() in ("1", "true", "yes")

# MCP app — 在 lifespan 中初始化，通过 _mcp_app 暴露给 ASGI 路由
_mcp_app = None


def _wrap_mcp_with_security(mcp):
    """为 MCP ASGI app 包裹安全上下文提取（绕过 BaseHTTPMiddleware 的兼容性问题）。"""
    from common.core.constants import SecurityConstants, CommonConstants
    from common.security.context import SecurityContextHolder, _security_context
    from common.gray.context import GrayContextHolder, _gray_context

    async def mcp_with_headers(scope, receive, send):
        if scope["type"] != "http":
            await mcp(scope, receive, send)
            return

        # ASGI header names are lowercase bytes, normalize to lowercase strings
        headers = {
            k.decode().lower(): v.decode()
            for k, v in scope.get("headers", [])
        }

        if headers.get(SecurityConstants.HEADER_FROM_SOURCE.lower(), "") != settings.FROM_SOURCE_SECRET:
            from starlette.responses import Response
            resp = Response(status_code=404, content="Not Found")
            await resp(scope, receive, send)
            return

        user_id = headers.get(SecurityConstants.HEADER_USER_ID.lower(), "")
        session_id = headers.get(SecurityConstants.HEADER_SESSION_ID.lower(), "")
        developer = headers.get(CommonConstants.GRAY_HEADER_DEV_KEY.lower(), "")

        if user_id:
            SecurityContextHolder.set_user_id(user_id)
        if session_id:
            SecurityContextHolder.set_session_id(session_id)
        if developer:
            GrayContextHolder.set_developer_tag(developer)

        try:
            await mcp(scope, receive, send)
        finally:
            _security_context.set({})
            _gray_context.set("")

    return mcp_with_headers


@asynccontextmanager
async def lifespan(app: FastAPI):
    info("starting.", service=bootstrap_settings.APP_NAME)

    # 容器池管理
    docker_ok = docker_available()
    vnc_binding = None
    pool = None

    if docker_ok:
        cfg = PoolConfig(
            image=settings.WORKER_IMAGE,
            min_idle=settings.WORKER_MIN_IDLE,
            max_total=settings.WORKER_MAX_TOTAL,
            workspace_cache=settings.WORKSPACE_CACHE_DIR,
            dirty_ttl=settings.WORKER_DIRTY_TTL,
        )
        configure_workspace(cfg.workspace_root, cfg.virtual_root)
        pool = ContainerPoolManager(cfg)
        pool.start()
        deps.set_queue(pool.queue)
        deps.set_file_manager(pool.file_manager)

        session_pool = SessionPool(pool)
        deps.set_session_pool(session_pool)
        mcp_server = build_sandbox_mcp(session_pool)

        vnc_binding = ContainerBinding(pool)
        deps.set_vnc_binding(vnc_binding)
    else:
        mock = build_mock_sandbox()
        session_pool = mock  # MockSandbox duck-types as session pool
        deps.set_queue(mock)
        deps.set_file_manager(mock)
        deps.set_session_pool(mock)
        mcp_server = build_sandbox_mcp(mock, executor=mock.execute)
        import sandbox.gateway.container_utils as cu
        cu._executor_override = mock.execute
        info("VNC disabled in dev mode.")

    # Mount MCP server (ASGI router bypasses BaseHTTPMiddleware, wraps own security)
    global _mcp_app
    _mcp_app = _wrap_mcp_with_security(mcp_server.streamable_http_app())

    vnc_cleanup_task = None
    if vnc_binding is not None:
        shutdown_event = asyncio.Event()

        async def _vnc_cleanup_loop():
            while not shutdown_event.is_set():
                try:
                    await asyncio.sleep(300)
                    released = vnc_binding.cleanup_idle()
                    if released:
                        info("vnc idle cleanup.", released=released)
                except asyncio.CancelledError:
                    break
        vnc_cleanup_task = asyncio.create_task(_vnc_cleanup_loop())

    session_cleanup_task = None
    if session_pool is not None:
        session_shutdown = asyncio.Event()

        async def _session_cleanup_loop():
            while not session_shutdown.is_set():
                try:
                    await asyncio.sleep(300)
                    released = session_pool.cleanup_idle()
                    if released:
                        info("session idle cleanup.", released=released)
                except asyncio.CancelledError:
                    break
        session_cleanup_task = asyncio.create_task(_session_cleanup_loop())

    if _use_nacos:
        try:
            await nacos_client_manager.register_instance()
        except Exception as e:
            error("nacos register failed.", exc=e)
        try:
            await register_mcp_service()
        except Exception as e:
            error("nacos mcp register failed.", exc=e)
    else:
        info("nacos registration skipped (CHAT_USE_NACOS not set).")

    # MCP session manager 需要 task group — 手动管理生命周期
    async with mcp_server._session_manager.run():
        info("ready.", service=bootstrap_settings.SERVICE_NAME, port=bootstrap_settings.SERVICE_PORT, mcp="ok")
        yield

    info("stopping.", service=bootstrap_settings.SERVICE_NAME)

    if vnc_cleanup_task is not None:
        shutdown_event.set()
        vnc_cleanup_task.cancel()
    if session_cleanup_task is not None:
        session_shutdown.set()
        session_cleanup_task.cancel()
    if pool is not None:
        pool.stop()
    info("stopped.", service=bootstrap_settings.SERVICE_NAME)

    if _use_nacos:
        try:
            await nacos_client_manager.deregister_instance()
        except Exception as e:
            error("nacos deregister failed.", exc=e)
        try:
            await deregister_mcp_service()
        except Exception as e:
            error("nacos mcp deregister failed.", exc=e)


fastapi_app = FastAPI(title=bootstrap_settings.APP_NAME, lifespan=lifespan, docs_url="/docs")

fastapi_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
fastapi_app.add_middleware(SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET)
setup_global_exception_handlers(fastapi_app, is_dev=bootstrap_settings.IS_DEV)
fastapi_app.include_router(api_router, prefix="/v1/sandbox/gateway")

# 手动 drain 端点
@fastapi_app.post("/v1/sandbox/gateway/admin/drain")
async def admin_drain():
    binding = deps._vnc_binding
    if binding:
        return R.success(binding.stats())
    return R(code=503, msg="vnc binding not initialized", data=None)


async def app(scope, receive, send):
    """顶层 ASGI 路由：/mcp 直连 MCP server（绕过 BaseHTTPMiddleware），其余走 FastAPI。"""
    if scope["type"] == "http" and scope["path"].startswith("/mcp") and _mcp_app is not None:
        # Strip /mcp prefix and set root_path, same as Starlette Mount does
        mcp_scope = dict(scope)
        mcp_scope["path"] = scope["path"][len("/mcp"):] or "/"
        mcp_scope["root_path"] = scope.get("root_path", "") + "/mcp"
        await _mcp_app(mcp_scope, receive, send)
    else:
        await fastapi_app(scope, receive, send)


if __name__ == "__main__":
    uvicorn.run(
        "sandbox.gateway.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False, workers=1,
    )
