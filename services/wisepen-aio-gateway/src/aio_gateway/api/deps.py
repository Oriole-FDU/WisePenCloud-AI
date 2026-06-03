import asyncio
from fastapi import Request, HTTPException
from aio_gateway.isolation import PathTranslator, TenantScope, PathValidationError
from aio_gateway.cleanup import WorkspaceCleaner

# 由 main.py 的 lifespan 在启动时注入
_cleaner: WorkspaceCleaner | None = None


def set_cleaner(cleaner: WorkspaceCleaner) -> None:
    global _cleaner
    _cleaner = cleaner


async def get_path_translator(request: Request) -> PathTranslator:
    """FastAPI dependency: extract tenant scope, build PathTranslator, record access."""
    scope = TenantScope.from_security_context()
    translator = PathTranslator(scope)

    # fire-and-forget: 更新 .last_access，不阻塞请求
    if _cleaner:
        asyncio.create_task(_cleaner.record_access(scope))

    return translator
