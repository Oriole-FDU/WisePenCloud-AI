from common.logger import setup_logging_intercept, log_event, log_error
from aio_gateway.settings import bootstrap_settings
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
from aio_gateway.cleanup import WorkspaceCleaner
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
    log_event(f"{bootstrap_settings.APP_NAME} 启动")

    # 创建 WorkspaceCleaner 并注入到 deps
    cleaner = WorkspaceCleaner(
        aio_base_url=settings.AIO_BASE_URL,
        ttl_seconds=settings.WORKSPACE_CLEANUP_TTL_SECONDS,
    )
    deps.set_cleaner(cleaner)

    shutdown_event = asyncio.Event()

    async def _cleanup_loop():
        log_event("工作域清理任务启动",
                  ttl_days=settings.WORKSPACE_CLEANUP_TTL_SECONDS // 86400,
                  interval_hours=settings.WORKSPACE_CLEANUP_INTERVAL_SECONDS // 3600)
        while not shutdown_event.is_set():
            try:
                await asyncio.sleep(settings.WORKSPACE_CLEANUP_INTERVAL_SECONDS)
                await cleaner.cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_error("清理循环异常", e)

    cleanup_task = asyncio.create_task(_cleanup_loop())

    try:
        await nacos_client_manager.register_instance()
    except Exception as e:
        log_error("Nacos 服务注册", e)

    log_event(f"{bootstrap_settings.APP_NAME} 就绪", port=bootstrap_settings.SERVICE_PORT)
    yield
    log_event(f"{bootstrap_settings.APP_NAME} 关闭")

    shutdown_event.set()
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    try:
        await nacos_client_manager.deregister_instance()
    except Exception as e:
        log_error("Nacos 服务注销", e)


app = FastAPI(title=bootstrap_settings.APP_NAME, lifespan=lifespan, docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET)

setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)

app.include_router(api_router, prefix="/v1/aio")

# 手动触发清理的管理端点（调试用）
@app.post("/v1/aio/admin/cleanup")
async def admin_cleanup():
    cleaner = deps._cleaner
    if not cleaner:
        return R.fail({"code": 503, "msg": "cleaner not initialized"})
    deleted = await cleaner.cleanup_expired()
    return R.success({"deleted_workspaces": deleted})

if __name__ == "__main__":
    uvicorn.run(
        "aio_gateway.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )
