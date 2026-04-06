from fastapi import APIRouter
from chat.api.v1.endpoints import chat, session, memory, model

api_router = APIRouter()

api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(session.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(memory.router, prefix="/memories", tags=["memories"])
api_router.include_router(model.router, prefix="/models", tags=["models"])
