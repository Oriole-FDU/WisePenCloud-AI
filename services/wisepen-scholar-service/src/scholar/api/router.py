from fastapi import APIRouter

from scholar.api.endpoints import health, papers

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(papers.router)
