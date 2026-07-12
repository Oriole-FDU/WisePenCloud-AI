from fastapi import APIRouter
from chat.api.endpoints import attachment, chat, memory, model, session, speech, tool

api_router = APIRouter()

api_router.include_router(chat.router, prefix="", tags=["chat"])
api_router.include_router(attachment.router, prefix="/attachment", tags=["attachment"])
api_router.include_router(session.router, prefix="/session", tags=["session"])
api_router.include_router(memory.router, prefix="/memory", tags=["memory"])
api_router.include_router(model.router, prefix="/model", tags=["model"])
api_router.include_router(speech.router, prefix="/speech", tags=["speech"])
api_router.include_router(tool.router, prefix="/tool", tags=["tool"])
