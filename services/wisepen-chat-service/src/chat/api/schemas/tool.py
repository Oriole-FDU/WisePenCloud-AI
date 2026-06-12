from pydantic import BaseModel, Field


class ToolOption(BaseModel):
    toolId: str = Field(..., description="Tool identifier used in chat requests")
    label: str = Field(..., description="Display label for UI")

