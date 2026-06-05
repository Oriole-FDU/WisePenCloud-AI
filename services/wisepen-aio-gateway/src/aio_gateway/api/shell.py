from typing import Any, Dict
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Request
import httpx

from common.logger import log_error
from common.core.domain.responses import R
from aio_gateway.settings import settings
from aio_gateway.isolation import PathTranslator, PathValidationError
from aio_gateway.api.deps import get_path_translator

router = APIRouter()


class ShellExecRequest(BaseModel):
    command: str
    exec_dir: str = "/workspace"
    timeout_ms: int = 30000


async def _proxy_to_aio(
    method: str,
    path: str,
    json_body: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    url = f"{settings.AIO_BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, json=json_body)
        resp.raise_for_status()
        return resp.json()


@router.post("/exec")
async def shell_exec(
    request: ShellExecRequest,
    req: Request,
    translator: PathTranslator = Depends(get_path_translator),
) -> R:
    try:
        physical_cwd = translator.translate(request.exec_dir)
        body: Dict[str, Any] = {
            "command": request.command,
            "exec_dir": physical_cwd,
        }
        if request.timeout_ms:
            body["timeout"] = request.timeout_ms // 1000
        result = await _proxy_to_aio("POST", "/v1/shell/exec", body)
        return R.success(result)
    except PathValidationError as e:
        return R(code=403, msg=str(e), data=None)
    except Exception as e:
        log_error("AIO shell/exec 代理失败", e, command=request.command)
        return R(code=500, msg=f"shell exec failed: {e}", data=None)
