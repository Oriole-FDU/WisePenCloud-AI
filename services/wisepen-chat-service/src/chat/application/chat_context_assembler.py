import json
import base64
from dataclasses import field, dataclass
from typing import Any, Dict, List, Optional

from common.core.exceptions import ServiceException
from common.logger import error, warn

from chat.core.config.app_settings import settings
from chat.domain.entities import (
    ChatMessage,
    VisionImage,
    MessageAttachmentRef,
    Role,
    ChatSession,
    TemporaryAttachmentRef,
    ResourceAttachmentRef,
)
from chat.domain.error_codes import ChatErrorCode
from chat.domain.entities.skill import SkillMeta
from chat.core.providers import OssFileLoader
from chat.domain.repositories import MessageRepository, HotContextRepository, SessionRepository
from chat.application.frontend_state_resolver import find_opened_note_resource


_IMAGE_MEDIA_TYPES = {
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}

_IMAGE_PLACEHOLDER_TEMPLATE = "[Image attachment: {name}, attachment_id={attachment_id}]"

_CURRENT_NOTE_CITATION_PROMPT = (
    "Only when the user explicitly requests citations to the currently open WisePen note, use "
    "`[verbatim excerpt](wisepen://note/<resource_id>#p_<public_block_id>)`; "
    "if included, keep the excerpt within 30 characters; copy both IDs exactly from "
    "`read_current_note_for_edit`, and omit the link if no exact block ID is available."
)


@dataclass
class WindowedMessages:
    messages_keep: List[ChatMessage] = field(default_factory=list)
    messages_compress_candidates: List[ChatMessage] = field(default_factory=list)
    needs_compression: bool = False

    def get_messages(self) -> List[ChatMessage]:
        # 会话历史
        return self.messages_compress_candidates + self.messages_keep

