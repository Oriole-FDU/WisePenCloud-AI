from fastapi import APIRouter
from common.core.domain.responses import R

router = APIRouter()


@router.get("/health")
async def health_check():
    return R.success({
        "status": "ok",
        "service": "wisepen-aio-service",
    })
