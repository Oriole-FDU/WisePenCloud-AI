from datetime import datetime
from decimal import Decimal
from typing import Annotated, ClassVar, Literal, Self
from urllib.parse import urlsplit

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator, model_validator

from chat.api.schemas.model import ModelResponse, ProviderResponse, ModelProviderMappingResponse
from chat.domain.entities.model import ModelFamily
from chat.domain.entities.provider import ProviderType


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Version = Annotated[int, Field(strict=True, ge=1)]
TokenLimit = Annotated[int, Field(strict=True, gt=0, le=100_000_000)]
BillingRatio = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=8, allow_inf_nan=False)]


class AdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def reject_explicit_null(self) -> Self:
        for name in self.model_fields_set - self.nullable_fields:
            if getattr(self, name) is None:
                raise ValueError("字段不允许为 null")
        return self

    @field_validator("api_key", check_fields=False)
    @classmethod
    def validate_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value()
        if not key.strip() or len(key) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in key):
            raise ValueError("API Key 格式不合法")
        return value

    @field_validator("base_url", check_fields=False)
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            url = urlsplit(value)
            valid = (url.scheme in {"http", "https"} and url.hostname and url.port != 0
                     and not url.username and not url.password and not url.query and not url.fragment)
        except ValueError:
            valid = False
        if not valid or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError("供应商地址必须是无凭证、查询串或片段的 HTTP(S) 地址")
        return value


class CreateProviderRequest(AdminRequest):
    nullable_fields = frozenset({"base_url"})
    name: Name
    type: ProviderType
    api_key: SecretStr
    base_url: str | None = None
    reason: Reason


class UpdateProviderRequest(AdminRequest):
    nullable_fields = frozenset({"base_url"})
    provider_id: PydanticObjectId
    expected_version: Version
    name: Name | None = None
    base_url: str | None = None
    is_active: bool | None = Field(default=None, strict=True)
    reason: Reason


class RotateProviderApiKeyRequest(AdminRequest):
    provider_id: PydanticObjectId
    expected_version: Version
    api_key: SecretStr
    reason: Reason


class CreateModelRequest(AdminRequest):
    nullable_fields = frozenset({"context_window_tokens", "max_output_tokens"})
    display_name: Name
    model_family: ModelFamily = ModelFamily.GENERIC
    support_thinking: bool = False
    support_vision: bool = False
    support_tools: bool = True
    context_window_tokens: TokenLimit | None = None
    max_output_tokens: TokenLimit | None = None
    reason: Reason


class UpdateModelRequest(AdminRequest):
    nullable_fields = frozenset({"context_window_tokens", "max_output_tokens"})
    model_id: PydanticObjectId
    expected_version: Version
    display_name: Name | None = None
    model_family: ModelFamily | None = None
    support_thinking: bool | None = Field(default=None, strict=True)
    support_vision: bool | None = Field(default=None, strict=True)
    support_tools: bool | None = Field(default=None, strict=True)
    context_window_tokens: TokenLimit | None = None
    max_output_tokens: TokenLimit | None = None
    is_active: bool | None = Field(default=None, strict=True)
    reason: Reason


class CreateModelMappingRequest(AdminRequest):
    model_id: PydanticObjectId
    provider_id: PydanticObjectId
    expected_version: Version = Field(description="所属 Model 的 config_version")
    provider_model_name: Name
    input_billing_ratio: BillingRatio = Decimal("1")
    cached_input_billing_ratio: BillingRatio = Decimal("1")
    output_billing_ratio: BillingRatio = Decimal("1")
    priority: int = Field(default=0, ge=0, le=2**31 - 1, strict=True)
    is_active: bool = True
    reason: Reason


class UpdateModelMappingRequest(AdminRequest):
    mapping_id: PydanticObjectId
    expected_version: Version = Field(description="所属 Model 的 config_version")
    provider_model_name: Name | None = None
    input_billing_ratio: BillingRatio | None = None
    cached_input_billing_ratio: BillingRatio | None = None
    output_billing_ratio: BillingRatio | None = None
    priority: int | None = Field(default=None, ge=0, le=2**31 - 1, strict=True)
    is_active: bool | None = Field(default=None, strict=True)
    reason: Reason


class SetPreferredMappingRequest(AdminRequest):
    model_id: PydanticObjectId
    mapping_id: PydanticObjectId
    expected_version: Version
    reason: Reason


class AdminProviderResponse(ProviderResponse):
    has_api_key: bool
    config_version: int
    config_updated_at: datetime | None
    created_at: datetime


class AdminMappingResponse(ModelProviderMappingResponse):
    id: str
    provider_type: ProviderType | None
    provider_is_active: bool
    is_available: bool


class AdminModelResponse(ModelResponse):
    config_version: int
    created_at: datetime
    updated_at: datetime
    mappings: list[AdminMappingResponse] = Field(default_factory=list)
    is_available: bool


class ProviderDetailResponse(BaseModel):
    provider: AdminProviderResponse
    models: list[AdminModelResponse]


class MappingListResponse(BaseModel):
    model_id: str
    config_version: int
    mappings: list[AdminMappingResponse]


class AdminWriteResponse(BaseModel):
    target_id: str
    config_version: int
    request_id: str = Field(description="服务端生成的请求 ID，可用于检索操作日志")
    model_id: str | None = None
    effective_from: Literal["next_resolution"] = "next_resolution"
