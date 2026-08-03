from fastapi import APIRouter

from rag.api.endpoints import navigation_router, resources_router

api_router = APIRouter()
api_router.include_router(
    navigation_router,
    prefix="/knowledge-navigation",
    tags=["knowledge-navigation"],
)
api_router.include_router(
    resources_router,
    prefix="/resources",
    tags=["resources"],
)
