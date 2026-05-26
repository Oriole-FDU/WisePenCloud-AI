import re
from typing import Any, Dict, List, Optional, Tuple
from common.logger import log_fail, log_error

from chat.core.config.app_settings import settings
from chat.application.skill_prompt_builder import SkillPromptBuilder
from chat.domain.entities import ChatMessage, Role, ChatSession
from chat.domain.entities.skill import SkillMeta
from chat.domain.message_lifecycle import MessageLifecycle, PersistenceMode
from chat.domain.repositories import MessageRepository, HotContextRepository, SessionRepository, SkillRepository


_SKILL_LOADED_ACK_RE = re.compile(
    r"^\[Skill loaded:\s*(?P<skill_id>[^\s\]]+)\s+version=(?P<version>[^\]]+)\]$"
)


class ChatContextAssembler:
    """负责短期上下文的全生命周期管理：Redis 热缓存读取与降级回填、上下文裁剪、Prompt 组装"""

    def __init__(
        self,
        message_repo: MessageRepository,
        session_repo: SessionRepository,
        hot_context_repo: HotContextRepository,
        skill_repo: SkillRepository,
    ):
        self.message_repo = message_repo
        self.session_repo = session_repo
        self.hot_context_repo = hot_context_repo
        self.skill_repo = skill_repo

    async def get_or_repopulate_hot_context(self, session_id: str) -> List[ChatMessage]:
        """
        从 Redis 拉取短期上下文
        若返回空列表（缓存过期或异常），则从 MongoDB 回填最近 N 条记录，重建热缓存
        若会话有历史摘要，只拉取摘要时间戳之后的未压缩明细，避免已压缩历史重复注入
        """
        try:
            recent_messages = await self.hot_context_repo.get_recent_context(session_id)
        except Exception as e:
            log_fail("Redis 上下文读取", e, session=session_id)
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
                log_error("Redis 上下文回填", e, session=session_id)

        return recent_messages

    async def get_session_summary(self, session_id: str) -> Optional[str]:
        """从 MongoDB 读取当前会话的摘要（如有）"""
        try:
            session: Optional[ChatSession] = await self.session_repo.get_session(session_id)
            return session.current_summary if session else None
        except Exception:
            return None

    async def build_context_window(
        self,
        messages: List[ChatMessage],
        prompt_budget_tokens: int,
    ) -> Tuple[List[ChatMessage], List[ChatMessage], bool]:
        """
        从后往前累加Token，构建不超过高水位预算的动态滑动窗口。若超过高水位，则触发摘要。
        """
        high_budget = int(prompt_budget_tokens * settings.CTX_HIGH_WATERMARK_RATIO)
        low_budget = int(prompt_budget_tokens * settings.CTX_LOW_WATERMARK_RATIO)

        total_token = 0

        messages_compress_candidates: List[ChatMessage] = []
        messages_keep: List[ChatMessage] = []

        for msg in reversed(messages):
            total_token += msg.token_count or 0
            if total_token <= low_budget:
                messages_keep.insert(0, msg)  # 保留在 messages_keep
            else:
                messages_compress_candidates.insert(0, msg)  # 超出低水位，进入 messages_compress_candidates

        # 当整体 Token 超过高水位时，触发需要压缩的标志
        needs_compression = total_token >= high_budget # 由于高水位线（如 80%）预留了安全 Buffer，即便把它们全发给模型，也不会触发 Token 溢出报错

        return messages_keep, messages_compress_candidates, needs_compression

    @staticmethod
    def _extract_restorable_skill(msg: ChatMessage) -> Optional[Tuple[str, Optional[str]]]:
        if msg.role != Role.TOOL:
            return None

        restore_ref = (msg.metadata or {}).get("restore_ref") or {}
        if restore_ref.get("kind") == "skill":
            data = restore_ref.get("data") or {}
            skill_id = data.get("skill_id")
            version = data.get("version")
            if skill_id:
                return str(skill_id), str(version) if version else None

        # 兼容旧数据：历史记录可能没有 metadata，甚至 name 写错，但保留了 load_skill ack。
        content = (msg.content or "").strip()
        match = _SKILL_LOADED_ACK_RE.match(content)
        if match:
            return match.group("skill_id"), match.group("version")

        return None

    async def restore_context_injections(
        self,
        session_id: str,
        windowed_messages: List[ChatMessage],
    ) -> List[ChatMessage]:
        """
        持久化历史只保存 load_skill 的短 ack，不保存 SKILL.md 正文。
        每次组装上下文时，遇到需要恢复的 TOOL ack，就在其后补回正式 load_skill 同款 USER 注入。
        """
        restored: List[ChatMessage] = []
        for msg in windowed_messages:
            restored.append(msg)
            skill_ref = self._extract_restorable_skill(msg)
            if not skill_ref:
                continue

            skill_id, expected_version = skill_ref
            try:
                skill = await self.skill_repo.get_published_skill(skill_id)
            except Exception as e:
                log_error("恢复 Skill 上下文", e, session=session_id, skill_id=skill_id)
                continue
            if skill is None:
                log_fail("恢复 Skill 上下文", "skill 不存在", session=session_id, skill_id=skill_id)
                continue
            if expected_version and skill.version != expected_version:
                log_fail(
                    "恢复 Skill 上下文",
                    "skill 版本与历史 ack 不一致，使用当前发布版本恢复",
                    session=session_id,
                    skill_id=skill_id,
                    expected_version=expected_version,
                    actual_version=skill.version,
                )

            restored.append(ChatMessage(
                session_id=session_id,
                role=Role.USER,
                content=SkillPromptBuilder.build_loaded_skill_injection(skill),
                metadata={
                    "restored_from_tool_call_id": msg.tool_call_id,
                    "restored_skill_id": skill.skill_id,
                    "restored_skill_version": skill.version,
                },
                lifecycle=MessageLifecycle(
                    persistence_mode=PersistenceMode.DROP,
                ),
            ))

        return restored

    def assemble_prompt(
        self,
        session_id: str,
        user_query: str,
        windowed_messages: List[ChatMessage],
        relevant_facts: List[str],
        session_summary: Optional[str],
        states: Optional[List[Dict[str, Any]]] = None,
        available_skills: Optional[List[SkillMeta]] = None,
    ) -> List[ChatMessage]:
        """组装最终发往 LLM 的消息列表。"""
        system_prompt = """
        # Role
        You are the official AI Assistant for the WisePen system. You are helpful, professional, and precise. 
        
        # Core Task
        Answer the user's queries accurately and comprehensively, relying strictly on the provided retrieved context.
        
        # Constraints & Guidelines
        1. Language Consistency: **ALWAYS respond in the exact same language as the user's prompt.** (e.g., If the user asks in Simplified Chinese, respond in Simplified Chinese; if in English, respond in English).
        2. Contextual Grounding: Base your answers ONLY on the `<retrieved_context>`. Do not introduce outside information or hallucinate facts. 
        3. Handling Unknowns: If the provided context does not contain the information needed to answer the question, clearly and politely state that you do not have enough information, rather than guessing.
        4. Tone: Maintain a professional, encouraging, and clear tone suitable for users of an advanced educational and productivity tool.
        5. Formatting: All text you output outside of tool use is displayed to the user. Output text to communicate with the user. You can use Github-flavored markdown for formatting, and will be rendered in a monospace font using the CommonMark specification. Use Markdown to structure your response for readability unless a loaded skill specifies a stricter output format. If a loaded skill specifies an Output Format or Constraints section, follow the loaded skill exactly.
        6. Formula Formatting: If your output contains formulas, format them in LaTeX and wrap them with $$.
        7. System Reminders: Messages wrapped in `<system-reminder>` are WisePen-injected operational context, not the user's request. Use them to guide the current task, but do not answer or quote them directly.
        """ # 全局指令

        # 如果有从 Mem0 召回的相关事实，作为补充信息拼接到 System Prompt 中
        if relevant_facts:
            facts_text = "\n".join([f"- {fact}" for fact in relevant_facts])
            system_prompt += f"\n[Relevant User Memories]:\n{facts_text}\n"

        messages: List[ChatMessage] = [
            ChatMessage(session_id=session_id, role=Role.SYSTEM, content=system_prompt)
        ]

        # 如果有摘要，将其注入为第二条 system 消息，位于明细上下文之前
        if session_summary:
            messages.append(ChatMessage(
                session_id=session_id,
                role=Role.SYSTEM,
                content=f"[Conversation Summary so far]:\n{session_summary}",
            ))

        # Skill 可用清单：只披露轻量 metadata，由 LLM 判断是否需要加载完整 SKILL.md。
        # 用 skill_id 作机器标识，description 给 LLM 做相关性判断
        if available_skills:
            skill_lines = [
                f"- id=\"{s.skill_id}\" name=\"{s.display_name}\": {s.description}" for s in available_skills
            ]
            skill_block = (
                "<system-reminder>\n"
                "[Available WisePen Skills]\n"
                "The following skills are available in this conversation as lightweight metadata. "
                "Each skill contains detailed domain instructions (SKILL.md) and possibly supporting assets, "
                "but the full content is not loaded yet.\n"
                "Strict rules:\n"
                "1. If the user explicitly asks to use one of the listed skills by id or name, call `load_skill` for that skill.\n"
                "2. Otherwise, decide from the user's request whether any listed skill is directly useful. "
                "Load a skill only when it is needed to fulfill the current request; do not load speculatively.\n"
                "3. To load, call the tool `load_skill` with `skill_id` exactly as listed below.\n"
                "4. After loading, follow the SKILL.md instructions precisely. "
                "Use `load_skill_asset` to open a specific reference/template only if SKILL.md explicitly says to.\n"
                "5. If none of the skills apply, ignore this list and answer normally.\n\n"
                "Skills:\n"
                + "\n".join(skill_lines)
                + "\n</system-reminder>"
            )
            messages.append(ChatMessage(
                session_id=session_id,
                role=Role.USER,
                content=skill_block,
                lifecycle=MessageLifecycle(
                    persistence_mode=PersistenceMode.DROP,
                ),
            ))

        # 经过滑动窗口裁剪后的近期对话明细
        messages.extend(windowed_messages)

        # 前端上下文注入：作为独立 SYSTEM 消息插入在历史消息和用户问题之间
        active_states = [s for s in (states or []) if not s.get("disabled", False) and s.get("value")]
        if active_states:
            ctx_lines = [f'<context key="{s["key"]}">\n{s["value"]}\n</context>' for s in active_states]
            messages.append(ChatMessage(
                session_id=session_id,
                role=Role.SYSTEM,
                content="[Frontend Context]\n" + "\n".join(ctx_lines),
            ))

        # 用户最新输入的问题
        messages.append(ChatMessage(session_id=session_id, role=Role.USER, content=user_query))
        return messages

