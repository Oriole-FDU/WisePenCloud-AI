from fastapi import APIRouter

from chat.domain.entities import ModelType
from chat.domain.entities.model import Model
from chat.api.schemas.model import ModelInfo, ModelsResponse
from chat.core.config.app_settings import settings
from common.core.domain import R

router = APIRouter()


@router.get("/listModels", response_model=R[ModelsResponse])
async def get_models():
    models = await Model.find(Model.is_active == True).to_list()

    standard_models = []
    advanced_models = []
    other_models = []

    for model in models:
        model_info = ModelInfo(
            id=model.id,
            name=model.display_name,
            vendor=model.vendor,
            type=model.type,
            ratio=model.billing_ratio,
            support_thinking=model.support_thinking,
            support_vision=model.support_vision,
            is_default=(model.id == settings.DEFAULT_MODEL_ID),
        )
        if model.type == ModelType.STANDARD_MODEL:
            standard_models.append(model_info)
        elif model.type == ModelType.ADVANCED_MODEL:
            advanced_models.append(model_info)
        else:
            other_models.append(model_info)

    return R.success(data=ModelsResponse(
        standard_models=standard_models,
        advanced_models=advanced_models,
        other_models=other_models,
    ))
