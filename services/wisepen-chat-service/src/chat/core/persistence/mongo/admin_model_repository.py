"""管理员配置持久化：限定系统资源，原子版本更新及配置事务。"""
import re
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from beanie import PydanticObjectId
from beanie.odm.utils.encoder import Encoder
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from chat.domain.entities.model import Model, ModelProviderMapping
from chat.domain.entities.provider import Provider
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories.admin_model_repo import (
    AdminConfig,
    AdminModelRepository,
    AdminResource,
    WriteResult,
)
from common.core.exceptions import ServiceException


class MongoAdminModelRepository(AdminModelRepository):
    @staticmethod
    def _scope(document_type: type[AdminResource]) -> dict[str, Any]:
        filters: dict[str, Any] = {"owner_user_id": None}
        if document_type in (Model, Provider):
            filters["scope"] = "SYSTEM"
        return filters

    async def get(
        self, document_type: type[AdminResource], document_id: PydanticObjectId, session: Any = None,
    ) -> AdminResource:
        value = await document_type.find_one(
            {"_id": document_id, **self._scope(document_type)}, session=session,
        )
        if value is None:
            code = {
                Provider: ChatErrorCode.PROVIDER_NOT_FOUND,
                Model: ChatErrorCode.MODEL_NOT_FOUND,
                ModelProviderMapping: ChatErrorCode.MODEL_MAPPING_NOT_FOUND,
            }[document_type]
            raise ServiceException(code)
        return value

    async def page(
        self, document_type: type[AdminConfig], *, page: int, size: int,
        filters: dict[str, Any] | None = None, keyword: str | None = None,
    ) -> tuple[list[AdminConfig], int]:
        query = {**self._scope(document_type), **(filters or {})}
        if keyword:
            query["name" if document_type is Provider else "display_name"] = {
                "$regex": re.escape(keyword), "$options": "i",
            }
        total = await document_type.find(query).count()
        items = await (
            document_type.find(query).sort("-created_at", "-_id")
            .skip((page - 1) * size).limit(size).to_list()
        )
        return items, total

    async def mappings(
        self, model_ids: Iterable[PydanticObjectId] | None = None,
        provider_id: PydanticObjectId | None = None, session: Any = None,
    ) -> list[ModelProviderMapping]:
        query = self._scope(ModelProviderMapping)
        if model_ids is not None:
            query["model_id"] = {"$in": list(model_ids)}
        if provider_id is not None:
            query["provider_id"] = provider_id
        return await ModelProviderMapping.find(query, session=session).sort(
            "-is_preferred", "+priority", "+created_at", "+_id",
        ).to_list()

    async def providers_for(
        self, mappings: list[ModelProviderMapping], session: Any = None,
    ) -> dict[PydanticObjectId, Provider]:
        providers = await Provider.find(
            {**self._scope(Provider), "_id": {"$in": list({m.provider_id for m in mappings})}},
            session=session,
        ).to_list()
        return {provider.id: provider for provider in providers}

    async def models_for(
        self, mappings: list[ModelProviderMapping], session: Any = None,
    ) -> list[Model]:
        return await Model.find(
            {**self._scope(Model), "_id": {"$in": list({m.model_id for m in mappings})}}, session=session,
        ).sort("+_id").to_list()

    async def insert(self, document: AdminResource, session: Any) -> AdminResource:
        return await document.insert(session=session)

    async def update_config(
        self, document: AdminConfig, expected_version: int, updates: dict[str, Any], session: Any,
    ) -> AdminConfig:
        document_type = type(document)
        query = {"_id": document.id, **self._scope(document_type)}
        # 无版本字段的旧记录以版本 1 参与 CAS，首次写入时自动补齐。
        if expected_version == 1:
            query["$or"] = [{"config_version": 1}, {"config_version": {"$exists": False}}]
        else:
            query["config_version"] = expected_version
        now = datetime.now(timezone.utc)
        fields = {**updates, "config_version": expected_version + 1, "updated_at": now}
        if document_type is Provider:
            fields["config_updated_at"] = now
        raw = await document_type.get_pymongo_collection().find_one_and_update(
            query, {"$set": Encoder().encode(fields)}, session=session, return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            raise ServiceException(ChatErrorCode.CONFIG_VERSION_CONFLICT)
        return document_type.model_validate(raw)

    async def update_mapping(
        self, mapping_id: PydanticObjectId, updates: dict[str, Any], session: Any,
    ) -> ModelProviderMapping:
        raw = await ModelProviderMapping.get_pymongo_collection().find_one_and_update(
            {"_id": mapping_id, **self._scope(ModelProviderMapping)},
            {"$set": Encoder().encode({**updates, "updated_at": datetime.now(timezone.utc)})},
            session=session, return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            raise ServiceException(ChatErrorCode.MODEL_MAPPING_NOT_FOUND)
        return ModelProviderMapping.model_validate(raw)

    async def clear_preferred(self, model_id: PydanticObjectId, session: Any) -> None:
        await ModelProviderMapping.get_pymongo_collection().update_many(
            {"model_id": model_id, "is_preferred": True, **self._scope(ModelProviderMapping)},
            {"$set": {"is_preferred": False, "updated_at": datetime.now(timezone.utc)}}, session=session,
        )

    async def write(
        self, operation: Callable[[Any], Awaitable[WriteResult]], duplicate_code: ChatErrorCode,
    ) -> WriteResult:
        client = Provider.get_pymongo_collection().database.client
        try:
            async with client.start_session() as session:
                return await session.with_transaction(
                    operation, read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"),
                )
        except DuplicateKeyError:
            raise ServiceException(duplicate_code) from None
        except OperationFailure as exc:
            if exc.code in (20, 263, 303):
                raise ServiceException(ChatErrorCode.ADMIN_STORAGE_UNAVAILABLE) from None
            if exc.code in (112, 251):
                raise ServiceException(ChatErrorCode.CONFIG_VERSION_CONFLICT) from None
            raise
