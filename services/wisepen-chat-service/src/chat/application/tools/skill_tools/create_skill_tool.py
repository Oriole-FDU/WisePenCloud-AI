from __future__ import annotations

from typing import Any, Dict

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.skill_tools.create_skill.models import CreateSkillRequest
from chat.application.tools.skill_tools.create_skill.service import CreateSkillService
from chat.application.tools.skill_tools.create_skill.skill_publisher import (
    SkillPublisher,
)
from chat.application.tools.skill_tools.create_skill.validator import (
    validate_create_skill,
)
from chat.application.tools.tool_settings import tool_settings

PARAMETERS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "$defs": {
        "SkillSection": {
            "type": "object",
            "description": (
                "Recursive heading node. 'heading' is the Markdown heading text for this level "
                "(without # prefix; depth is inferred from tree position, do not write # yourself). "
                "'body' is the prose between this heading and the first child heading, "
                "written in native Markdown (paragraphs, lists, code blocks, tables, blockquotes). "
                "'children' recursively expands sub-headings; an empty array means a leaf node."
            ),
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": (
                        "Unique identifier for this heading node, using English kebab-case slug "
                        "(e.g. 'error-handling'). Used for incremental positioning and partial updates; "
                        "do not use plain numeric indices."
                    ),
                },
                "heading": {"type": "string"},
                "body": {"type": "string"},
                "children": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/SkillSection"},
                },
            },
            "required": ["node_id", "heading", "body", "children"],
            "additionalProperties": False,
        },
        "SkillFile": {
            "type": "object",
            "description": (
                "A file in the skill package. For .md files, use body+children to generate "
                "structured Markdown (reusing the heading tree); for other text files, "
                "put raw content in body with empty children."
            ),
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative file path (e.g. 'api-guide.md', 'template.json'). "
                        "Must not contain '..' or start with '/'."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": (
                        "Optional H1 title for .md files. If omitted, derived from the path filename."
                    ),
                },
                "body": {"type": "string"},
                "children": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/SkillSection"},
                },
            },
            "required": ["path", "body", "children"],
            "additionalProperties": False,
        },
        "SkillScript": {
            "type": "object",
            "description": "An executable script file in the skill package.",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Filename within scripts/ directory (e.g. 'extract.py'). "
                        "Must not contain '..' or start with '/'."
                    ),
                },
                "body": {
                    "type": "string",
                    "description": "Source code of the script.",
                },
            },
            "required": ["path", "body"],
            "additionalProperties": False,
        },
    },
    "properties": {
        "skill_id": {
            "type": "string",
            "description": (
                "Globally unique skill identifier in English kebab-case slug. "
                "Used as the OSS object key and available_skills index key; "
                "must not change after creation."
            ),
        },
        "title": {
            "type": "string",
            "description": (
                "Skill document title, rendered as the sole H1 heading, "
                "also used as the human-readable display name."
            ),
        },
        "trigger_description": {
            "type": "string",
            "description": (
                "Full description of when this skill should be triggered. "
                "This is the sole basis for trigger discovery in the available_skills directory — "
                "the main model reads only this text when deciding whether to load_skill, "
                "never the content below. Must be self-contained and specific; "
                "do not write a vague one-liner."
            ),
        },
        "body": {
            "type": "string",
            "description": (
                "Root body text placed between the H1 title and the first H2 section, "
                "written in native Markdown. Leave empty string if the document "
                "starts directly with sections."
            ),
        },
        "children": {
            "type": "array",
            "description": "Heading tree for H2 and deeper levels, recursive structure.",
            "items": {"$ref": "#/$defs/SkillSection"},
        },
        "references": {
            "type": "array",
            "description": (
                "Reference documents loaded on demand. .md files use the heading tree structure; "
                "other text files use raw body."
            ),
            "items": {"$ref": "#/$defs/SkillFile"},
        },
        "scripts": {
            "type": "array",
            "description": "Executable scripts included in the skill package.",
            "items": {"$ref": "#/$defs/SkillScript"},
        },
        "assets": {
            "type": "array",
            "description": (
                "Templates and resource files. .md files use the heading tree structure; "
                "other text files use raw body."
            ),
            "items": {"$ref": "#/$defs/SkillFile"},
        },
    },
    "required": ["skill_id", "title", "trigger_description", "body", "children"],
    "additionalProperties": False,
}


class CreateSkillTool:
    """从结构化标题树创建并发布新的 Skill 文档。

    Tool 层只负责：参数解析 → 业务校验 → 委托 Service → 返回结果。
    """

    def __init__(
        self,
        skill_publisher: SkillPublisher,
    ) -> None:
        self._service = CreateSkillService(publisher=skill_publisher)
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="create_skill",
                description=(
                    "Create and publish a new Skill document from a structured tree of sections.\n"
                    "\n"
                    "WHEN TO USE:\n"
                    "  - The user explicitly asks to create a new skill.\n"
                    "  - The user wants to save, reuse, or summarize a workflow as a skill.\n"
                    "  - The user asks to turn a set of instructions or best practices into a reusable skill.\n"
                    "\n"
                    "STRUCTURE RULES:\n"
                    "  - trigger_description determines when this Skill should be discovered and triggered.\n"
                    "  - The document body is generated from the heading tree; do NOT write headings in body fields.\n"
                    "  - Use the heading/children structure to organize content hierarchically.\n"
                    "  - references/ and assets/ can contain .md files that also use the heading tree.\n"
                    "  - scripts/ contains executable code files.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=True,
                risk_level=ToolRiskLevel.HIGH,
                required_context_keys=("user_id", "session_id"),
                timeout_seconds=tool_settings.CREATE_SKILL_TOOL_TIMEOUT_SECONDS,
            ),
            preflight_hooks=(),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        # user_id / session_id 由 required_context_keys 保证存在
        user_id = context["user_id"]
        session_id = context["session_id"]

        # 1. 将 kwargs 解析为 CreateSkillRequest
        try:
            request = CreateSkillRequest(**kwargs)
        except Exception as e:
            raise ToolExecutionError(
                reason="invalid_parameters",
                detail_reason=f"Failed to parse create_skill parameters: {e}",
                retryable=False,
            )

        # 2. 执行业务校验（node_id 唯一性 + body 标题检查 + 路径合法性）
        errors = validate_create_skill(request)
        if errors:
            raise ToolExecutionError(
                reason="validation_failed",
                detail_reason="; ".join(errors),
                retryable=False,
            )

        # 3. 委托 Service 完成打包 + 发布
        result = await self._service.create(
            request, user_id=user_id, session_id=session_id
        )

        # 4. 返回创建结果
        return {
            "skill_id": result.skill_id,
            "version": result.version,
            "status": result.status,
            "published_at": result.published_at.isoformat(),
        }
