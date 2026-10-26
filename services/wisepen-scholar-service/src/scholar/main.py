"""沿用 Chat/Sandbox 的引导、配置、容器和 HTTP 生命周期。"""

from contextlib import asynccontextmanager

from common.logger import info, setup_logging_intercept
from common.observability import setup_observability

from scholar.core.config.bootstrap_settings import bootstrap_settings

setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME, environment=bootstrap_settings.PROFILE
)

import uvicorn
from common.observability import instrument_fastapi_app

from scholar.api.app import create_app
from scholar.api.endpoints import health, papers
from scholar.container import container
from scholar.core.config.app_settings import settings
from scholar.core.config.nacos import nacos_client_manager

container.config.from_dict(settings.model_dump())
container.wire(modules=[health, papers])


@asynccontextmanager
async def lifespan(app):
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)
    try:
        await nacos_client_manager.register_instance()
        yield
    finally:
        await nacos_client_manager.deregister_instance()
        await container.redis().aclose()
        await container.elasticsearch().close()


app = create_app(from_source_secret=settings.FROM_SOURCE_SECRET, lifespan=lifespan)
instrument_fastapi_app(app)


if __name__ == "__main__":
    uvicorn.run(
        "scholar.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )
