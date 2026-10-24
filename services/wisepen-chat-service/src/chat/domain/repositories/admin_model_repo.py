from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

from beanie import PydanticObjectId

from chat.domain.entities.model import Model, ModelProviderMapping
from chat.domain.entities.provider import Provider
from chat.domain.error_codes import ChatErrorCode


AdminResource = TypeVar("AdminResource", Provider, Model, ModelProviderMapping)
AdminConfig = TypeVar("AdminConfig", Provider, Model)
WriteResult = TypeVar("WriteResult")


@dataclass(frozen=True)
class AdminModelInfo:
    model: Model
    mappings: list[ModelProviderMapping]
    providers: dict[PydanticObjectId, Provider]


class AdminModelRepository(ABC):
    """系统配置仓储；session 由 write 创建，在相关配置文档的写入间传递。"""

    @abstractmethod
    async def get(
        self, document_type: type[AdminResource], document_id: PydanticObjectId, session: Any = None,
    ) -> AdminResource: pass

    @abstractmethod
    async def page(
        self, document_type: type[AdminConfig], *, page: int, size: int,
        filters: dict[str, Any] | None = None, keyword: str | None = None,
    ) -> tuple[list[AdminConfig], int]: pass

    @abstractmethod
    async def mappings(
        self, model_ids: Iterable[PydanticObjectId] | None = None,
        provider_id: PydanticObjectId | None = None, session: Any = None,
    ) -> list[ModelProviderMapping]: pass

    @abstractmethod
    async def providers_for(
        self, mappings: list[ModelProviderMapping], session: Any = None,
    ) -> dict[PydanticObjectId, Provider]: pass

    @abstractmethod
    async def models_for(
        self, mappings: list[ModelProviderMapping], session: Any = None,
    ) -> list[Model]: pass

    @abstractmethod
    async def insert(self, document: AdminResource, session: Any) -> AdminResource: pass

    @abstractmethod
    async def update_config(
        self, document: AdminConfig, expected_version: int, updates: dict[str, Any], session: Any,
    ) -> AdminConfig: pass

    @abstractmethod
    async def update_mapping(
        self, mapping_id: PydanticObjectId, updates: dict[str, Any], session: Any,
    ) -> ModelProviderMapping: pass

    @abstractmethod
    async def clear_preferred(self, model_id: PydanticObjectId, session: Any) -> None: pass

    @abstractmethod
    async def write(
        self, operation: Callable[[Any], Awaitable[WriteResult]], duplicate_code: ChatErrorCode,
    ) -> WriteResult: pass
