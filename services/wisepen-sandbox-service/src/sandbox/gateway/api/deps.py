import asyncio
from fastapi import Request, HTTPException
from sandbox.gateway.isolation import PathTranslator, TenantScope, PathValidationError

# 由 main.py lifespan 在启动时注入
_queue = None
_file_manager = None
_vnc_binding = None
_session_pool = None


def set_queue(queue):
    global _queue
    _queue = queue


def set_file_manager(fm):
    global _file_manager
    _file_manager = fm


def set_vnc_binding(binding):
    global _vnc_binding
    _vnc_binding = binding


def set_session_pool(pool):
    global _session_pool
    _session_pool = pool


async def get_path_translator(request: Request) -> PathTranslator:
    """FastAPI dependency: extract tenant scope, build PathTranslator."""
    try:
        scope = TenantScope.from_security_context()
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PathTranslator(scope)


def acquire_container(user_id: str, session_id: str) -> tuple[str, int]:
    """获取容器，返回 (container_id, fencing_token)。"""
    if not _queue:
        raise HTTPException(status_code=503, detail="container queue not enabled")
    cid, token = _queue.acquire(user_id, session_id)
    if _file_manager:
        _file_manager.pull(cid, user_id, session_id)
    return cid, token


def release_container(cid: str, user_id: str = "", session_id: str = "",
                      fencing_token: int = 0):
    """释放容器（携带 fencing token）。"""
    if _file_manager:
        try:
            _file_manager.push(cid, user_id, session_id)
        except Exception:
            pass
    if _queue:
        _queue.release(cid, fencing_token)


def acquire_for_session(user_id: str, session_id: str) -> tuple[str, int]:
    """获取会话绑定的容器（首次分配 + pull，后续复用）."""
    if not _session_pool:
        raise HTTPException(status_code=503, detail="session pool not enabled")
    return _session_pool.acquire(user_id, session_id)


def touch_session(user_id: str, session_id: str) -> None:
    """更新会话心跳（每次请求调用）。"""
    if _session_pool:
        _session_pool.heartbeat(user_id, session_id)
