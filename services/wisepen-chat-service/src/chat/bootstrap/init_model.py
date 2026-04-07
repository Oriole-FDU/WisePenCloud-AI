from typing import Dict, List

from common.logger import log_event
from chat.domain.entities import ModelConfig, ProviderMap, ProviderId



PROVIDER_MAPPINGS: Dict[str, List[ProviderMap]] = {
    # ==================== Standard 模型 (费率 x1) ====================
    "gpt-4o-mini": [
        ProviderMap(provider_id=ProviderId.ZHIZENGZENG, model_id="gpt-4o-mini"),
    ],

    # ==================== Advanced 模型 (费率 x10) ====================
    "gpt-4o": [
        ProviderMap(provider_id=ProviderId.ZHIZENGZENG, model_id="gpt-4o"),
    ],
    "gpt-5.4": [
        ProviderMap(provider_id=ProviderId.ZHIZENGZENG, model_id="gpt-5.4"),
    ],
    "gemini-2.5-pro": [
        ProviderMap(provider_id=ProviderId.ZHIZENGZENG, model_id="gemini-2.5-pro-preview"),
    ],
    "deepseek-reasoner": [
        ProviderMap(provider_id=ProviderId.ZHIZENGZENG, model_id="deepseek-reasoner"),
    ],
    "qwen-max": [
        ProviderMap(provider_id=ProviderId.ZHIZENGZENG, model_id="qwen-max"),
    ],
}


async def init_models():
    """
    同步模型配置到数据库（增、删、改）
    - 新增：PROVIDER_MAPPINGS 中有，数据库中没有的
    - 删除：数据库中有，PROVIDER_MAPPINGS 中没有的
    - 更新：已存在的模型，如果需要可以更新 providers
    """
    existing_models = await ModelConfig.find().to_list()

    for model in existing_models:
        if model.id not in PROVIDER_MAPPINGS.keys():
            await model.delete()
            log_event(f"删除模型配置: {model.id}")

    for model_id, providers in PROVIDER_MAPPINGS.items():
        existing = await ModelConfig.find_one(ModelConfig.id == model_id)

        if not existing:
            config = ModelConfig(id=model_id, providers=providers)
            await config.insert()
            log_event(f"新增模型配置: {model_id}")
        else:
            if existing.providers != providers:
                existing.providers = providers
                await existing.save()
                log_event(f"更新模型配置: {model_id}")