from chat.api.schemas.admin_model import (
    AdminMappingResponse,
    AdminModelResponse,
    AdminProviderResponse,
)
from chat.domain.entities.model import Model, ModelProviderMapping
from chat.domain.entities.provider import Provider
from chat.domain.model_configuration import mask_api_key, supports_model_family
from chat.domain.repositories.admin_model_repo import AdminModelInfo


def to_provider_response(provider: Provider) -> AdminProviderResponse:
    return AdminProviderResponse(
        id=str(provider.id),
        name=provider.name,
        base_url=provider.base_url,
        api_key_fingerprint=mask_api_key(provider.api_key),
        scope=provider.scope,
        type=provider.type,
        is_active=provider.is_active,
        has_api_key=bool(provider.api_key),
        config_version=provider.config_version,
        config_updated_at=provider.config_updated_at,
        created_at=provider.created_at,
        input_tokens=provider.input_tokens,
        cached_input_tokens=provider.cached_input_tokens,
        output_tokens=provider.output_tokens,
        billable_tokens=provider.billable_tokens,
    )


def to_mapping_response(
    model: Model, mapping: ModelProviderMapping, provider: Provider | None,
) -> AdminMappingResponse:
    available = bool(
        provider and provider.is_active and provider.api_key and mapping.is_active
        and supports_model_family(provider.type, model.model_family)
    )
    return AdminMappingResponse(
        id=str(mapping.id),
        model_id=str(model.id),
        provider_id=str(mapping.provider_id),
        provider_name=provider.name if provider else None,
        provider_type=provider.type if provider else None,
        provider_is_active=bool(provider and provider.is_active),
        is_available=available,
        provider_model_name=mapping.provider_model_name,
        input_billing_ratio=str(mapping.input_billing_ratio),
        cached_input_billing_ratio=str(mapping.cached_input_billing_ratio),
        output_billing_ratio=str(mapping.output_billing_ratio),
        is_preferred=mapping.is_preferred,
        is_active=mapping.is_active,
        priority=mapping.priority,
    )


def to_model_response(info: AdminModelInfo) -> AdminModelResponse:
    model = info.model
    mapping_responses = [
        to_mapping_response(model, mapping, info.providers.get(mapping.provider_id))
        for mapping in info.mappings
    ]
    return AdminModelResponse(
        id=str(model.id),
        scope=model.scope,
        display_name=model.display_name,
        model_family=model.model_family,
        support_thinking=model.support_thinking,
        support_vision=model.support_vision,
        support_tools=model.support_tools,
        context_window_tokens=model.context_window_tokens,
        max_output_tokens=model.max_output_tokens,
        is_active=model.is_active,
        config_version=model.config_version,
        created_at=model.created_at,
        updated_at=model.updated_at,
        mappings=mapping_responses,
        is_available=model.is_active and any(m.is_available for m in mapping_responses),
    )
