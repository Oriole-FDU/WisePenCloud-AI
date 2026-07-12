from fastapi import APIRouter
from aio_gateway.api import file, health, shell

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(file.router, prefix="/file", tags=["file"])
api_router.include_router(shell.router, prefix="/shell", tags=["shell"])
