import asyncio
from fastapi import Request, HTTPException
from aio_gateway.isolation import PathTranslator, TenantScope, PathValidationError

# 由 main.py lifespan 在启动时注入
_queue = None
_file_manager = None
_vnc_binding = None


def set_queue(queue):
    global _queue
    _queue = queue


def set_file_manager(fm):
    global _file_manager
    _file_manager = fm


def set_vnc_binding(binding):
    global _vnc_binding
    _vnc_binding = binding


async def get_path_translator(request: Request) -> PathTranslator:
    """FastAPI dependency: extract tenant scope, build PathTranslator."""
    try:
        scope = TenantScope.from_security_context()
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PathTranslator(scope)


def acquire_container(user_id: str, session_id: str) -> str:
    """Get a container from the pool. Raises if queue not enabled."""
    if not _queue:
        raise HTTPException(status_code=503, detail="container queue not enabled")
    cid = _queue.acquire(user_id, session_id)
    if _file_manager:
        _file_manager.pull(cid, user_id, session_id)
    return cid


def release_container(cid: str, user_id: str = "", session_id: str = ""):
    """Release container back to pool (fire-and-forget in finally block)."""
    if _file_manager:
        try:
            _file_manager.push(cid, user_id, session_id)
        except Exception:
            pass
    if _queue:
        _queue.release(cid)
