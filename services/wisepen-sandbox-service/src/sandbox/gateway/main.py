from common.logger import setup_logging_intercept, info, error
from sandbox.gateway.bootstrap import bootstrap_settings
setup_logging_intercept(bootstrap_settings.LOG_LEVEL)

import asyncio
import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sandbox.gateway.nacos import nacos_client_manager
from sandbox.gateway.settings import settings
from sandbox.gateway.api.router import api_router
from sandbox.gateway.api import deps
from sandbox.gateway.api.vnc_binding import ContainerBinding
from sandbox.Queue.pool_manager import PoolConfig, ContainerPoolManager
from common.web.middleware import SecurityHeaderMiddleware
from common.web.exception_handlers import setup_global_exception_handlers
from common.core.domain.responses import R

no_proxy = ",".join(filter(None, [
    os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
    "localhost, 127.0.0.1"
]))
os.environ["no_proxy"] = no_proxy
os.environ["NO_PROXY"] = no_proxy


@asynccontextmanager
async def lifespan(app: FastAPI):
    info("starting.", service=bootstrap_settings.APP_NAME)

    # 容器池管理
    pool = ContainerPoolManager(PoolConfig(
        image=settings.WORKER_IMAGE,
        min_idle=settings.WORKER_MIN_IDLE,
        max_total=settings.WORKER_MAX_TOTAL,
        workspace_cache=settings.AIO_WORKSPACE_CACHE_DIR,
        dirty_ttl=settings.WORKER_DIRTY_TTL,
    ))
    pool.start()
    deps.set_queue(pool.queue)
    deps.set_file_manager(pool.file_manager)

    # VNC 绑定管理器
    vnc_binding = ContainerBinding(pool)
    deps.set_vnc_binding(vnc_binding)

    shutdown_event = asyncio.Event()

    async def _vnc_cleanup_loop():
        while not shutdown_event.is_set():
            try:
                await asyncio.sleep(300)  # 每 5 分钟扫描
                released = vnc_binding.cleanup_idle()
                if released:
                    info("vnc idle cleanup.", released=released)
            except asyncio.CancelledError:
                break
    vnc_cleanup_task = asyncio.create_task(_vnc_cleanup_loop())

    try:
        await nacos_client_manager.register_instance()
    except Exception as e:
        error("nacos register failed.", exc=e)

    info("ready.", service=bootstrap_settings.SERVICE_NAME, port=bootstrap_settings.SERVICE_PORT)
    yield
    info("stopping.", service=bootstrap_settings.SERVICE_NAME)

    shutdown_event.set()
    vnc_cleanup_task.cancel()
    pool.stop()
    info("stopped.", service=bootstrap_settings.SERVICE_NAME)

    try:
        await nacos_client_manager.deregister_instance()
    except Exception as e:
        error("nacos deregister failed.", exc=e)


app = FastAPI(title=bootstrap_settings.APP_NAME, lifespan=lifespan, docs_url="/docs")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET)
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)
app.include_router(api_router, prefix="/v1/sandbox/gateway")

# 手动 drain 端点
@app.post("/v1/sandbox/gateway/admin/drain")
async def admin_drain():
    binding = deps._vnc_binding
    if binding:
        return R.success(binding.stats())
    return R(code=503, msg="vnc binding not initialized", data=None)

if __name__ == "__main__":
    uvicorn.run(
        "sandbox.gateway.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False, workers=1,
    )
