from chat.domain.entities.model import ModelFamily, Model
from chat.domain.entities.provider import ProviderType
from chat.domain.error_codes import ChatErrorCode
from chat.domain.model_configuration import supports_model_family
from chat.domain.interfaces import LLMProvider
from chat.domain.repositories.model_repo import ModelRequestInfo
from common.core.exceptions import ServiceException


class LLMProviderResolver:
    """
    LLM Provider 解析器
    """

    def __init__(
        self,
        qwen_adapter: LLMProvider,
        openai_adapter: LLMProvider,
        anthropic_adapter: LLMProvider,
        gemini_adapter: LLMProvider,
        litellm_adapter: LLMProvider,
    ):
        self._qwen_adapter = qwen_adapter
        self._openai_adapter = openai_adapter
        self._anthropic_adapter = anthropic_adapter
        self._gemini_adapter = gemini_adapter
        self._litellm_adapter = litellm_adapter

    def resolve(self, model_request: ModelRequestInfo) -> LLMProvider:
        provider_type = model_request.provider.type
        family = model_request.model.model_family
        if not supports_model_family(provider_type, family):
            raise ServiceException(ChatErrorCode.MODEL_PROVIDER_TYPE_UNSUPPORTED)
        if provider_type == ProviderType.ALIBABA and family == ModelFamily.QWEN:
            return self._qwen_adapter
        if provider_type == ProviderType.OPENAI and family == ModelFamily.GPT:
            return self._openai_adapter
        if provider_type == ProviderType.ANTHROPIC and family == ModelFamily.CLAUDE:
            return self._anthropic_adapter
        if provider_type == ProviderType.GOOGLE and family == ModelFamily.GEMINI:
            return self._gemini_adapter
        if provider_type == ProviderType.LITELLM_OPENAI_COMPATIBLE:
            return self._litellm_adapter
        # 不在这个模型搭配范围内，报错
        raise ServiceException(ChatErrorCode.MODEL_PROVIDER_TYPE_UNSUPPORTED)

    def runtime_options_manifest(self, provider_type: ProviderType) -> dict:
        if provider_type == ProviderType.ALIBABA:
            return self._qwen_adapter.runtime_options_manifest()
        if provider_type == ProviderType.OPENAI:
            return self._openai_adapter.runtime_options_manifest()
        if provider_type == ProviderType.ANTHROPIC:
            return self._anthropic_adapter.runtime_options_manifest()
        if provider_type == ProviderType.GOOGLE:
            return self._gemini_adapter.runtime_options_manifest()
        if provider_type == ProviderType.LITELLM_OPENAI_COMPATIBLE:
            return self._litellm_adapter.runtime_options_manifest()
        return LLMProvider.empty_runtime_options_manifest()
