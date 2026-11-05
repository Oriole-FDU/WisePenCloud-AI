from pydantic import BaseModel, ConfigDict, Field
from beanie import PydanticObjectId


class AgentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)

class ModelCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: PydanticObjectId
    provider_id: PydanticObjectId | None = None
    runtime_options: dict = Field(default_factory=dict)


class SubagentOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_active_runs: int = Field(default=4, ge=1, le=8)
    max_total_runs: int = Field(default=16, ge=1, le=32)
    run_timeout_seconds: int = Field(default=600, ge=1, le=1200)
    max_iterations: int = Field(default=10, ge=1, le=25)
    max_depth: int = Field(default=1, ge=1, le=3)
    agents: list[AgentCandidate] = Field(default_factory=list, max_length=32)
    models: list[ModelCandidate] = Field(default_factory=list, max_length=16)
