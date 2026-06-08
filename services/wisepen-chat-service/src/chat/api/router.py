from fastapi import APIRouter

from chat.api.endpoints import (
    chat,
    chat_file,
    memory,
    model,
    # RAG API 暂时关闭，恢复时取消下面导入和 include_router 注释。
    # rag,
    search_provider,
    session,
)

api_router = APIRouter()

api_router.include_router(chat.router, prefix="", tags=["chat"])
api_router.include_router(chat_file.router, prefix="/chatFile", tags=["chatFile"])
api_router.include_router(session.router, prefix="/session", tags=["session"])
api_router.include_router(memory.router, prefix="/memory", tags=["memory"])
api_router.include_router(model.router, prefix="/model", tags=["model"])
api_router.include_router(
    search_provider.router,
    prefix="/searchProvider",
    tags=["searchProvider"],
)
# api_router.include_router(rag.router, prefix="/rag", tags=["rag"])
