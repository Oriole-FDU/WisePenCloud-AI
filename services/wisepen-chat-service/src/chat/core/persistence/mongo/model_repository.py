from datetime import datetime, timezone
from typing import Any, List, Optional

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from chat.domain.entities.model import Model, ModelProviderMapping, ModelScope
from chat.domain.entities.provider import Provider, ProviderScope, ProviderType
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories.model_repo import ModelInfo, ModelRepository, ModelRequestInfo
from common.core.exceptions import ServiceException


class MongoModelRepository(ModelRepository):
    """Model / ModelProviderMapping / Provider 的 MongoDB 仓储实现。user_id=None 表示 SYSTEM，否则表示 USER。"""

    async def get_model(
        self,
        model_id: PydanticObjectId,
        user_id: Optional[str] = None,
    ) -> Model:
        model = await Model.find_one(
            Model.id == model_id,
            Model.scope == self._scope_for(user_id),
            Model.owner_user_id == user_id,
        )
        if model is None:
            raise ServiceException(ChatErrorCode.MODEL_NOT_FOUND)

        return model

    async def list_models_and_mappings(
        self,
        user_id: Optional[str] = None,
    ) -> List[ModelInfo]:
        models = await Model.find(
            Model.scope == self._scope_for(user_id),
            Model.owner_user_id == user_id,
        ).sort("-is_active", "-updated_at").to_list()

        return [
            ModelInfo(
                model=model,
                mappings=await self._list_mappings_for_model(model, user_id),
            )
            for model in models
        ]

    async def _list_mappings_for_model(
        self,
        model: Model,
        user_id: Optional[str],
    ) -> List[ModelProviderMapping]:
        return await ModelProviderMapping.find(
            ModelProviderMapping.model_id == model.id,
            ModelProviderMapping.owner_user_id == user_id,
        ).sort("-is_active", "-is_preferred", "+priority", "+created_at").to_list()

    async def list_models_by_provider_id(
        self,
        provider_id: PydanticObjectId,
        user_id: Optional[str] = None,
    ) -> List[ModelInfo]:
        mappings = await ModelProviderMapping.find(
            ModelProviderMapping.provider_id == provider_id,
            ModelProviderMapping.owner_user_id == user_id,
        ).sort("-is_active", "-is_preferred", "+priority", "+created_at").to_list()

        result: List[ModelInfo] = []
        for mapping in mappings:
            model = await Model.find_one(
                Model.id == mapping.model_id,
                Model.scope == self._scope_for(user_id),
                Model.owner_user_id == user_id,
            )
            if model is not None:
                result.append(ModelInfo(model=model, mappings=[mapping]))

        return result

    async def create_model(
        self,
        model: Model,
        user_id: Optional[str] = None,
    ) -> Model:
        now = datetime.now(timezone.utc)

        model.scope = self._scope_for(user_id)
        model.owner_user_id = user_id
        model.is_active = True
        model.created_at = model.created_at or now
        model.updated_at = now

        try:
            await model.insert()
        except DuplicateKeyError:
            raise ServiceException(ChatErrorCode.MODEL_ALREADY_EXISTS)

        return model

    async def update_model(
        self,
        model_id: PydanticObjectId,
        updates: dict[str, Any],
        user_id: Optional[str] = None,
    ) -> Model:
        model = await self.get_model(model_id, user_id)

        if "display_name" in updates:
            model.display_name = updates["display_name"]
        if "vendor" in updates:
            model.vendor = updates["vendor"]
        if "type" in updates:
            model.type = updates["type"]
        if "billing_ratio" in updates:
            model.billing_ratio = updates["billing_ratio"]
        if "support_thinking" in updates:
            model.support_thinking = updates["support_thinking"]
        if "support_vision" in updates:
            model.support_vision = updates["support_vision"]
        if "support_tools" in updates:
            model.support_tools = updates["support_tools"]
        if "support_streaming" in updates:
            model.support_streaming = updates["support_streaming"]
        if "context_window_tokens" in updates:
            model.context_window_tokens = updates["context_window_tokens"]
        if "max_output_tokens" in updates:
            model.max_output_tokens = updates["max_output_tokens"]
        if "is_active" in updates:
            model.is_active = updates["is_active"]

        model.updated_at = datetime.now(timezone.utc)

        try:
            await model.save()
        except DuplicateKeyError:
            raise ServiceException(ChatErrorCode.MODEL_ALREADY_EXISTS)

        return model

    async def delete_model(
        self,
        model_id: PydanticObjectId,
        user_id: Optional[str] = None,
    ) -> None:
        model = await self.get_model(model_id, user_id)

        mappings = await ModelProviderMapping.find(
            ModelProviderMapping.model_id == model_id,
            ModelProviderMapping.owner_user_id == user_id,
        ).to_list()

        for mapping in mappings:
            await mapping.delete()

        await model.delete()

    async def bind_model_to_provider(
        self,
        model_id: PydanticObjectId,
        provider_id: PydanticObjectId,
        provider_model_name: str,
        user_id: Optional[str] = None,
        *,
        is_preferred: bool = True,
        is_active: bool = True,
    ) -> ModelProviderMapping:
        await self.get_model(model_id, user_id)

        provider = await Provider.find_one(
            Provider.id == provider_id,
            Provider.scope == self._provider_scope_for(user_id),
            Provider.owner_user_id == user_id,
        )
        if provider is None:
            raise ServiceException(ChatErrorCode.PROVIDER_NOT_FOUND)

        if provider.type != ProviderType.OPENAI_COMPATIBLE_LLM:
            raise ServiceException(ChatErrorCode.MODEL_PROVIDER_TYPE_UNSUPPORTED)

        mapping = await ModelProviderMapping.find_one(
            ModelProviderMapping.model_id == model_id,
            ModelProviderMapping.provider_id == provider_id,
            ModelProviderMapping.owner_user_id == user_id,
        )

        now = datetime.now(timezone.utc)
        is_preferred = False if not is_active else is_preferred

        if mapping is None:
            mapping = ModelProviderMapping(
                model_id=model_id,
                provider_id=provider_id,
                provider_model_name=provider_model_name,
                owner_user_id=user_id,
                is_preferred=is_preferred,
                is_active=is_active,
                priority=0,
                created_at=now,
                updated_at=now,
            )

            try:
                if is_preferred: # 如果设为首选
                    await self._clear_preferred_mappings(model_id, user_id, now) # 移除其他首选项
                await mapping.insert()
            except DuplicateKeyError:
                raise ServiceException(ChatErrorCode.MODEL_MAPPING_ALREADY_EXISTS)

            return mapping

        mapping.provider_model_name = provider_model_name
        mapping.updated_at = now

        try:
            if is_preferred == True and mapping.is_preferred == False: # 如果设为首选且此前不是首选
                await self._clear_preferred_mappings(model_id, user_id, now) # 移除其他首选项

            mapping.is_preferred = is_preferred
            mapping.is_active = is_active
            await mapping.save()
        except DuplicateKeyError:
            raise ServiceException(ChatErrorCode.MODEL_MAPPING_ALREADY_EXISTS)

        return mapping

    async def _clear_preferred_mappings(
        self,
        model_id: PydanticObjectId,
        owner_user_id: Optional[str],
        now: datetime,
    ) -> None:
        mappings = await ModelProviderMapping.find(
            ModelProviderMapping.model_id == model_id,
            ModelProviderMapping.owner_user_id == owner_user_id,
            ModelProviderMapping.is_preferred == True,
        ).to_list()

        for mapping in mappings:
            mapping.is_preferred = False
            mapping.updated_at = now
            await mapping.save()

    async def unbind_model_from_provider(
        self,
        model_id: PydanticObjectId,
        provider_id: PydanticObjectId,
        user_id: Optional[str] = None,
    ) -> None:
        await self.get_model(model_id, user_id)

        mapping = await ModelProviderMapping.find_one(
            ModelProviderMapping.model_id == model_id,
            ModelProviderMapping.provider_id == provider_id,
            ModelProviderMapping.owner_user_id == user_id,
        )
        if mapping is None:
            raise ServiceException(ChatErrorCode.MODEL_MAPPING_NOT_FOUND)

        was_preferred = mapping.is_preferred
        await mapping.delete()

        if was_preferred:
            await self._promote_next_preferred_mapping(model_id, user_id)

    async def _promote_next_preferred_mapping(
        self,
        model_id: PydanticObjectId,
        owner_user_id: Optional[str],
    ) -> None:
        mappings = await ModelProviderMapping.find(
            ModelProviderMapping.model_id == model_id,
            ModelProviderMapping.owner_user_id == owner_user_id,
            ModelProviderMapping.is_active == True,
        ).sort("+priority", "+created_at").limit(1).to_list()

        if not mappings:
            return

        mapping = mappings[0]
        mapping.is_preferred = True
        mapping.updated_at = datetime.now(timezone.utc)
        await mapping.save()

    async def resolve_model_for_chat(
        self,
        model_id: PydanticObjectId,
        user_id: Optional[str] = None,
        provider_id: Optional[PydanticObjectId] = None,
        scope: Optional[ModelScope] = None,
    ) -> ModelRequestInfo:
        model = await self._find_chat_model(model_id, user_id, scope)
        if model is None:
            raise ServiceException(ChatErrorCode.MODEL_NOT_FOUND)

        if provider_id is not None:
            mapping = await ModelProviderMapping.find_one(
                ModelProviderMapping.model_id == model.id,
                ModelProviderMapping.provider_id == provider_id,
                ModelProviderMapping.owner_user_id == model.owner_user_id,
                ModelProviderMapping.is_active == True,
            )
        else:
            mappings = await ModelProviderMapping.find(
                ModelProviderMapping.model_id == model.id,
                ModelProviderMapping.owner_user_id == model.owner_user_id,
                ModelProviderMapping.is_active == True,
            ).sort("-is_preferred", "+priority", "+created_at").limit(1).to_list()

            mapping = mappings[0] if mappings else None

        if mapping is None:
            raise ServiceException(ChatErrorCode.MODEL_MAPPING_NOT_FOUND)

        provider = await Provider.find_one(
            Provider.id == mapping.provider_id,
            Provider.scope == self._provider_scope_for(model.owner_user_id),
            Provider.owner_user_id == model.owner_user_id,
            Provider.is_active == True,
        )
        if provider is None:
            raise ServiceException(ChatErrorCode.PROVIDER_NOT_FOUND)

        return ModelRequestInfo(model=model, mapping=mapping, provider=provider)

    async def _find_chat_model(
        self,
        model_id: PydanticObjectId,
        user_id: Optional[str],
        scope: Optional[ModelScope],
    ) -> Optional[Model]:
        if scope is not None:
            owner_user_id = user_id if scope == ModelScope.USER else None
            return await Model.find_one(
                Model.id == model_id,
                Model.scope == scope,
                Model.owner_user_id == owner_user_id,
                Model.is_active == True,
            )

        if user_id is not None:
            user_model = await Model.find_one(
                Model.id == model_id,
                Model.scope == ModelScope.USER,
                Model.owner_user_id == user_id,
                Model.is_active == True,
            )
            if user_model is not None:
                return user_model

        return await Model.find_one(
            Model.id == model_id,
            Model.scope == ModelScope.SYSTEM,
            Model.owner_user_id == None,
            Model.is_active == True,
        )

    @staticmethod
    def _scope_for(user_id: Optional[str]) -> ModelScope:
        return ModelScope.USER if user_id is not None else ModelScope.SYSTEM

    @staticmethod
    def _provider_scope_for(user_id: Optional[str]) -> ProviderScope:
        return ProviderScope.USER if user_id is not None else ProviderScope.SYSTEM
