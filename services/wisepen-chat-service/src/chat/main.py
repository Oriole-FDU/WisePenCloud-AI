# 屏蔽 websockets.legacy 第三方弃用提示
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"websockets\.legacy",)

from common.logger import setup_logging_intercept, info, error
from common.observability import setup_observability
from chat.core.config.bootstrap_settings import bootstrap_settings
# 在任何其他 import 之前完成日志桥接与 OTel SDK 初始化。
# LOG_LEVEL 和服务名来自 bootstrap_settings（.env），无需等待 Nacos
setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)

import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo import AsyncMongoClient
from beanie import init_beanie

from chat.core.config.nacos import nacos_client_manager
from common.web.middleware import SecurityHeaderMiddleware
from common.web.exception_handlers import setup_global_exception_handlers
from common.observability import instrument_fastapi_app

from chat.container import container  # noqa: F401 — 触发 dependency_injector wiring，不可删除
from chat.core.config.app_settings import settings
from chat.api.router import api_router
from chat.api.endpoints import attachment as attachment_endpoints
from chat.api.endpoints import chat as chat_endpoints
from chat.api.endpoints import session as session_endpoints
from chat.api.endpoints import memory as memory_endpoints
from chat.api.endpoints import model as model_endpoints
from chat.api.endpoints import speech as speech_endpoints
from chat.api.endpoints import tool as tool_endpoints
from chat.domain.entities import ChatSession, ChatMessage, Provider, Model, ModelProviderMapping, UserToolConfig


# 避免 HTTP 代理拦截内部中间件请求。
no_proxy = ",".join(filter(None, [
    os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
    "localhost, 127.0.0.1"
]))
os.environ["no_proxy"] = no_proxy
os.environ["NO_PROXY"] = no_proxy

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 应用生命周期
    # --- 启动阶段 ---
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)

    # 初始化 Beanie
    mongo_client = AsyncMongoClient(settings.MONGODB_URL)
    await init_beanie(
        database=mongo_client[settings.MONGODB_DB_NAME],
        document_models=[ChatSession, ChatMessage, Provider, Model, ModelProviderMapping, UserToolConfig],
    )
    info("beanie initialized.", db=settings.MONGODB_DB_NAME)

    # 注册 Nacos 服务
    try:
        await nacos_client_manager.register_instance()
    except Exception as e:
        error("nacos instance register failed.", exc=e)
    
    # 启动 Kafka Producer
    kafka_producer = container.kafka_producer()
    await kafka_producer.start()

    # 启动 Oss File 加载器
    oss_file_loader = container.oss_file_loader()
    if getattr(oss_file_loader, "start", None) is not None:
        try:
            await oss_file_loader.start()
        except Exception as e:
            error("file loader start failed.", exc=e)

    info("service ready.", service=bootstrap_settings.SERVICE_NAME, port=bootstrap_settings.SERVICE_PORT)

    # --- 运行阶段 ---
    yield

    # --- 关闭阶段 ---
    info("service stopping.", service=bootstrap_settings.SERVICE_NAME)

    # 关闭 Kafka Producer
    kafka_producer = container.kafka_producer()
    await kafka_producer.stop()

    # 关闭 Oss File 加载器
    oss_file_loader = container.oss_file_loader()
    if getattr(oss_file_loader, "stop", None) is not None:
        try:
            await oss_file_loader.stop()
        except Exception as e:
            error("file loader stop failed.", exc=e)
    try:
        await container.rpc_client().aclose()
    except Exception as e:
        error("rpc client close failed.", exc=e)
    try:
        await container.service_discovery().close()
    except Exception as e:
        error("service discovery close failed.", exc=e)

    try:
        await nacos_client_manager.deregister_instance()
    except Exception as e:
        error("nacos instance deregister failed.", exc=e)

container.wire(modules=[
    attachment_endpoints,
    chat_endpoints,
    session_endpoints,
    memory_endpoints,
    model_endpoints,
    speech_endpoints,
    tool_endpoints,
])  # 注入依赖到路由模块
app = FastAPI(title=bootstrap_settings.APP_NAME, lifespan=lifespan, docs_url="/docs")
instrument_fastapi_app(app)

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
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)

# 挂载业务路由
app.include_router(api_router, prefix="/chat")

if __name__ == "__main__":
    uvicorn.run(
        "chat.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )


