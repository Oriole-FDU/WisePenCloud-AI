from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any, List, Optional

from beanie import PydanticObjectId

from chat.domain.entities.model import Model, ModelScope, ModelProviderMapping
from chat.domain.entities.provider import Provider, ProviderScope, ProviderType


@dataclass(frozen=True)
class ModelInfo:
    model: Model
    mappings: List[ModelProviderMapping]


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
    def billing_ratio(self) -> int:
        return self.model.billing_ratio

    @property
    def is_billable(self) -> bool:
        return self.provider.scope == ProviderScope.SYSTEM and self.model.billing_ratio > 0

    def billable_tokens(self, token_usage: int) -> int:
        if token_usage <= 0 or not self.is_billable:
            return 0
        return token_usage * self.model.billing_ratio

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
