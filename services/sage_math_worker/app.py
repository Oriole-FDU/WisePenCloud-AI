from fastapi import FastAPI

from models import SageComputeRequest, SageComputeResponse
from tasks import compute_sage

app = FastAPI(title="Sage Math Worker")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/compute", response_model=SageComputeResponse)
def compute(request: SageComputeRequest) -> SageComputeResponse:
    try:
        return compute_sage(request)
    except Exception as e:
        return SageComputeResponse(
            status="error",
            task=request.task,
            error=str(e),
        )