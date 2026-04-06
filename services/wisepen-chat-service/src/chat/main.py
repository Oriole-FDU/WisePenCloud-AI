from common.logger import setup_logging_intercept, log_event, log_error
from chat.core.config.bootstrap_settings import bootstrap_settings
# 在任何其他 import 之前完成日志桥接，确保 uvicorn/fastapi 日志统一输出到 Loguru
# LOG_LEVEL 来自 bootstrap_settings（.env），无需等待 Nacos
setup_logging_intercept(bootstrap_settings.LOG_LEVEL)

import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo import AsyncMongoClient
from beanie import init_beanie

from common.cloud.nacos_client import nacos_client_manager
from common.web.middleware import SecurityHeaderMiddleware
from common.web.exception_handlers import setup_global_exception_handlers

from chat.container import container  # noqa: F401 — 触发 dependency_injector wiring，不可删除
from chat.core.config.app_settings import settings
from chat.api.v1.router import api_router
from chat.api.v1.endpoints import chat as chat_endpoints
from chat.api.v1.endpoints import session as session_endpoints
from chat.api.v1.endpoints import memory as memory_endpoints
from chat.api.v1.endpoints import model as model_endpoints
from chat.domain.entities import ChatSession, ChatMessage


os.environ["no_proxy"] = "localhost,127.0.0.1,wisepen-dev-server"
os.environ["NO_PROXY"] = "localhost,127.0.0.1,wisepen-dev-server"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 应用生命周期
    # --- 启动阶段 ---
    log_event(f"{settings.APP_NAME} 启动")

    # 初始化 Beanie
    mongo_client = AsyncMongoClient(settings.MONGODB_URL)
    await init_beanie(
        database=mongo_client[settings.MONGODB_DB_NAME],
        document_models=[ChatSession, ChatMessage],
    )
    log_event("Beanie 初始化", db=settings.MONGODB_DB_NAME)

    # 注册 Nacos 服务
    try:
        await nacos_client_manager.register_instance()
    except Exception as e:
        log_error("Nacos 服务注册", e)

    # 启动 Kafka Producer
    kafka_producer = container.kafka_producer()
    await kafka_producer.start()

    log_event(f"{settings.APP_NAME} 就绪", port=settings.SERVICE_PORT)

    # --- 运行阶段 ---
    yield

    # --- 关闭阶段 ---
    log_event(f"{settings.APP_NAME} 关闭")
    
    # 关闭 Kafka Producer
    kafka_producer = container.kafka_producer()
    await kafka_producer.stop()
    
    try:
        await nacos_client_manager.deregister_instance()
    except Exception as e:
        log_error("Nacos 服务注销", e)

container.wire(modules=[chat_endpoints, session_endpoints, memory_endpoints, model_endpoints])  # 注入依赖到路由模块
app = FastAPI(title=settings.APP_NAME, lifespan=lifespan, docs_url="/docs")

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册安全中间件：校验 X-From-Source，解析 X-User-Id 等网关透传 Headers
app.add_middleware(SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET)

# 注册全局异常处理器：ServiceException / PermissionException / RequestValidationError 统一转为 R 格式
setup_global_exception_handlers(app, is_dev=settings.DEV)

# 挂载业务路由
app.include_router(api_router, prefix="/v1")

if __name__ == "__main__":
    uvicorn.run(
        "chat.main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=settings.DEV,
        env_file="./.env"
    )
