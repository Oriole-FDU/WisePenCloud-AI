from fastapi import APIRouter

from rag.api.endpoints.expand import router as expand_router
from rag.api.endpoints.locate import router as locate_router
from rag.api.endpoints.read import router as read_router

api_router = APIRouter()
api_router.include_router(
    locate_router,
    tags=["locate"],
)
api_router.include_router(read_router, tags=["read"])
api_router.include_router(expand_router, tags=["expand"])
