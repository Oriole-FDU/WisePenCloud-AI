"""应用入口。管理 FastAPI 生命周期、容器初始化和资源清理。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from beanie import init_beanie
from chat.application.rag.implementations.persistence.mongodb.entities.chunk_documents import (
    RetrieveChunkDocument,
    SearchChunkDocument,
)
from chat.application.rag.implementations.persistence.mongodb.entities.manifest_documents import (
    RagIndexManifestDocument,
)
from chat.application.rag.implementations.persistence.mongodb.entities.resource_documents import (
    DocumentResourceDocument,
    NoteResourceDocument,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chat.api.endpoints import (
    chat as chat_endpoints,
    chat_file as chat_file_endpoints,
    memory as memory_endpoints,
    model as model_endpoints,
    rag as rag_endpoints,
    search_provider as search_provider_endpoints,
    session as session_endpoints,
)
from chat.api.router import api_router
from chat.container import container  # noqa: F401 — 触发 dependency_injector wiring，不可删除
from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.domain.entities import (
    ChatMessage,
    ChatSession,
    Model,
    ModelProviderMapping,
    Provider,
    Skill,
    UserSearchProviderConfig,
)
from common.cloud.nacos_client import nacos_client_manager
from common.logger import log_error, log_event, setup_logging_intercept
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware

# ==============================================================================
#   Bootstrap: 日志拦截 & 网络环境
# ==============================================================================

setup_logging_intercept(bootstrap_settings.LOG_LEVEL)

os.environ.update(
    no_proxy="localhost,127.0.0.1,wisepen-dev-server",
    NO_PROXY="localhost,127.0.0.1,wisepen-dev-server",
)


# ==============================================================================
#   Beanie Document Models（用于 init_beanie 注册）
# ==============================================================================

DOCUMENT_MODELS = [
    ChatSession,
    ChatMessage,
    Provider,
    Model,
    ModelProviderMapping,
    Skill,
    UserSearchProviderConfig,
    NoteResourceDocument,
    DocumentResourceDocument,
    RagIndexManifestDocument,
    RetrieveChunkDocument,
    SearchChunkDocument,
]


async def guarded(label: str, coro) -> None:
    """执行协程；捕获并记录异常，不中断调用链。"""
    try:
        await coro
    except Exception as e:
        log_error(label, e)


# ==============================================================================
#   应用生命周期：启动
# ==============================================================================


async def start_application() -> None:
    log_event(f"{settings.APP_NAME} 启动")

    # ----- 1. 容器资源初始化（HTTP 连接池等）-----
    await container.init_resources()

    # ----- 2. 启动常驻后台 Scheduler -----
    container.rag_index_gc_scheduler().start()
    container.document_temp_file_cleanup_scheduler().start()

    # ----- 3. MongoDB ODM（Beanie）初始化 -----
    await init_beanie(
        database=container.mongo_client()[settings.MONGODB_DB_NAME],
        document_models=DOCUMENT_MODELS,
    )
    log_event("Beanie 初始化", db=settings.MONGODB_DB_NAME)

    # ----- 4. 基础设施连接（Nacos / Kafka / Skill 缓存）-----
    await guarded("Nacos 服务注册", nacos_client_manager.register_instance())
    await container.kafka_producer().start()
    await container.skill_cache_refresher().start()

    # ----- 5. 启动重量级子进程（OCR / 浏览器池）-----
    await guarded("SkillAssetLoader 启动", container.skill_asset_loader().start())
    await guarded("DocumentExport BrowserPool 启动", container.document_export_browser_pool().start())

    log_event(f"{settings.APP_NAME} 就绪", port=settings.SERVICE_PORT)


# ==============================================================================
#   应用生命周期：关闭（按启动反向顺序释放）
# ==============================================================================


async def shutdown_application() -> None:
    log_event(f"{settings.APP_NAME} 关闭流程开始")

    steps = [
        ("Skill Cache Refresher",        container.skill_cache_refresher().stop),
        ("Kafka Producer",               container.kafka_producer().stop),
        ("SkillAssetLoader",             container.skill_asset_loader().stop),
        ("OCR Processor",                container.ocr_processor().close),
        ("Browser Pool",                 container.document_export_browser_pool().stop),
        ("BrowseInteractTool",           container.browse_interact_tool().close),
        ("LocalScriptFetcher",           container.local_script_fetcher().close),
        ("PythonMathSolverTool",         container.python_math_solver_tool().close),
        ("SageMathClient",               container.sage_runtime_client().close),
        ("TranslationAssistTool",        container.translation_assist_tool().close),
        ("Document Cleanup Scheduler",   container.document_temp_file_cleanup_scheduler().close),
        ("RAG Index GC Scheduler",       container.rag_index_gc_scheduler().close),
        ("RAG Redis Queue",              container.rag_indexing_queue().close),
        ("RAG Qdrant Client",            container.rag_qdrant_client().close),
        ("RAG Elasticsearch Client",     container.rag_elasticsearch_client().close),
        ("RAG ZeroEntropy Client",       container.rag_zero_entropy_client().close),
        ("RpcClient",                    container.rpc_client().aclose),
        ("ServiceDiscovery",             container.service_discovery().close),
    ]
    for label, fn in steps:
        await guarded(f"{label} 释放失败", fn())

    await guarded("Nacos 服务注销", nacos_client_manager.deregister_instance())
    await guarded("容器资源释放失败", container.shutdown_resources())

    log_event(f"{settings.APP_NAME} 安全退出")


# ==============================================================================
#   FastAPI Lifespan（启动 & 关闭的编排入口）
# ==============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_application()
    try:
        yield
    finally:
        await shutdown_application()


# ==============================================================================
#   依赖注入 Wiring（将 container provider 注入到 endpoint 模块）
# ==============================================================================


container.wire(
    modules=[
        chat_endpoints,
        session_endpoints,
        memory_endpoints,
        model_endpoints,
        rag_endpoints,
        search_provider_endpoints,
        chat_file_endpoints,
    ]
)


# ==============================================================================
#   FastAPI 应用实例 & 中间件
# ==============================================================================


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan, docs_url="/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET)
setup_global_exception_handlers(app, is_dev=settings.DEV)
app.include_router(api_router, prefix="/chat")


# ==============================================================================
#   直接运行入口
# ==============================================================================


if __name__ == "__main__":
    uvicorn.run(
        "chat.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
        env_file="./.env",
        ws="websockets-sansio",
    )