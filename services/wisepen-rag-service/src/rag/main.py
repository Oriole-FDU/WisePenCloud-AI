"""RAG 平台入口：通用装配、就绪握手、GC 和共享客户端生命周期。"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial

from common.logger import error, info, setup_logging_intercept
from common.observability import instrument_fastapi_app, setup_observability
from common.web.exception_handlers import setup_global_exception_handlers
from fastapi import FastAPI

from rag.core.config.bootstrap_settings import bootstrap_settings

# 日志与可观测性必须在导入业务模块前初始化
setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)


from rag.api.router import api_router
from rag.container import container
from rag.core.config.app_settings import settings
from rag.core.config.nacos import nacos_client_manager
from rag.extensions import register_extensions


async def _run_background(name: str, run: Callable[[], Awaitable[None]]) -> None:
    # 任务真正开始才创建业务协程，避免启动即取消时留下未 await 的协程。
    await run()
    # 后台任务正常退出视为异常，交由 TaskGroup 收敛
    raise RuntimeError(f"{name} exited unexpectedly")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """先初始化，再启动被观察的后台任务；任务全部收尾后才关闭共享连接。"""
    app.state.ready = False
    container = app.state.container
    settings = container.settings()
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)

    try:
        async with AsyncExitStack() as resources:
            owned = set()

            def own(client):
                # provider 覆盖可能把两个角色绑定到同一客户端，也只关闭一次。
                if id(client) not in owned:
                    resources.push_async_callback(client.close)
                    owned.add(id(client))
                return client

            # 共享客户端登记：关闭顺序与登记顺序相反
            mongo = own(container.mongo_client())
            own(container.qdrant_client())
            own(container.embedding_openai())
            if settings.INSTRUCTION_MODEL is not None:
                own(container.instruction_openai())
            if settings.ENABLE_GRAPH_STORAGE:
                driver = own(container.neo4j_driver())
                await driver.verify_connectivity()
            graph = container.graph_storage()

            # 扩展声明在共享连接登记后解析；部分构造失败也进入统一清理。
            extensions = register_extensions(container)
            app.state.extensions = extensions

            # 存储层探活与索引/集合初始化
            await mongo.admin.command("ping")
            await container.content_storage().ensure_indexes()
            await container.publication_store().ensure_indexes()
            await container.acl_repository().ensure_indexes()
            for collection in container.vector_collections():
                await collection.storage.ensure_collection(
                    collection.metadata_index_fields
                )
            if graph is not None:
                await graph.ensure_schema()
            for name, extension in extensions.items():
                if extension.api is not None:
                    path = f"/rag/extensions/{name}"
                    app.mount(path, extension.api)

            # 构造绑定模型与客户端的必要组件，但不通过真实模型请求探测启动。
            container.embedding_client()
            container.rerank_client()
            container.pipeline()

            # 后台任务：GC 必启，Kafka 消费者按需启用
            async with asyncio.TaskGroup() as background:
                tasks = [
                    background.create_task(
                        _run_background(
                            "resource-gc", container.garbage_collector().run
                        ),
                        name="resource-gc",
                    )
                ]
                connected = []
                if settings.KAFKA_ENABLED:
                    for name, consumer in (
                        ("acl", container.acl_consumer()),
                        ("publication", container.publication_consumer()),
                    ):
                        started = asyncio.Event()
                        connected.append(started)
                        tasks.append(
                            background.create_task(
                                _run_background(
                                    name, partial(consumer.run, started=started)
                                ),
                                name=name,
                            )
                        )

                try:
                    # 首轮 GC 不参与就绪等待；Kafka 主消费者则必须真正完成连接。
                    await asyncio.gather(*(event.wait() for event in connected))

                    # 服务注册失败只记录，不影响进程继续提供服务
                    try:
                        await nacos_client_manager.register_instance()
                    except Exception as exc:  # noqa: BLE001 - 按资源/服务注册边界记录错误并继续
                        error("nacos instance register failed.", exc=exc)

                    app.state.ready = True
                    for extension in extensions.values():
                        if extension.api is not None:
                            extension.api.state.ready = True
                    info("service ready.", service=bootstrap_settings.SERVICE_NAME)
                    yield
                finally:
                    # 退出时先摘掉就绪标记，再取消后台任务
                    app.state.ready = False
                    for extension in extensions.values():
                        if extension.api is not None:
                            extension.api.state.ready = False
                    for task in tasks:
                        task.cancel()
    finally:
        app.state.ready = False
        info("service stopping.", service=bootstrap_settings.SERVICE_NAME)
        # 注销失败只记录，避免阻塞进程退出
        try:
            await nacos_client_manager.deregister_instance()
        except Exception as exc:  # noqa: BLE001 - 按资源/服务注册边界记录错误并继续
            error("nacos instance deregister failed.", exc=exc)


# 补齐 no_proxy，避免访问本机地址时走代理
no_proxy = ",".join(
    filter(
        None,
        [
            os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
            "localhost, 127.0.0.1",
        ],
    )
)
os.environ["no_proxy"] = no_proxy
os.environ["NO_PROXY"] = no_proxy


# 把 Nacos 拉到的 settings 注入容器，再按配置接入垂类，最后装配 FastAPI 应用
container.settings.override(settings)

app = FastAPI(title=bootstrap_settings.APP_NAME, lifespan=lifespan, docs_url="/docs")
app.state.ready = False
app.state.container = container
app.state.service_name = bootstrap_settings.SERVICE_NAME
app.include_router(api_router, prefix="/rag")
instrument_fastapi_app(app)
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "rag.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )
