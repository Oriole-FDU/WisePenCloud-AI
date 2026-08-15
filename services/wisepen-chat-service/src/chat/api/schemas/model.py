from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from chat.domain.entities.model import ModelFamily, ModelScope
from chat.domain.entities.provider import ProviderScope, ProviderType


class ProviderResponse(BaseModel):
    id: str
    name: str
    base_url: Optional[str] = None
    api_key_fingerprint: Optional[str] = None
    scope: ProviderScope
    type: ProviderType
    is_active: bool
    token_usage: int
    billable_token_usage: int


class ModelProviderMappingResponse(BaseModel):
    model_id: str
    provider_id: str
    provider_name: Optional[str] = None
    provider_model_name: str
    support_runtime_options: Dict = Field(default_factory=dict)
    is_preferred: bool
    is_active: bool
    priority: int


class ModelResponse(BaseModel):
    id: str
    scope: ModelScope
    display_name: str
    model_family: ModelFamily
    billing_ratio: int
    support_thinking: bool
    support_vision: bool
    support_tools: bool
    context_window_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    is_active: bool
    mappings: List[ModelProviderMappingResponse] | None = Field(default_factory=list)


class AvailableModelsResponse(BaseModel):
    system_models: List[ModelResponse] = Field(default_factory=list)
    user_models: List[ModelResponse] = Field(default_factory=list)


class ListModelsResponse(BaseModel):
    models: List[ModelResponse] = Field(default_factory=list)


class ListUserProvidersResponse(BaseModel):
    providers: List[ProviderResponse] = Field(default_factory=list)


class CreateUserProviderRequest(BaseModel):
    name: str
    type: ProviderType
    api_key: str
    base_url: Optional[str] = None
    is_active: bool = True


class UpdateUserProviderRequest(BaseModel):
    provider_id: str
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    type: Optional[ProviderType] = None
    is_active: Optional[bool] = None


class DeleteUserProviderRequest(BaseModel):
    provider_id: str

class CreateUserModelRequest(BaseModel):
    display_name: str
    model_family: ModelFamily = ModelFamily.GENERIC
    billing_ratio: int = 1
    support_thinking: bool = False
    support_vision: bool = False
    support_tools: bool = True
    context_window_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None


class UpdateUserModelRequest(BaseModel):
    model_id: str
    display_name: Optional[str] = None
    model_family: Optional[ModelFamily] = None
    billing_ratio: Optional[int] = None
    support_thinking: Optional[bool] = None
    support_vision: Optional[bool] = None
    support_tools: Optional[bool] = None
    context_window_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    is_active: Optional[bool] = None


class DeleteUserModelRequest(BaseModel):
    model_id: str


class BindModelProviderRequest(BaseModel):
    model_id: str
    provider_id: str
    provider_model_name: str
    is_preferred: bool = True
    is_active: bool = True


class UnbindModelProviderRequest(BaseModel):
    model_id: str
    provider_id: str
