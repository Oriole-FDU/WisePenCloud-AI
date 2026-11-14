from typing import List, Optional

from pydantic import BaseModel

from chat.api.schemas.model import ProviderResponse
from chat.domain.entities.model import ModelFamily
from chat.domain.entities.provider import ProviderType


class ListSystemProvidersResponse(BaseModel):
    providers: List[ProviderResponse]


class CreateSystemProviderRequest(BaseModel):
    name: str
    type: ProviderType
    api_key: str
    base_url: Optional[str] = None
    is_active: bool = True


class UpdateSystemProviderRequest(BaseModel):
    provider_id: str
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    type: Optional[ProviderType] = None
    is_active: Optional[bool] = None


class DeleteSystemProviderRequest(BaseModel):
    provider_id: str


class CreateSystemModelRequest(BaseModel):
    display_name: str
    model_family: ModelFamily = ModelFamily.GENERIC
    support_thinking: bool = False
    support_vision: bool = False
    support_tools: bool = True
    context_window_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None


class UpdateSystemModelRequest(BaseModel):
    model_id: str
    display_name: Optional[str] = None
    model_family: Optional[ModelFamily] = None
    support_thinking: Optional[bool] = None
    support_vision: Optional[bool] = None
    support_tools: Optional[bool] = None
    context_window_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    is_active: Optional[bool] = None


class DeleteSystemModelRequest(BaseModel):
    model_id: str


class BindSystemModelProviderRequest(BaseModel):
    model_id: str
    provider_id: str
    provider_model_name: str
    input_billing_ratio: str = "1"
    cached_input_billing_ratio: str = "1"
    output_billing_ratio: str = "1"
    is_preferred: bool = True
    is_active: bool = True


class UnbindSystemModelProviderRequest(BaseModel):
    model_id: str
    provider_id: str
