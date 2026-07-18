from fastapi import APIRouter

from sandbox.gateway.api.vnc import create_vnc_router
from sandbox.gateway.binding import VncBinding


def create_gateway_router(binding: VncBinding) -> APIRouter:
    router = APIRouter(prefix="/v1/sandbox/gateway")
    router.include_router(create_vnc_router(binding))
    return router
