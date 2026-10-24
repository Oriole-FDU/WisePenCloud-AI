from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_CEILING
from typing import Any, List, Optional

from beanie import PydanticObjectId

from chat.domain.entities.model import Model, ModelScope, ModelProviderMapping
from chat.domain.entities.provider import Provider, ProviderScope, ProviderType
from chat.domain.error_codes import ChatErrorCode
from chat.domain.model_configuration import supports_model_family
from common.core.exceptions import ServiceException


@dataclass(frozen=True)
class ModelInfo:
    model: Model
    mappings: List[ModelProviderMapping]

    def available_mappings(self, providers: dict[str, Provider]) -> List[ModelProviderMapping]:
        return [
            mapping for mapping in self.mappings
            if mapping.is_active and (provider := providers.get(str(mapping.provider_id)))
            and provider.is_active and provider.api_key
            and provider.owner_user_id == mapping.owner_user_id
            and provider.scope == (ProviderScope.SYSTEM if mapping.owner_user_id is None else ProviderScope.USER)
            and supports_model_family(provider.type, self.model.model_family)
        ]


@dataclass(frozen=True)
class ModelRequestInfo:
    """
    一次聊天调用前解析出的完整模型调用信息
    """

    model: Model
    mapping: ModelProviderMapping
    provider: Provider
    runtime_options: dict

    @property
    def model_id(self) -> PydanticObjectId:
        return self.model.id

    @property
    def provider_id(self) -> PydanticObjectId:
        return self.mapping.provider_id

    @property
    def model_name(self) -> str:
        return self.mapping.provider_model_name

    @property
    def base_url(self) -> Optional[str]:
        return self.provider.base_url

    @property
    def provider_type(self) -> ProviderType:
        return self.provider.type

    @property
    def api_key(self) -> str:
        return self.provider.api_key

    @property
    def model_scope(self) -> ModelScope:
        return self.model.scope

    @property
    def owner_user_id(self) -> Optional[str]:
        return self.model.owner_user_id

    @property
    def provider_owner_user_id(self) -> Optional[str]:
        return self.mapping.owner_user_id

    @property
    def provider_scope(self) -> ProviderScope:
        return self.provider.scope

    @property
    def input_billing_ratio(self) -> Decimal:
        return self.mapping.input_billing_ratio

    @property
    def cached_input_billing_ratio(self) -> Decimal:
        return self.mapping.cached_input_billing_ratio

    @property
    def output_billing_ratio(self) -> Decimal:
        return self.mapping.output_billing_ratio

    @property
    def is_billable(self) -> bool:
        return self.provider.scope == ProviderScope.SYSTEM and (
            self.mapping.input_billing_ratio > 0
            or self.mapping.cached_input_billing_ratio > 0
            or self.mapping.output_billing_ratio > 0
        )

    def billable_tokens(self, usage: Any) -> int:
        if usage.total_tokens <= 0 or not self.is_billable:
            return 0
        billable = (
            Decimal(usage.uncached_input_tokens) * self.mapping.input_billing_ratio
            + Decimal(usage.cached_input_tokens) * self.mapping.cached_input_billing_ratio
            + Decimal(usage.output_tokens) * self.mapping.output_billing_ratio
        )
        return int(billable.to_integral_value(rounding=ROUND_CEILING))

    @property
    def support_tools(self) -> bool:
        return self.model.support_tools

    @property
    def context_window_tokens(self) -> Optional[int]:
        return self.model.context_window_tokens

    @property
    def max_output_tokens(self) -> Optional[int]:
        return self.model.max_output_tokens

    def with_runtime_options(self, runtime_options: dict[str, Any]) -> "ModelRequestInfo":
        return replace(self, runtime_options=runtime_options)

class ModelRepository(ABC):

    async def refresh_suspended_model(self, snapshot: ModelRequestInfo, user_id: str) -> ModelRequestInfo:
        current = await self.resolve_model_for_chat(
            snapshot.model_id, user_id, snapshot.provider_id, snapshot.runtime_options,
        )
        model_fields = (
            "model_family", "support_thinking", "support_vision", "support_tools",
            "context_window_tokens", "max_output_tokens",
        )
        if (
            current.mapping.id != snapshot.mapping.id or current.model_name != snapshot.model_name
            or current.provider_type != snapshot.provider_type or current.base_url != snapshot.base_url
            or any(getattr(current.model, field) != getattr(snapshot.model, field) for field in model_fields)
        ):
            raise ServiceException(ChatErrorCode.MODEL_CONFIGURATION_CHANGED)
        # 本轮计费倍率等继续使用挂起前快照，仅刷新当前凭证。
        return replace(snapshot, provider=current.provider)

    @abstractmethod
    async def get_model(self, model_id: PydanticObjectId, user_id: Optional[str] = None) -> Model: pass

    @abstractmethod
    async def list_models_and_mappings(self, model_scope: ModelScope, user_id: Optional[str] = None) -> List[ModelInfo]: pass

    @abstractmethod
    async def list_models_by_provider_id(self, provider_id: PydanticObjectId, user_id: Optional[str] = None) -> List[ModelInfo]: pass

    @abstractmethod
    async def create_model(self, model: Model, user_id: Optional[str] = None) -> Model: pass

    @abstractmethod
    async def update_model(
        self,
        model_id: PydanticObjectId,
        updates: dict[str, Any],
        user_id: Optional[str] = None,
    ) -> Model: pass

    @abstractmethod
    async def delete_model(self, model_id: PydanticObjectId, user_id: Optional[str] = None) -> None: pass

    @abstractmethod
    async def bind_model_to_provider(
        self,
        model_id: PydanticObjectId,
        provider_id: PydanticObjectId,
        provider_model_name: str,
        user_id: Optional[str] = None,
        input_billing_ratio: Decimal = Decimal("1"),
        cached_input_billing_ratio: Decimal = Decimal("1"),
        output_billing_ratio: Decimal = Decimal("1"),
        is_preferred: bool = True,
        is_active: bool = True,
    ) -> ModelProviderMapping:
        pass

    @abstractmethod
    async def unbind_model_from_provider(
        self,
        model_id: PydanticObjectId,
        provider_id: PydanticObjectId,
        user_id: Optional[str] = None,
    ) -> None:
        pass

    @abstractmethod
    async def resolve_model_for_chat(
            self,
            model_id: PydanticObjectId,
            user_id: str,
            provider_id: Optional[PydanticObjectId] = None,
            runtime_options: Optional[dict[str, Any]] = None,
    ) -> ModelRequestInfo:
        pass
