import json
from dataclasses import field, dataclass
from typing import Any, Dict, List, Optional

from common.logger import error, warn

from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role, ChatSession, TemporaryAttachmentRef, ResourceAttachmentRef
from chat.domain.entities.skill import SkillMeta
from chat.domain.repositories import MessageRepository, HotContextRepository, SessionRepository

@dataclass(frozen=True)
class PreloadedSkillInstruction:
    skill_id: str
    name: str
    content: str

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
    ):
        self.message_repo = message_repo
        self.session_repo = session_repo
        self.hot_context_repo = hot_context_repo

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

    def assemble_prompt(
        self,
        session_id: str,
        user_query: str,
        system_prompt: str,
        session_summary: Optional[str],
        history_messages: List[ChatMessage],
        relevant_facts: List[str],
        frontend_states: Optional[List[Dict[str, Any]]] = None,
        available_skills: Optional[List[SkillMeta]] = None,
        preloaded_skill_instructions: Optional[List[PreloadedSkillInstruction]] = None,
        temp_attachments: Optional[List[TemporaryAttachmentRef]] = None,
        resource_attachments: Optional[List[ResourceAttachmentRef]] = None,
        user_defined_attachment_ids: Optional[List[str]] = None,
    ) -> List[ChatMessage]:
        """组装最终发往 LLM 的消息列表"""

        # Message 列表初始化并加入 System Prompt
        system_content = system_prompt
        if preloaded_skill_instructions:
            system_content = (
                f"{system_prompt.rstrip()}\n\n"
                f"{self._format_preloaded_skill_instructions(preloaded_skill_instructions)}"
            )
        messages: List[ChatMessage] = [
            ChatMessage(session_id=session_id, role=Role.SYSTEM, content=system_content)
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
        preloaded_skill_ids = {
            item.skill_id for item in (preloaded_skill_instructions or []) if item.skill_id
        }
        loadable_skills = [
            skill for skill in (available_skills or [])
            if skill.skill_id not in preloaded_skill_ids
        ]
        if loadable_skills:
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
                } for skill in loadable_skills]
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
        } for temp_attachment in (temp_attachments or [])]
        resource_attachments_context = [{
            'attachment_id': resource_attachment.attachment_id,
            'name': resource_attachment.attachment_name,
            'resource_type': resource_attachment.resource_type,
        } for resource_attachment in (resource_attachments or [])]
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
        ))

        return messages

    @staticmethod
    def _format_preloaded_skill_instructions(
        preloaded_skill_instructions: List[PreloadedSkillInstruction],
    ) -> str:
        lines = [
            "[System-managed skills]",
            "The following skills are already loaded by the application for this turn.",
            "Follow these instructions when they are relevant, and do not call `load_skill` for these skill ids.",
        ]
        for skill in preloaded_skill_instructions:
            lines.extend(
                [
                    "",
                    f"<loaded_skill id=\"{skill.skill_id}\" name=\"{skill.name}\">",
                    skill.content.rstrip(),
                    "</loaded_skill>",
                ]
            )
        return "\n".join(lines)


