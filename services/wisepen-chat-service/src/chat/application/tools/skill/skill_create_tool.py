from typing import Any, Dict, List

from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error

from chat.application.tools.skill.services.skill_create.errors import SkillBundleBuildError
from chat.application.tools.skill.services.skill_create.models import (
    CreateSkillBundleRequest,
    SkillBundleAssetDraft,
    SkillBundleBuildContext,
    SkillBundleReferenceDraft,
    SkillBundleScriptDraft,
    SkillMarkdownDraft,
    SkillMarkdownExample,
    SkillWorkflowStep,
)
from chat.application.tools.skill.services.skill_create.service import SkillBundleService


_DESCRIPTION = (
    "Create a portable .skill bundle from a reusable workflow, project convention, "
    "output pattern, tool-usage process, or repeated correction captured from the current conversation. "
    "Use this tool when the user wants to turn a task process, repeated workflow, custom instruction set, "
    "project convention, or recurring way of doing work into a reusable skill.\n\n"
    "The assistant must provide structured skill content: trigger description, markdown instructions, "
    "optional references, optional assets, and optional scripts. This tool renders SKILL.md, writes bundled files, "
    "and saves the skill folder to a local path."
)

_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "skill_id": {
            "type": "string",
            "description": (
                "Skill directory name. Use kebab-case when possible, "
                "for example 'markdown-to-pptx' or 'research-note-cleanup'."
            ),
        },
        "display_name": {
            "type": "string",
            "description": "Human-readable skill name shown to users.",
        },
        "description": {
            "type": "string",
            "description": (
                "Trigger-oriented frontmatter description. Explain what the skill does and when it should be used. "
                "Include concrete contexts, phrases, or task situations where future agents should consider this skill."
            ),
        },
        "version": {
            "type": "string",
            "description": "Skill bundle version string. Defaults to '1.0.0' when omitted.",
        },
        "markdown": {
            "type": "object",
            "additionalProperties": False,
            "description": "Structured SKILL.md body. The service renders this into a consistent Markdown format.",
            "properties": {
                "purpose": {
                    "type": "string",
                    "description": "What this skill helps future agents do.",
                },
                "workflow": {
                    "type": "array",
                    "description": "Ordered workflow steps for using the skill.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "step": {
                                "type": "string",
                                "description": "Main workflow step.",
                            },
                            "sub_steps": {
                                "type": "array",
                                "description": "Optional sub-steps under this workflow step.",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["step"],
                    },
                },
                "input_requirements": {
                    "type": "array",
                    "description": "What inputs the user or environment should provide.",
                    "items": {"type": "string"},
                },
                "output_requirements": {
                    "type": "array",
                    "description": "Expected output format and quality requirements.",
                    "items": {"type": "string"},
                },
                "tool_guidance": {
                    "type": "array",
                    "description": "Guidance on which tools to prefer, avoid, or combine.",
                    "items": {"type": "string"},
                },
                "resource_guidance": {
                    "type": "array",
                    "description": (
                        "Guidance on when to read references, use assets, or execute scripts. "
                        "Mention important bundled file paths explicitly."
                    ),
                    "items": {"type": "string"},
                },
                "constraints": {
                    "type": "array",
                    "description": "Important boundaries, caveats, and safety or quality constraints.",
                    "items": {"type": "string"},
                },
                "examples": {
                    "type": "array",
                    "description": "Optional examples of user input and expected behavior.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Example title.",
                            },
                            "user_input": {
                                "type": "string",
                                "description": "Example user request.",
                            },
                            "expected_behavior": {
                                "type": "string",
                                "description": "How the skill should handle this request.",
                            },
                        },
                        "required": ["title", "user_input", "expected_behavior"],
                    },
                },
            },
            "required": ["purpose", "workflow", "output_requirements"],
        },
        "references": {
            "type": "array",
            "description": (
                "Optional static documentation written under references/. "
                "Use for long rules, protocols, checklists, domain knowledge, or format specs."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "description": "Path relative to references/, for example 'style-guide.md'.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short description of what this reference is for.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Reference text content.",
                    },
                },
                "required": ["relative_path", "description", "content"],
            },
        },
        "assets": {
            "type": "array",
            "description": (
                "Optional resources written under assets/. "
                "Use for templates, schemas, configs, examples, styles, or other task resources. "
                "This version accepts model-generated text assets via content_text."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "description": "Path relative to assets/, for example 'templates/report.md'.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short description of what this asset is for.",
                    },
                    "content_text": {
                        "type": "string",
                        "description": "Text content of this asset.",
                    },
                },
                "required": ["relative_path", "description", "content_text"],
            },
        },
        "scripts": {
            "type": "array",
            "description": (
                "Optional scripts written under scripts/. "
                "Execution is handled by sandbox runtime; this tool only packages them."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "description": "Path relative to scripts/, for example 'build_report.py', 'validate.js', or 'run.sh'.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short description of what this script does.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Script content.",
                    },
                },
                "required": ["relative_path", "description", "content"],
            },
        },
    },
    "required": ["skill_id", "display_name", "description", "markdown"],
}


class CreateSkillBundleTool(BaseTool):
    """
    创建 Skill Bundle。

    - Agent 负责注入结构化 Skill 内容。
    - Tool 只负责把 kwargs 解析成 dataclass，并调用 service。
    - Service 负责渲染 SKILL.md、写文件、打包 .skill。
    - 本工具不发布、不安装、不启用 Skill，也不上传 OSS。
    """

    def __init__(self, skill_bundle_service: SkillBundleService) -> None:
        self._skill_bundle_service = skill_bundle_service

    @property
    def name(self) -> str:
        return "skill_create"

    @property
    def description(self) -> str:
        return _DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA


    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        try:
            request = self._build_request(kwargs)
            build_context = SkillBundleBuildContext(
                user_id=context["user_id"],
                session_id=context["session_id"],
            )

            result = await self._skill_bundle_service.create_bundle(
                request=request,
                context=build_context,
            )
        except SkillBundleBuildError as e:
            log_error("create_skill_bundle 构建", e)
            return f"[Tool Error] Failed to create skill bundle: {e}"
        except Exception as e:
            log_error("create_skill_bundle 执行", e)
            return f"[Tool Error] Failed to create skill bundle: {type(e).__name__}: {e}"

        lines = [
            "[Created Skill Bundle]",
            f"skill_id={result.skill_id}",
            f"display_name={result.display_name}",
            f"version={result.version}",
            f"bundle_dir_ref={result.bundle_dir_ref}",
            "",
            "This bundle has not been installed, published, or enabled.",
        ]

        if result.files:
            lines.append("")
            lines.append("Files:")
            for file in result.files:
                lines.append(
                    f"- {file.path} ({file.size_bytes} bytes) — {file.description}"
                )

        return "\n".join(lines)

    def _build_request(self, kwargs: Dict[str, Any]) -> CreateSkillBundleRequest:
        markdown_data = kwargs["markdown"]

        markdown = SkillMarkdownDraft(
            purpose=markdown_data["purpose"],
            workflow=[
                SkillWorkflowStep(
                    step=item["step"],
                    sub_steps=item.get("sub_steps") or [],
                )
                for item in markdown_data["workflow"]
            ],
            input_requirements=markdown_data.get("input_requirements") or [],
            output_requirements=markdown_data["output_requirements"],
            tool_guidance=markdown_data.get("tool_guidance") or [],
            resource_guidance=markdown_data.get("resource_guidance") or [],
            constraints=markdown_data.get("constraints") or [],
            examples=[
                SkillMarkdownExample(
                    title=item["title"],
                    user_input=item["user_input"],
                    expected_behavior=item["expected_behavior"],
                )
                for item in markdown_data.get("examples") or []
            ],
        )

        return CreateSkillBundleRequest(
            skill_id=kwargs["skill_id"],
            display_name=kwargs["display_name"],
            description=kwargs["description"],
            version=kwargs.get("version", "1.0.0"),
            markdown=markdown,
            references=[
                SkillBundleReferenceDraft(
                    relative_path=item["relative_path"],
                    description=item["description"],
                    content=item["content"],
                )
                for item in kwargs.get("references") or []
            ],
            assets=[
                SkillBundleAssetDraft(
                    relative_path=item["relative_path"],
                    description=item["description"],
                    content_text=item["content_text"],
                )
                for item in kwargs.get("assets") or []
            ],
            scripts=[
                SkillBundleScriptDraft(
                    relative_path=item["relative_path"],
                    description=item["description"],
                    content=item["content"],
                )
                for item in kwargs.get("scripts") or []
            ],
        )
