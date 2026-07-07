from common.logger import setup_logging_intercept, info, error
from aio_gateway.bootstrap import bootstrap_settings
setup_logging_intercept(bootstrap_settings.LOG_LEVEL)

import asyncio
import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aio_gateway.nacos import nacos_client_manager
from aio_gateway.settings import settings
from aio_gateway.api.router import api_router
from aio_gateway.api import deps
from sandbox.Queue.container_queue import ContainerQueue
from sandbox.Queue.file_manager import FileManager
from sandbox.Queue.watcher import Watcher
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

    # 容器队列：预热 AIO worker 池
    queue = ContainerQueue(
        image=settings.AIO_WORKER_IMAGE,
        min_idle=settings.AIO_WORKER_MIN_IDLE,
        max_total=settings.AIO_WORKER_MAX_TOTAL,
        workspace_cache=settings.AIO_WORKSPACE_CACHE_DIR,
    )
    file_mgr = FileManager(workspace_cache=settings.AIO_WORKSPACE_CACHE_DIR)
    deps.set_queue(queue)
    deps.set_file_manager(file_mgr)

    info("prefetching workers.", min_idle=settings.AIO_WORKER_MIN_IDLE)
    queue.ensure_idle_count()

    watcher = Watcher(queue, dirty_ttl=settings.AIO_WORKER_DIRTY_TTL)
    watcher.start()
    info("watcher started.")

    try:
        await nacos_client_manager.register_instance()
    except Exception as e:
        error("nacos register failed.", exc=e)

    info("ready.", service=bootstrap_settings.SERVICE_NAME, port=bootstrap_settings.SERVICE_PORT)
    yield
    info("stopping.", service=bootstrap_settings.SERVICE_NAME)

    watcher.stop()
    # 清理所有容器
    for cid in list(queue._containers.keys()):
        try:
            queue._rm_container(cid)
        except Exception:
            pass
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
app.include_router(api_router, prefix="/v1/aio")

# 手动 drain 端点
@app.post("/v1/aio/admin/drain")
async def admin_drain():
    queue = deps._queue
    if not queue:
        return R(code=503, msg="queue not initialized", data=None)
    cids = list(queue._containers.keys())
    recycled = 0
    for cid in cids:
        new_cid = queue.recycle(cid)
        if new_cid:
            recycled += 1
    return R.success({"drained": recycled})

if __name__ == "__main__":
    uvicorn.run(
        "aio_gateway.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False, workers=1,
    )
