import os
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"websockets\.legacy")

from common.logger import error, info, setup_logging_intercept
from common.observability import instrument_fastapi_app, setup_observability
from wisepen_mcp.core.config.bootstrap_settings import bootstrap_settings

setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI

from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from wisepen_mcp.capabilities.skill_creator import build_skill_creator_mcp
from wisepen_mcp.container import container
from wisepen_mcp.core.config.app_settings import settings
from wisepen_mcp.core.config.nacos import nacos_client_manager

no_proxy = ",".join(filter(None, [
    os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
    "localhost, 127.0.0.1",
]))
os.environ["no_proxy"] = no_proxy
os.environ["NO_PROXY"] = no_proxy


mcp_server = build_skill_creator_mcp(container.ai_asset_client())
mcp_app = mcp_server.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)
    async with mcp_server.session_manager.run():
        try:
            await nacos_client_manager.register_instance()
        except Exception as e:
            error("nacos instance register failed.", exc=e)

        info("service ready.", service=bootstrap_settings.SERVICE_NAME, port=bootstrap_settings.SERVICE_PORT)
        yield

        info("service stopping.", service=bootstrap_settings.SERVICE_NAME)
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


app = FastAPI(title=bootstrap_settings.APP_NAME, lifespan=lifespan, docs_url="/docs")
instrument_fastapi_app(app)
app.add_middleware(SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET)
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": bootstrap_settings.SERVICE_NAME}


app.mount("/mcp", mcp_app)


if __name__ == "__main__":
    uvicorn.run(
        "wisepen_mcp.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )
