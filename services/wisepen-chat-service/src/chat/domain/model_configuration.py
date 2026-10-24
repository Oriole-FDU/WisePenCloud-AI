from chat.domain.entities.model import ModelFamily
from chat.domain.entities.provider import ProviderType


PROVIDER_MODEL_FAMILIES = {
    ProviderType.ALIBABA: (ModelFamily.QWEN,),
    ProviderType.OPENAI: (ModelFamily.GPT,),
    ProviderType.ANTHROPIC: (ModelFamily.CLAUDE,),
    ProviderType.GOOGLE: (ModelFamily.GEMINI,),
    ProviderType.LITELLM_OPENAI_COMPATIBLE: tuple(ModelFamily),
}


def supports_model_family(provider_type: ProviderType, family: ModelFamily) -> bool:
    return family in PROVIDER_MODEL_FAMILIES.get(provider_type, ())


def mask_api_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}***{api_key[-4:]}"
