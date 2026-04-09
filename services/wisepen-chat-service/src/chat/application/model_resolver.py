from dataclasses import dataclass
from typing import Optional

from common.logger import log_error
from chat.domain.entities import Provider, ModelProviderMapping
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException


@dataclass(frozen=True)
class ResolvedModel:
    """ModelResolver 的解析结果，包含调用供应商 API 所需的全部信息"""
    provider_model_name: str
    api_base_url: str
    api_key: str


class ModelResolver:
    """
    根据前端传来的统一 model_id，查询映射表和供应商表，
    返回实际的供应商模型名、API 地址和密钥。
    """

    async def resolve(self, model_id: int) -> ResolvedModel:
        mapping = await self._find_mapping(model_id)
        if mapping is None:
            raise ServiceException(
                ChatErrorCode.LLM_GENERATION_FAILED,
                custom_msg=f"模型 '{model_id}' 未配置供应商映射",
            )

        provider = await Provider.get(mapping.provider_id)
        if provider is None or not provider.is_active:
            raise ServiceException(
                ChatErrorCode.LLM_GENERATION_FAILED,
                custom_msg=f"模型 '{model_id}' 的供应商不可用",
            )

        return ResolvedModel(
            provider_model_name=mapping.provider_model_name,
            api_base_url=provider.api_base_url,
            api_key=provider.api_key,
        )

    async def _find_mapping(self, model_id: int) -> Optional[ModelProviderMapping]:
        """优先查找 is_preferred=True 的映射，若不存在则降级到任意可用映射"""
        preferred = await ModelProviderMapping.find_one(
            ModelProviderMapping.model_id == model_id,
            ModelProviderMapping.is_preferred == True,
        )
        if preferred is not None:
            return preferred

        return await ModelProviderMapping.find_one(
            ModelProviderMapping.model_id == model_id,
        )
