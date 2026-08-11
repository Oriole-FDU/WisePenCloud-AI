from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager, suppress

from common.logger import error, info, setup_logging_intercept
from common.observability import setup_observability
from sandbox.core.config.bootstrap_settings import bootstrap_settings


# 在任何其他业务 import 之前完成日志桥接与 OTel SDK 初始化。
# LOG_LEVEL 和服务名来自 bootstrap_settings（.env），无需等待 Nacos。
setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)


import uvicorn
from beanie import init_beanie
from common.observability import instrument_fastapi_app
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from fastapi import FastAPI

from sandbox.api import create_app
from sandbox.container import container
from sandbox.core.config.app_settings import settings
from sandbox.core.config.nacos import nacos_client_manager
from sandbox.domain.entities import SandboxDocument, SessionWorkspaceDocument


# 将应用配置注入 dependency_injector 容器。
container.config.from_dict(settings.model_dump())


async def _close_mongo_client() -> None:
    # 兼容不同 PyMongo 版本中同步或异步的 close 实现。
    result = container.mongo_client().close()
    if inspect.isawaitable(result):
        await result


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 应用生命周期
    # --- 启动阶段 ---
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)

    # MongoClient 由容器统一管理，确保关闭阶段释放同一个实例。
    mongo_client = container.mongo_client()
    nacos_registered = False
    watcher = None
    watcher_task = None
    try:
        # 初始化 Beanie 文档模型。
        await init_beanie(
            database=mongo_client[settings.MONGODB_DB_NAME],
            document_models=[SandboxDocument, SessionWorkspaceDocument],
        )
        info("beanie initialized.", db=settings.MONGODB_DB_NAME)

        # 注册 Nacos 服务；注册失败不阻塞应用启动。
        try:
            await nacos_client_manager.register_instance()
            nacos_registered = True
        except Exception as exc:
            error("nacos instance register failed.", exc=exc)

        # 启动沙箱状态 watcher，并保存任务引用供应用运行期间访问。
        watcher = container.watcher()
        watcher_task = asyncio.create_task(watcher.run())
        app.state.watcher_task = watcher_task
        info(
            "service ready.",
            service=bootstrap_settings.SERVICE_NAME,
            port=bootstrap_settings.SERVICE_PORT,
        )

        # --- 运行阶段 ---
        yield
    finally:
        # --- 关闭阶段 ---
        info("service stopping.", service=bootstrap_settings.SERVICE_NAME)

        # 停止 watcher；超时后取消任务，避免阻塞服务退出。
        if watcher is not None and watcher_task is not None:
            watcher.stop()
            try:
                await asyncio.wait_for(watcher_task, timeout=5.0)
            except asyncio.TimeoutError:
                watcher_task.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher_task
            except Exception as exc:
                error("sandbox watcher stop failed.", exc=exc)

        # 仅在启动时注册成功的情况下注销 Nacos 实例。
        if nacos_registered:
            try:
                await nacos_client_manager.deregister_instance()
            except Exception as exc:
                error("nacos instance deregister failed.", exc=exc)

        # 最后关闭 MongoClient，释放数据库连接池。
        try:
            await _close_mongo_client()
        except Exception as exc:
            error("mongo client close failed.", exc=exc)


# 创建应用并挂载业务路由。
app = create_app(lifespan=lifespan)
instrument_fastapi_app(app)

# 注册安全中间件：校验 X-From-Source，解析 X-User-Id 等网关透传 Headers。
app.add_middleware(
    SecurityHeaderMiddleware,
    from_source_secret=settings.FROM_SOURCE_SECRET,
)

# 注册全局异常处理器：统一转换业务异常与请求校验异常。
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)


if __name__ == "__main__":
    uvicorn.run(
        "sandbox.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )
