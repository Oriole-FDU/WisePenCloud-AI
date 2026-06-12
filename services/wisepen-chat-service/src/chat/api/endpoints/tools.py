from typing import List

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from chat.api.schemas.tool import ToolOption
from chat.application.tools.core import ToolRegistry
from chat.container import Container
from common.core.domain import R
from common.security import require_login

router = APIRouter()


def _tool_label(tool_name: str) -> str:
    return tool_name.replace("_", " ").title()


@router.get("", response_model=R[List[ToolOption]])
@inject
async def list_tools(
    user_id: str = Depends(require_login),
    registry: ToolRegistry = Depends(Provide[Container.tool_registry]),
):
    del user_id
    options: list[ToolOption] = []
    for schema in registry.schemas():
        tool_name = schema["function"]["name"]
        tool = registry.get(tool_name)
        if tool is None or not tool.definition.policy.expose_by_default:
            continue
        options.append(ToolOption(toolId=tool_name, label=_tool_label(tool_name)))
    return R.success(data=options)

