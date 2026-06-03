from fastapi import Request, HTTPException
from aio_gateway.isolation import PathTranslator, TenantScope, PathValidationError


async def get_path_translator(request: Request) -> PathTranslator:
    """FastAPI dependency: extract tenant scope from security context, build PathTranslator."""
    try:
        scope = TenantScope.from_security_context()
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PathTranslator(scope)
