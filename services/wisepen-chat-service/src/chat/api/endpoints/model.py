from fastapi import APIRouter

from chat.domain.entities import ModelType
from chat.api.schemas.model import ModelInfo, ModelsResponse
from chat.core.config.app_settings import settings
from chat.domain.entities.model import ModelConfig
from common.core.domain import R

router = APIRouter()


@router.get("/listModels", response_model=R[ModelsResponse])
async def get_models():
    configs = await ModelConfig.find(ModelConfig.is_active == True).to_list()

    standard_models = []
    advanced_models = []
    other_models = []
    
    for config in configs:
        model_info = ModelInfo(
            id=config.id,
            name=config.name,
            type=config.type,
            providers=config.providers,
            ratio=config.ratio,
            is_default=(config.id == settings.DEFAULT_MODEL),
        )
        if config.type == ModelType.STANDARD_MODEL:
            standard_models.append(model_info)
        elif config.type == ModelType.ADVANCED_MODEL:
            advanced_models.append(model_info)
        else:
            other_models.append(model_info)
    
    return R.success(data=ModelsResponse(
        standard_models=standard_models,
        advanced_models=advanced_models,
        other_models=other_models,
    ))