class ChatContextAssembler:
    """负责短期上下文的全生命周期管理：Redis 热缓存读取与降级回填、上下文裁剪、Prompt 组装"""

    def __init__(
        self,
        message_repo: MessageRepository,
        session_repo: SessionRepository,
        hot_context_repo: HotContextRepository,
        oss_file_loader: OssFileLoader,
    ):
        self.message_repo = message_repo
        self.session_repo = session_repo
        self.hot_context_repo = hot_context_repo
        self.oss_file_loader = oss_file_loader

    async def get_chat_history_record_messages(self, session_id: str) -> List[ChatMessage]:
        """
        从 Redis 拉取短期上下文
        若返回空列表（缓存过期或异常），则从 MongoDB 回填最近 N 条记录，重建热缓存
        若会话有历史摘要，只拉取摘要时间戳之后的未压缩明细，避免已压缩历史重复注入
        """
        try:
            recent_messages = await self.hot_context_repo.get_recent_context(session_id)
        except Exception as e:
            warn("get chat history record messages from read redis hot-context failed.", session_id=session_id, exc=e)
            recent_messages = []

        if not recent_messages:
            try:
                session: Optional[ChatSession] = await self.session_repo.get_session(session_id)

                history = await self.message_repo.list_session_messages(
                    session_id=session_id,
                    after=session.summary_updated_at,
                    limit=settings.CTX_FALLBACK_HISTORY_LIMIT,
                )

                if history:
                    await self.hot_context_repo.load_messages(session_id, history)
                    return history
            except Exception as e:
                error("chat history record messages repopulate failed.", session_id=session_id, exc=e)

        return recent_messages

    async def get_session_summary(self, session_id: str) -> Optional[str]:
        """从 MongoDB 读取当前会话的摘要（如有）"""
        try:
            session: Optional[ChatSession] = await self.session_repo.get_session(session_id)
            return session.current_summary if session else None
        except Exception:
            return None

    async def build_windowed_messages(
        self,
        chat_history_record_messages: List[ChatMessage],
        prompt_budget_tokens: int,
        high_watermark_ratio: Optional[float] = None,
        low_watermark_ratio: Optional[float] = None,
    ) -> WindowedMessages:
        """
        从后往前累加Token，构建不超过高水位预算的动态滑动窗口。若超过高水位，则触发摘要。
        """
        high_ratio = high_watermark_ratio or settings.CTX_HIGH_WATERMARK_RATIO
        low_ratio = low_watermark_ratio or settings.CTX_LOW_WATERMARK_RATIO
        high_budget = int(prompt_budget_tokens * high_ratio)
        low_budget = int(prompt_budget_tokens * low_ratio)

        contents_token_count = 0

        windowed_messages = WindowedMessages()

        for msg in reversed(chat_history_record_messages):
            contents_token_count += msg.content_token_count or 0
            if contents_token_count <= low_budget:
                windowed_messages.messages_keep.insert(0, msg)  # 保留在 messages_keep
            else:
                windowed_messages.messages_compress_candidates.insert(0, msg)  # 超出低水位，进入 messages_compress_candidates

        # 当整体 Token 超过高水位时，触发需要压缩的标志
        windowed_messages.needs_compression = contents_token_count >= high_budget # 由于高水位线（如 80%）预留了安全 Buffer，即便把它们全发给模型，也不会触发 Token 溢出报错

        return windowed_messages

    def build_message_attachment_refs(
        self,
        temp_attachments: List[TemporaryAttachmentRef],
        resource_attachments: List[ResourceAttachmentRef],
        user_defined_attachment_ids: Optional[List[str]],
    ) -> List[MessageAttachmentRef]:
        """
        构建当前对话的附件摘要
        """

        # 当前对话没有附件（上传或提及的）
        if not user_defined_attachment_ids: return []

        refs: List[MessageAttachmentRef] = []

        # 遍历 temp_attachments
        for ref in temp_attachments:
            if ref.attachment_id not in set(user_defined_attachment_ids):
                continue # 跳过不是当前对话上传或提及的
            refs.append(MessageAttachmentRef(
                attachment_id=ref.attachment_id,
                kind="temporary",
                name=ref.attachment_name,
                extension=ref.extension.lower(),
                file_size=ref.file_size,
                mime_type=ref.mime_type,
                is_image=ref.extension.lower() in _IMAGE_MEDIA_TYPES,
            ))

        # 遍历 resource_attachments
        for ref in resource_attachments:
            if ref.attachment_id not in set(user_defined_attachment_ids):
                continue # 跳过不是当前对话上传或提及的
            refs.append(MessageAttachmentRef(
                attachment_id=ref.attachment_id,
                kind="resource",
                name=ref.attachment_name,
                is_image=False, # 图片暂时不是一种内部资源
            ))

        return refs

    async def _load_images(
        self, temp_attachments: List[TemporaryAttachmentRef],
        support_vision: bool, lenient_load: bool, current_count: int = 0, current_total_bytes: int = 0
    ) -> List[tuple[str, VisionImage]]:
        """
        加载附件中的图片
        """

        image_refs: List[tuple[TemporaryAttachmentRef, str]] = []

        for ref in temp_attachments:
            media_type = _IMAGE_MEDIA_TYPES.get(ref.extension.lower())
            if media_type:
                image_refs.append((ref, media_type))

        if not image_refs: return []

        if not support_vision:
            if lenient_load: return [] # 宽容模式下不报错
            raise ServiceException(ChatErrorCode.MODEL_VISION_UNSUPPORTED)

        remaining_count = settings.VISION_MAX_IMAGE_COUNT - current_count
        if len(image_refs) > remaining_count:
            if lenient_load: image_refs = image_refs[:max(0, remaining_count)] # 宽容模式下可加载到最大限制数量
            else: raise ServiceException(ChatErrorCode.IMAGE_INPUT_INVALID, custom_msg="too many images in one query")

        if not image_refs: return []

        if any(ref.file_size > settings.VISION_MAX_IMAGE_BYTES for ref, _ in image_refs):
            if lenient_load: # 宽容模式下加载符合最大尺寸限制的单图
                image_refs = [(ref, media_type) for ref, media_type in image_refs if ref.file_size <= settings.VISION_MAX_IMAGE_BYTES]
            else: raise ServiceException(ChatErrorCode.IMAGE_INPUT_INVALID, custom_msg="an image exceeds the size limit")

        if current_total_bytes + sum(ref.file_size for ref, _ in image_refs) > settings.VISION_MAX_TOTAL_IMAGE_BYTES and not lenient_load:
            raise ServiceException(ChatErrorCode.IMAGE_INPUT_INVALID, custom_msg="total image size exceeds the limit")

        images: List[tuple[str, VisionImage]] = []
        for ref, media_type in image_refs:
            try:
                raw = await self.oss_file_loader.load_by_object_key(ref.object_key)
            except Exception as exc:
                if lenient_load: continue # 宽容模式下跳过
                raise ServiceException(ChatErrorCode.IMAGE_INPUT_INVALID, custom_msg=f"failed to load image attachment {ref.attachment_id}",) from exc

            images.append((ref.attachment_id, VisionImage(media_type=media_type, base64_data=base64.b64encode(raw).decode("ascii"),)))
        return images

    async def _prepare_history_messages(
        self,
        history_messages: List[ChatMessage],
        temp_attachments: List[TemporaryAttachmentRef],
        support_vision: bool,
        current_image_count: int,
        current_total_bytes: int,
    ) -> List[ChatMessage]:
        """
        找出历史里带图片的用户消息，在预算允许时自动把最近几轮的历史图片加载回上下文
        对未自动加载的历史图片，补文本占位保留引用痕迹
        """

        prepared = [message.model_copy(deep=True) for message in history_messages] # 深拷贝历史消息
        # 计算当前轮已经占掉多少图片名额和字节预算
        temp_by_id = {ref.attachment_id: ref for ref in temp_attachments}

        # 挑选带图片附件的用户消息
        image_user_messages = [
            message for message in prepared
            if message.role == Role.USER and any(attachment.is_image for attachment in message.attachments)
        ]
        auto_load_ids: set[str] = set()

        used_count = current_image_count
        used_bytes = current_total_bytes
        history_turn_limit = max(0, settings.VISION_HISTORY_IMAGE_TURN_LIMIT)

        if settings.VISION_ENABLE_HISTORY_IMAGE_AUTO_LOAD and support_vision and history_turn_limit > 0:
            recent_user_messages = image_user_messages[-history_turn_limit:]  # 只看最近 N 轮
            for message in reversed(recent_user_messages):
                if used_count >= settings.VISION_MAX_IMAGE_COUNT:
                    break
                if used_bytes >= settings.VISION_MAX_TOTAL_IMAGE_BYTES:
                    break
                candidate_refs = []
                for attachment in message.attachments:
                    if not attachment.is_image: continue # 非图片附件，跳过
                    ref = temp_by_id.get(attachment.attachment_id)
                    if ref is None : continue # 确保仍在会话的附件中，且不超过图片比特限制
                    candidate_refs.append(ref)

                images = await self._load_images(
                    candidate_refs,
                    support_vision=True,
                    lenient_load=True,
                    current_count=used_count,
                    current_total_bytes=used_bytes,
                ) # 宽容模式批量加载

                if not images: continue

                message.imgs.extend(image for _, image in images)
                auto_load_ids.update(attachment_id for attachment_id, _ in images)

                used_count += len(images)
                used_bytes += sum(
                    temp_by_id[attachment_id].file_size or 0
                    for attachment_id, _ in images if attachment_id in temp_by_id
                )

        for message in prepared:
            # 为没加载的图片补文字占位
            excluded_ids = {
                attachment.attachment_id
                for attachment in message.attachments
                if attachment.is_image and attachment.attachment_id not in auto_load_ids
            }
            placeholders = [
                _IMAGE_PLACEHOLDER_TEMPLATE.format(name=attachment.name, attachment_id=attachment.attachment_id)
                for attachment in message.attachments
                if attachment.is_image and attachment.attachment_id in excluded_ids
            ]
            if not placeholders: continue
            suffix = "\n".join(placeholders)
            message.content = f"{message.content or ''}\n\n{suffix}".strip()

        return prepared

    async def assemble_prompt(
        self,
        session_id: str,
        user_query: str,
        system_prompt: str,
        session_summary: Optional[str],
        history_messages: List[ChatMessage],
        relevant_facts: List[str],
        frontend_states: Optional[List[Dict[str, Any]]] = None,
        available_skills: Optional[List[SkillMeta]] = None,
        temp_attachments: Optional[List[TemporaryAttachmentRef]] = None,
        resource_attachments: Optional[List[ResourceAttachmentRef]] = None,
        user_defined_attachment_ids: Optional[List[str]] = None,
        support_vision: bool = False,
    ) -> List[ChatMessage]:
        """组装最终发往 LLM 的消息列表"""
        temp_attachments = temp_attachments or []
        resource_attachments = resource_attachments or []
        user_defined_attachment_ids = user_defined_attachment_ids or []
        user_defined_image_refs = [
            ref for ref in temp_attachments
            if ref.attachment_id in set(user_defined_attachment_ids)
            and _IMAGE_MEDIA_TYPES.get(ref.extension.lower())
        ]
        # 加载当前会话的图片
        query_images = [image for _, image in await self._load_images(
            user_defined_image_refs,
            support_vision=support_vision,
            lenient_load=False,
        )]

        # 当前笔记的引用协议是应用级规则，对默认和自定义 Agent 统一生效。
        if find_opened_note_resource(frontend_states) is not None:
            system_prompt = f"{system_prompt.rstrip()}\n\n{_CURRENT_NOTE_CITATION_PROMPT}"

        # Message 列表初始化并加入 System Prompt
        messages: List[ChatMessage] = [
            ChatMessage(session_id=session_id, role=Role.SYSTEM, content=system_prompt)
        ]

        # 如果有摘要，将其注入为 user 消息，位于明细上下文之前
        # 若禁用摘要则无 session_summary
        if session_summary:
            messages.append(ChatMessage(
                session_id=session_id,
                role=Role.USER,
                content=f"[Conversation Summary so far]:\n{session_summary}",
            ))

        # 追加近期对话明细
        history_messages = await self._prepare_history_messages(
            history_messages=history_messages,
            temp_attachments=temp_attachments,
            support_vision=support_vision,
            current_image_count=len(query_images),
            current_total_bytes=sum(ref.file_size for ref in user_defined_image_refs),
        ) # 处理历史消息中的图片
        messages.extend(history_messages)

        # -- 以上消息在多轮对话中保持公共前缀，可命中缓存 --

        # 上下文块组装
        context_blocks: dict = {}
        # 如果有从 Mem0 召回的相关事实，作为补充信息拼接到 System Prompt 中
        # 若禁用长期记忆则无 relevant_facts
        if relevant_facts:
            context_blocks['relevant_user_memories'] = relevant_facts

        # Skill 提示
        # 披露轻量 metadata，由 LLM 判断是否需要加载完整 SKILL.md
        if available_skills:
            context_blocks['available_skills'] = {
                'note': "The following skills are available in this turn as lightweight metadata. "
                        "Each skill contains detailed domain instructions in SKILL.md and may include supporting assets.\n"
                        "Strict rules:\n"
                        "1. If the user explicitly asks to use one of the listed skills by id or name, call `load_skill` for that skill.\n"
                        "2. Otherwise, call `load_skill` only when a listed skill is directly useful for the current request. Do not load speculatively.\n"
                        "3. To load a skill, call `load_skill` with `skill_id` exactly as listed below.\n"
                        "4. After loading, the returned SKILL.md is mandatory for the current task. Follow its Scope, Output Format, and Constraints precisely.\n"
                        "5. Call `load_skill_asset` only after loading a skill, and only if the loaded SKILL.md explicitly requires a listed asset.\n"
                        "6. If none of the skills apply, ignore this list and answer normally.\n\n",
                'skills': [{
                    'id': skill.skill_id,
                    'name': skill.name,
                    'description': skill.description,
                } for skill in available_skills]
            }

        # 前端上下文
        # 从 states 里筛选出 没有被禁用、并且 有 value 值 的元素
        active_frontend_states = [state for state in (frontend_states or []) if not state.get("disabled", False) and state.get("value")]
        if active_frontend_states: # 若存在这样的元素
            context_blocks['user_frontend_context'] = {}
            for state in active_frontend_states:
                context_blocks['user_frontend_context'][state["key"]] = state["value"]

        # 附件
        temp_attachments_context = [{
            'attachment_id': temp_attachment.attachment_id,
            'name': temp_attachment.attachment_name,
            'extension': temp_attachment.extension,
            'size': temp_attachment.file_size // 1024,
        } for temp_attachment in temp_attachments]
        resource_attachments_context = [{
            'attachment_id': resource_attachment.attachment_id,
            'name': resource_attachment.attachment_name,
            'resource_type': resource_attachment.resource_type,
        } for resource_attachment in resource_attachments]
        if temp_attachments or resource_attachments or user_defined_attachment_ids:
            context_blocks["session_attachments"] = {
                "temporary_attachments": temp_attachments_context,
                "resource_attachments": resource_attachments_context,
                "user_query_attachment_ids": user_defined_attachment_ids or [],
            }

        context_text = json.dumps(context_blocks, ensure_ascii=False, indent=2, default=str)

        # 用户最新输入的问题
        if context_blocks.keys():
            final_user_content = (
                    "[Application-provided context]\n"
                    "The following context is provided by the application. "
                    "Use it as background information, but the user's actual request is in <user_query>.\n\n"
                    + f"<application_context>\n{context_text}\n</application_context>\n\n"
                    + f"<user_query>\n{user_query}\n</user_query>"
            )
        else:
            final_user_content = user_query

        # 该 Message 不持久化
        messages.append(ChatMessage(
            session_id=session_id,
            role=Role.USER,
            content=final_user_content,
            imgs=query_images # 加载图片
        ))

        return messages
