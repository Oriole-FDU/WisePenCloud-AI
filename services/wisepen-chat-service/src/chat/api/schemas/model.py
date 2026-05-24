from typing import List, Optional

from pydantic import BaseModel, Field

from chat.domain.entities.model import ModelScope, ModelType
from chat.domain.entities.provider import ProviderScope, ProviderType


class ProviderResponse(BaseModel):
    id: str
    name: str
    api_base_url: str
    api_key_fingerprint: Optional[str] = None
    scope: ProviderScope
    type: ProviderType
    is_active: bool
    usage_tokens: int
    billable_usage_tokens: int


class ModelProviderMappingResponse(BaseModel):
    model_id: str
    provider_id: str
    provider_name: Optional[str] = None
    provider_model_name: str
    is_preferred: bool
    is_active: bool
    priority: int


class ModelResponse(BaseModel):
    id: str
    scope: ModelScope
    display_name: str
    vendor: str
    type: ModelType
    billing_ratio: int
    support_thinking: bool
    support_vision: bool
    support_tools: bool
    support_streaming: bool
    context_window_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    is_active: bool
    mappings: List[ModelProviderMappingResponse] | None = Field(default_factory=list)


class AvailableModelsResponse(BaseModel):
    system_models: List[ModelResponse] = Field(default_factory=list)
    user_models: List[ModelResponse] = Field(default_factory=list)


class ListUserModelsResponse(BaseModel):
    models: List[ModelResponse] = Field(default_factory=list)


class ListUserProvidersResponse(BaseModel):
    providers: List[ProviderResponse] = Field(default_factory=list)


class CreateUserProviderRequest(BaseModel):
    name: str
    api_base_url: str
    api_key: str
    type: ProviderType = ProviderType.OPENAI_COMPATIBLE_LLM


class UpdateUserProviderRequest(BaseModel):
    provider_id: str
    name: Optional[str] = None
    api_base_url: Optional[str] = None
    api_key: Optional[str] = None
    type: Optional[ProviderType] = None
    is_active: Optional[bool] = None


class DeleteUserProviderRequest(BaseModel):
    provider_id: str

class CreateUserModelRequest(BaseModel):
    display_name: str
    vendor: str
    type: ModelType = ModelType.CUSTOM_MODEL
    billing_ratio: int = 1
    support_thinking: bool = False
    support_vision: bool = False
    support_tools: bool = True
    support_streaming: bool = True
    context_window_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None


class UpdateUserModelRequest(BaseModel):
    model_id: str
    display_name: Optional[str] = None
    vendor: Optional[str] = None
    type: Optional[ModelType] = None
    billing_ratio: Optional[int] = None
    support_thinking: Optional[bool] = None
    support_vision: Optional[bool] = None
    support_tools: Optional[bool] = None
    support_streaming: Optional[bool] = None
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
