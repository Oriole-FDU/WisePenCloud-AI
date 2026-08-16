from chat.api.schemas.tool import ToolResponse
from chat.application.tools.core import Tool
from chat.domain.entities.tool_config import UserToolConfig


def build_tool_response(tool: Tool, entity: UserToolConfig | None) -> ToolResponse:
    definition = tool.definition
    config_spec = definition.config_spec
    ui_spec = definition.ui_spec
    if config_spec is None:
        return ToolResponse(
            name=definition.llm_spec.name,
            display_name=ui_spec.display_name if ui_spec is not None else definition.llm_spec.name.replace("_", " ").strip().title(),
            description=ui_spec.description if ui_spec is not None and ui_spec.description is not None else definition.llm_spec.description,
            selection_mode=definition.policy.selection_mode,
            requires_config=False,
            configured=True,
            enabled=True,
            source=definition.source_spec,
        )

    # 检查用户是否有配置、必填项是否完整
    configured = False
    missing_keys: list[str] = []
    if entity is not None:
        for key in config_spec.required_keys:
            config_source = entity.secret_config if key in config_spec.secret_keys else entity.config
            if config_source.get(key) is None or (isinstance(config_source.get(key), str) and not config_source.get(key).strip()):
                missing_keys.append(key)
        configured = not missing_keys

    return ToolResponse(
        name=definition.llm_spec.name,
        display_name=ui_spec.display_name if ui_spec is not None else definition.llm_spec.name.replace("_", " ").strip().title(),
        description=ui_spec.description if ui_spec is not None and ui_spec.description is not None else definition.llm_spec.description,
        selection_mode=definition.policy.selection_mode,
        requires_config=True,
        configured=configured,
        enabled=entity.enabled if entity is not None else True,
        source=definition.source_spec,
        missing_config_keys=missing_keys,
        config_schema=dict(config_spec.schema),
        secret_fingerprints={
            key: value
            for key, value in (entity.secret_fingerprints if entity is not None else {}).items()
            if key in config_spec.secret_keys
        },
    )
