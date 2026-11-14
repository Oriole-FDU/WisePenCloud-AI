import base64
from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolOutput,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolSelectionMode,
    ToolUISpec,
)
from chat.core.config.app_settings import settings
from chat.core.providers import OssFileLoader
from chat.domain.entities import VisionImage, TemporaryAttachmentRef


_IMAGE_MEDIA_TYPES = {
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


class LoadImageAttachmentTool:
    def __init__(self, file_loader: OssFileLoader) -> None:
        self._file_loader = file_loader
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="load_image_attachment",
                description=(
                    "Load a previously sent image attachment by attachment_id when the user asks "
                    "about an older image that is only represented by an image attachment placeholder."
                ),
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "attachment_id": {
                            "type": "string",
                            "description": "The attachment_id from an [Image attachment: ...] placeholder.",
                        },
                    },
                    "required": ["attachment_id"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                selection_mode=ToolSelectionMode.CONTEXTUAL,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id", "temporary_attachment_refs"),
                timeout_seconds=10.0,
                max_output_chars=settings.tools.result_max_chars,
            ),
            ui_spec=ToolUISpec(
                display_name="加载会话历史图片",
                description="在需要查看会话历史中被压缩的图片时加载图片文件。不推荐禁用。",
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolOutput:
        attachment_id = str(kwargs.get("attachment_id") or "").strip()
        if not attachment_id:
            raise ToolExecutionError(
                reason="missing_attachment_id",
                detail_reason="Missing required argument: attachment_id.",
            )

        refs = context.get("temporary_attachment_refs") or []
        ref = None
        for item in refs:
            if isinstance(item, TemporaryAttachmentRef): # 从工具上下文里找会话附件
                candidate = item
            elif isinstance(item, dict):
                candidate = TemporaryAttachmentRef(**item)
            else:
                continue
            if candidate.attachment_id == attachment_id and not candidate.deleted: # 校验附件属于当前会话且没删除
                ref = candidate
                break
        if ref is None:
            raise ToolExecutionError(
                reason="image_attachment_not_found",
                detail_reason=f"Image attachment '{attachment_id}' is not available in this session.",
            )

        media_type = _IMAGE_MEDIA_TYPES.get(ref.extension.lower())
        if media_type is None:
            raise ToolExecutionError(
                reason="attachment_is_not_image",
                detail_reason=f"Attachment '{attachment_id}' is not a supported image.",
            )
        if ref.file_size > settings.vision.max_image_bytes:
            raise ToolExecutionError(
                reason="image_too_large",
                detail_reason=f"Image attachment '{attachment_id}' exceeds the per-image size limit.",
            )

        raw = await self._file_loader.load_by_object_key(ref.object_key)
        if not raw or len(raw) > settings.vision.max_image_bytes:
            raise ToolExecutionError(
                reason="image_load_invalid",
                detail_reason=f"Image attachment '{attachment_id}' could not be loaded within size limits.",
            )

        return ToolOutput(
            content=f"[Loaded image attachment: {ref.attachment_name}, attachment_id={ref.attachment_id}]",
            images=[
                VisionImage(
                    media_type=media_type,
                    base64_data=base64.b64encode(raw).decode("ascii"),
                )
            ],
        )
