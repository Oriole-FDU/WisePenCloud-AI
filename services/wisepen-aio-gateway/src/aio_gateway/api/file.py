from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Request
import httpx

from common.logger import error as log_error
from common.core.domain.responses import R
from common.core.domain.enums import ResultCode
from aio_gateway.settings import settings
from aio_gateway.isolation import PathTranslator, PathValidationError
from aio_gateway.api.deps import get_path_translator

router = APIRouter()
_aio_client = httpx.AsyncClient(timeout=30.0)


class FileReadRequest(BaseModel):
    file: str
    max_chars: Optional[int] = None


class FileWriteRequest(BaseModel):
    file: str
    content: str
    encoding: str = "utf-8"


class FileListRequest(BaseModel):
    path: str
    recursive: bool = False


class FileGrepRequest(BaseModel):
    path: str
    pattern: str
    recursive: bool = True
    ignore_case: bool = False


class FileReplaceRequest(BaseModel):
    file: str
    old_str: str
    new_str: str


async def _proxy_to_aio(
    method: str,
    path: str,
    json_body: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    url = f"{settings.AIO_BASE_URL}{path}"
    resp = await _aio_client.request(method, url, json=json_body)
    resp.raise_for_status()
    return resp.json()


def _scrub_result(result: Any, translator: PathTranslator) -> Any:
    """Replace physical paths in AIO response with virtual paths."""
    if isinstance(result, dict):
        scrubbed = {}
        for k, v in result.items():
            if k in ("file", "path") and isinstance(v, str):
                scrubbed[k] = translator.reverse(v)
            elif isinstance(v, str):
                scrubbed[k] = v.replace(translator.physical_root, "/workspace")
            elif isinstance(v, list):
                scrubbed[k] = [_scrub_result(item, translator) for item in v]
            elif isinstance(v, dict):
                scrubbed[k] = _scrub_result(v, translator)
            else:
                scrubbed[k] = v
        return scrubbed
    return result


@router.post("/read")
async def file_read(
    request: FileReadRequest,
    req: Request,
    translator: PathTranslator = Depends(get_path_translator),
) -> R:
    try:
        physical = translator.translate(request.file)
        body: Dict[str, Any] = {"file": physical}
        if request.max_chars is not None:
            body["max_chars"] = request.max_chars
        result = await _proxy_to_aio("POST", "/v1/file/read", body)
        return R.success(_scrub_result(result, translator))
    except PathValidationError as e:
        return R(code=403, msg=str(e), data=None)
    except Exception as e:
        log_error("AIO file/read 代理失败", e, file=request.file)
        return R(code=500, msg=f"read failed: {e}", data=None)


@router.post("/write")
async def file_write(
    request: FileWriteRequest,
    req: Request,
    translator: PathTranslator = Depends(get_path_translator),
) -> R:
    try:
        body = {
            "file": translator.translate(request.file),
            "content": request.content,
            "encoding": request.encoding,
        }
        result = await _proxy_to_aio("POST", "/v1/file/write", body)
        return R.success(_scrub_result(result, translator))
    except PathValidationError as e:
        return R(code=403, msg=str(e), data=None)
    except Exception as e:
        log_error("AIO file/write 代理失败", e, file=request.file)
        return R(code=500, msg=f"write failed: {e}", data=None)


@router.post("/list")
async def file_list(
    request: FileListRequest,
    req: Request,
    translator: PathTranslator = Depends(get_path_translator),
) -> R:
    try:
        body = {"path": translator.translate(request.path), "recursive": request.recursive}
        result = await _proxy_to_aio("POST", "/v1/file/list", body)
        return R.success(_scrub_result(result, translator))
    except PathValidationError as e:
        return R(code=403, msg=str(e), data=None)
    except Exception as e:
        log_error("AIO file/list 代理失败", e, path=request.path)
        return R(code=500, msg=f"list failed: {e}", data=None)


@router.post("/grep")
async def file_grep(
    request: FileGrepRequest,
    req: Request,
    translator: PathTranslator = Depends(get_path_translator),
) -> R:
    try:
        body = {
            "path": translator.translate(request.path),
            "pattern": request.pattern,
            "recursive": request.recursive,
            "ignore_case": request.ignore_case,
        }
        result = await _proxy_to_aio("POST", "/v1/file/grep", body)
        return R.success(_scrub_result(result, translator))
    except PathValidationError as e:
        return R(code=403, msg=str(e), data=None)
    except Exception as e:
        log_error("AIO file/grep 代理失败", e, path=request.path)
        return R(code=500, msg=f"grep failed: {e}", data=None)


@router.post("/replace")
async def file_replace(
    request: FileReplaceRequest,
    req: Request,
    translator: PathTranslator = Depends(get_path_translator),
) -> R:
    try:
        body = {
            "file": translator.translate(request.file),
            "old_str": request.old_str,
            "new_str": request.new_str,
        }
        result = await _proxy_to_aio("POST", "/v1/file/replace", body)
        return R.success(_scrub_result(result, translator))
    except PathValidationError as e:
        return R(code=403, msg=str(e), data=None)
    except Exception as e:
        log_error("AIO file/replace 代理失败", e, file=request.file)
        return R(code=500, msg=f"replace failed: {e}", data=None)
