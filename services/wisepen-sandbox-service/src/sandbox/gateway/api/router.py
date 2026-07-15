from fastapi import APIRouter
from sandbox.gateway.api import file, health, shell, vnc

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(file.router, prefix="/file", tags=["file"])
api_router.include_router(shell.router, prefix="/shell", tags=["shell"])
api_router.include_router(vnc.router, tags=["vnc"])
