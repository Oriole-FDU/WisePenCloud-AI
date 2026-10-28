import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from beanie import PydanticObjectId
from pydantic import SecretStr

from chat.domain.entities.model import Model, ModelProviderMapping
from chat.domain.entities.provider import Provider
from chat.domain.error_codes import ChatErrorCode
from chat.domain.model_configuration import mask_api_key, supports_model_family
from chat.domain.repositories.admin_model_repo import AdminModelInfo, AdminModelRepository
from common.core.domain import ResultCode
from common.core.exceptions import ServiceException
from common.logger import info


@dataclass(frozen=True)
class AdminActor:
    user_id: str
    reason: str
    request_id: str


@dataclass(frozen=True)
class AdminWriteResult:
    target_id: str
    config_version: int
    request_id: str
    log_fields: dict[str, Any] = field(repr=False)
    model_id: str | None = None


class AdminModelManagement:
    """系统模型配置操作；操作者与资源 owner 分离，事务提交后记录操作日志。"""

    def __init__(self, repository: AdminModelRepository, default_model_id: str):
        self.repo = repository
        self.default_model_id = default_model_id

    async def list_providers(
        self, *, page: int, size: int, filters: dict[str, Any] | None = None, keyword: str | None = None,
    ) -> tuple[list[Provider], int]:
        return await self.repo.page(Provider, page=page, size=size, filters=filters, keyword=keyword)

    async def get_provider(self, provider_id: PydanticObjectId) -> tuple[Provider, list[AdminModelInfo]]:
        provider = await self.repo.get(Provider, provider_id)
        mappings = await self.repo.mappings(provider_id=provider_id)
        models = await self.repo.models_for(mappings)
        return provider, await self._model_details(models)

    async def list_models(
        self, *, page: int, size: int, filters: dict[str, Any] | None = None, keyword: str | None = None,
    ) -> tuple[list[AdminModelInfo], int]:
        models, total = await self.repo.page(Model, page=page, size=size, filters=filters, keyword=keyword)
        return await self._model_details(models), total

    async def get_model(self, model_id: PydanticObjectId) -> AdminModelInfo:
        model = await self.repo.get(Model, model_id)
        return (await self._model_details([model]))[0]

    async def _model_details(self, models: list[Model]) -> list[AdminModelInfo]:
        mappings = await self.repo.mappings(model_ids=[m.id for m in models])
        providers = await self.repo.providers_for(mappings)
        by_model: dict[PydanticObjectId, list[ModelProviderMapping]] = {m.id: [] for m in models}
        for mapping in mappings:
            by_model[mapping.model_id].append(mapping)
        return [AdminModelInfo(model, by_model[model.id], providers) for model in models]

    async def create_provider(self, fields: dict[str, Any], actor: AdminActor) -> AdminWriteResult:
        fields = {**fields, "api_key": fields["api_key"].get_secret_value()}

        async def operation(session: Any) -> AdminWriteResult:
            provider = Provider(**fields, is_active=False, config_updated_at=datetime.now(timezone.utc))
            provider.api_key_fingerprint = mask_api_key(provider.api_key)
            await self.repo.insert(provider, session)
            return self._build_change("create_provider", None, provider, fields, actor)

        return await self._write(operation, ChatErrorCode.PROVIDER_ALREADY_EXISTS)

    async def update_provider(
        self, provider_id: PydanticObjectId, expected_version: int,
        updates: dict[str, Any], actor: AdminActor,
    ) -> AdminWriteResult:
        self._nonempty(updates)

        async def operation(session: Any) -> AdminWriteResult:
            before = await self.repo.get(Provider, provider_id, session)
            after = await self.repo.update_config(before, expected_version, updates, session)
            if after.is_active and not after.api_key.strip():
                raise ServiceException(ChatErrorCode.MODEL_CONFIGURATION_INVALID)
            if "is_active" in updates:
                mappings = await self.repo.mappings(provider_id=provider_id, session=session)
                for model in await self.repo.models_for(mappings, session):
                    model = await self.repo.update_config(model, model.config_version, {}, session)
                    await self._check_model(model, session, require_available=False)
            return self._build_change("update_provider", before, after, updates, actor)

        return await self._write(operation, ChatErrorCode.PROVIDER_ALREADY_EXISTS)

    async def rotate_provider_key(
        self, provider_id: PydanticObjectId, expected_version: int, api_key: SecretStr, actor: AdminActor,
    ) -> AdminWriteResult:
        key = api_key.get_secret_value()

        async def operation(session: Any) -> AdminWriteResult:
            before = await self.repo.get(Provider, provider_id, session)
            updates = {"api_key": key, "api_key_fingerprint": mask_api_key(key)}
            after = await self.repo.update_config(before, expected_version, updates, session)
            return self._build_change("rotate_provider_api_key", before, after, updates, actor)

        return await self._write(operation, ChatErrorCode.PROVIDER_ALREADY_EXISTS)

    async def create_model(self, fields: dict[str, Any], actor: AdminActor) -> AdminWriteResult:
        async def operation(session: Any) -> AdminWriteResult:
            model = Model(**fields, is_active=False)
            self._check_limits(model)
            await self.repo.insert(model, session)
            return self._build_change("create_model", None, model, fields, actor)

        return await self._write(operation, ChatErrorCode.MODEL_ALREADY_EXISTS)

    async def update_model(
        self, model_id: PydanticObjectId, expected_version: int, updates: dict[str, Any], actor: AdminActor,
    ) -> AdminWriteResult:
        self._nonempty(updates)

        async def operation(session: Any) -> AdminWriteResult:
            before = await self.repo.get(Model, model_id, session)
            after = await self.repo.update_config(before, expected_version, updates, session)
            await self._check_model(after, session, require_available=after.is_active)
            return self._build_change("update_model", before, after, updates, actor)

        return await self._write(operation, ChatErrorCode.MODEL_ALREADY_EXISTS)

    async def create_mapping(
        self, model_id: PydanticObjectId, provider_id: PydanticObjectId, expected_version: int,
        fields: dict[str, Any], actor: AdminActor,
    ) -> AdminWriteResult:
        async def operation(session: Any) -> AdminWriteResult:
            model = await self.repo.get(Model, model_id, session)
            provider = await self.repo.get(Provider, provider_id, session)
            self._check_compatibility(model, provider)
            model = await self.repo.update_config(model, expected_version, {}, session)
            # 与 Provider 启停竞争同一文档，防止新增映射避开启停的关联检查。
            await self.repo.update_config(provider, provider.config_version, {}, session)
            mapping = ModelProviderMapping(model_id=model_id, provider_id=provider_id, **fields)
            await self.repo.insert(mapping, session)
            await self._check_model(model, session, require_available=False)
            return self._build_change(
                "create_mapping", None, mapping, fields, actor,
                version=model.config_version, before_version=expected_version,
            )

        return await self._write(operation, ChatErrorCode.MODEL_MAPPING_ALREADY_EXISTS)

    async def update_mapping(
        self, mapping_id: PydanticObjectId, expected_version: int,
        updates: dict[str, Any], actor: AdminActor,
    ) -> AdminWriteResult:
        self._nonempty(updates)

        async def operation(session: Any) -> AdminWriteResult:
            before = await self.repo.get(ModelProviderMapping, mapping_id, session)
            model = await self.repo.get(Model, before.model_id, session)
            provider = await self.repo.get(Provider, before.provider_id, session)
            self._check_compatibility(model, provider)
            model = await self.repo.update_config(model, expected_version, {}, session)
            await self.repo.update_config(provider, provider.config_version, {}, session)
            fields = dict(updates)
            if fields.get("is_active") is False:
                fields["is_preferred"] = False
            after = await self.repo.update_mapping(mapping_id, fields, session)
            await self._check_model(model, session, require_available=False)
            return self._build_change(
                "update_mapping", before, after, fields, actor,
                version=model.config_version, before_version=expected_version,
            )

        return await self._write(operation, ChatErrorCode.MODEL_MAPPING_ALREADY_EXISTS)

    async def set_preferred(
        self, model_id: PydanticObjectId, mapping_id: PydanticObjectId,
        expected_version: int, actor: AdminActor,
    ) -> AdminWriteResult:
        async def operation(session: Any) -> AdminWriteResult:
            model = await self.repo.get(Model, model_id, session)
            mapping = await self.repo.get(ModelProviderMapping, mapping_id, session)
            if mapping.model_id != model_id:
                raise ServiceException(ChatErrorCode.MODEL_MAPPING_NOT_FOUND)
            provider = await self.repo.get(Provider, mapping.provider_id, session)
            self._check_compatibility(model, provider)
            if not mapping.is_active or not provider.is_active or not provider.api_key:
                raise ServiceException(ChatErrorCode.MODEL_CONFIGURATION_INVALID)
            model = await self.repo.update_config(model, expected_version, {}, session)
            await self.repo.update_config(provider, provider.config_version, {}, session)
            mappings = await self.repo.mappings(model_ids=[model_id], session=session)
            previous = [str(m.id) for m in mappings if m.is_preferred]
            await self.repo.clear_preferred(model_id, session)
            after = await self.repo.update_mapping(mapping_id, {"is_preferred": True}, session)
            return self._build_change(
                "set_preferred_mapping", mapping, after, {"is_preferred": True}, actor,
                version=model.config_version, before_version=expected_version,
                extra_changes={"preferred_mapping_ids": {"before": previous, "after": [str(mapping_id)]}},
            )

        return await self._write(operation, ChatErrorCode.MODEL_MAPPING_ALREADY_EXISTS)

    async def _write(
        self, operation: Callable[[Any], Awaitable[AdminWriteResult]], duplicate_code: ChatErrorCode,
    ) -> AdminWriteResult:
        result = await self.repo.write(operation, duplicate_code)
        # with_transaction 可能重试回调；只在最终提交成功后记录一次。
        try:
            info("admin configuration updated.", **result.log_fields)
        except Exception:
            # 日志失败不把已经提交的配置修改变成失败响应，也不输出潜在敏感异常。
            pass
        return result

    @staticmethod
    def _nonempty(updates: dict[str, Any]) -> None:
        if not updates:
            raise ServiceException(ResultCode.PARAM_ERROR, "至少提供一个待更新字段")

    @staticmethod
    def _check_limits(model: Model) -> None:
        if (
            model.context_window_tokens is not None and model.max_output_tokens is not None
            and model.max_output_tokens >= model.context_window_tokens
        ):
            raise ServiceException(ChatErrorCode.MODEL_CONFIGURATION_INVALID)

    @staticmethod
    def _check_compatibility(model: Model, provider: Provider) -> None:
        if not supports_model_family(provider.type, model.model_family):
            raise ServiceException(ChatErrorCode.MODEL_PROVIDER_TYPE_UNSUPPORTED)

    async def _check_model(self, model: Model, session: Any, *, require_available: bool) -> None:
        self._check_limits(model)
        mappings = await self.repo.mappings(model_ids=[model.id], session=session)
        providers = await self.repo.providers_for(mappings, session)
        available = False
        for mapping in mappings:
            provider = providers.get(mapping.provider_id)
            if provider is None:
                continue
            self._check_compatibility(model, provider)
            available |= mapping.is_active and provider.is_active and bool(provider.api_key)
        if str(model.id) == self.default_model_id and (not model.is_active or not available):
            raise ServiceException(ChatErrorCode.DEFAULT_MODEL_UNAVAILABLE)
        if require_available and not available:
            raise ServiceException(ChatErrorCode.MODEL_CONFIGURATION_INVALID)

    @staticmethod
    def _build_change(
        action: str, before: Provider | Model | ModelProviderMapping | None,
        after: Provider | Model | ModelProviderMapping, fields: dict[str, Any],
        actor: AdminActor, *, version: int | None = None,
        before_version: int | None = None, extra_changes: dict[str, Any] | None = None,
    ) -> AdminWriteResult:
        target_type = {Provider: "provider", Model: "model", ModelProviderMapping: "mapping"}[type(after)]
        old = before.model_dump(mode="json", include=set(fields)) if before else {}
        new = after.model_dump(mode="json", include=set(fields))
        # 只从允许修改的字段构建脱敏摘要，不将完整 Provider 或请求体写入日志。
        changes = {}
        for field in fields:
            if field == "api_key":
                changes[field] = {"before": mask_api_key(old.get(field, "")), "after": mask_api_key(new[field])}
            else:
                changes[field] = {"before": old.get(field), "after": new.get(field)}
        changes.update(extra_changes or {})
        reason = actor.reason
        for document in (before, after):
            key = document.api_key if isinstance(document, Provider) else None
            if key:
                reason = reason.replace(key, "[REDACTED]")
                for change in changes.values():
                    for side, value in change.items():
                        if isinstance(value, str):
                            change[side] = value.replace(key, "[REDACTED]")
        version = version if version is not None else after.config_version
        if before_version is None and before is not None:
            before_version = before.config_version
        target_id = str(after.id)
        model_id = str(after.model_id) if isinstance(after, ModelProviderMapping) else None
        # 复杂字段显式转成 JSON 字符串，便于现有 OTel attributes 链路存储和检索。
        log_fields = {
            "operUserId": actor.user_id,
            "action": action,
            "targetType": target_type,
            "targetId": target_id,
            "modelId": model_id,
            "beforeVersion": before_version,
            "afterVersion": version,
            "reason": reason,
            "requestId": actor.request_id,
            "changedFields": json.dumps(sorted(changes), ensure_ascii=False),
            "changes": json.dumps(changes, ensure_ascii=False),
        }
        return AdminWriteResult(
            target_id=target_id,
            config_version=version,
            request_id=actor.request_id,
            log_fields=log_fields,
            model_id=model_id,
        )
