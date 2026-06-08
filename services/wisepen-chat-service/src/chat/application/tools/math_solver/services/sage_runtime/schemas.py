from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SageComputeResponse(BaseModel):
    status: str
    task: str
    exact_result: Optional[str] = None
    numeric_result: Optional[str] = None
    latex_result: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
