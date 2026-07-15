from typing import Any, Dict
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Request

from common.logger import error as log_error
from common.core.domain.responses import R
from common.security.context import SecurityContextHolder
from sandbox.gateway.isolation import PathTranslator, PathValidationError
from sandbox.gateway.api.deps import get_path_translator, acquire_container, release_container
from sandbox.gateway.container_utils import execute_on_container

router = APIRouter()


class ShellExecRequest(BaseModel):
    command: str
    exec_dir: str = "/workspace"
    timeout_ms: int = 30000


def _extract_tenant() -> tuple[str, str]:
    uid = (SecurityContextHolder.get_user_id() or "").strip()
    sid = (SecurityContextHolder.get_session_id() or "").strip()
    return uid, sid


@router.post("/exec")
async def shell_exec(request: ShellExecRequest, req: Request,
                     translator: PathTranslator = Depends(get_path_translator)) -> R:
    uid, sid = _extract_tenant()
    cid = None
    try:
        cid = acquire_container(uid, sid)
        physical_cwd = translator.translate(request.exec_dir)
        body: Dict[str, Any] = {"command": request.command, "exec_dir": physical_cwd}
        if request.timeout_ms:
            body["timeout"] = request.timeout_ms // 1000
        result = await execute_on_container(cid, "POST", "/v1/shell/exec", body)
        return R.success(result)
    except PathValidationError as e:
        return R(code=403, msg=str(e), data=None)
    except Exception as e:
        log_error("sandbox gateway shell/exec 代理失败", e, command=request.command)
        return R(code=500, msg=f"shell exec failed: {e}", data=None)
    finally:
        if cid:
            release_container(cid, uid, sid)
