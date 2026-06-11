from chat.application.agents.models import (
    Agent,
    AgentMemoryPolicy,
    AgentModelPolicy,
    AgentSpec, AgentToolAndSkillPolicy,
)
from chat.core.config.app_settings import settings

DEFAULT_AGENT_ID = "default-chat-agent"

DEFAULT_SYSTEM_PROMPT = """
        # Role
        You are the official AI Assistant for the WisePen system. Your name is 'small W'(Chinese:'小W'). You are helpful, professional, and precise. 
        
        # Core Task
        Answer the user's queries accurately and comprehensively, relying strictly on the provided retrieved context.
        
        # Constraints & Guidelines
        1. Language Consistency: **ALWAYS respond in the exact same language as the user's prompt.** (e.g., If the user asks in Simplified Chinese, respond in Simplified Chinese; if in English, respond in English).
        2. Contextual Grounding: Base your answers ONLY on the `<retrieved_context>`. Do not introduce outside information or hallucinate facts. 
        3. Handling Unknowns: If the provided context does not contain the information needed to answer the question, clearly and politely state that you do not have enough information, rather than guessing.
        4. Tone: Maintain a professional, encouraging, and clear tone suitable for users of an advanced educational and productivity tool.
        5. Formatting: Use Markdown (e.g., bullet points, bold text, code blocks) to structure your response for maximum readability.
        """


def build_default_agent() -> Agent:
    return Agent(
        agent_id=DEFAULT_AGENT_ID,
        name="Default Chat Agent",
        description="WisePen default assistant behavior.",
        version=0,
        spec=AgentSpec(
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            agent_md=None,
            auto_generate_title=True,
            billing_group_id=None,
            model_policy=AgentModelPolicy(
                default_model_id=None,
                default_provider_id=None,
                allow_request_override=True,
            ),
            tool_and_skill_policy=AgentToolAndSkillPolicy(
                enable_use_tool=True,
                allow_tool_names=None,
                deny_tool_names=None,
                enable_use_skill=True,
                on_demand_skill_ids=None,
                force_enabled_skill_ids=None,
                skill_match_top_k=settings.SKILL_MATCH_TOP_K,
            ),
            memory_policy=AgentMemoryPolicy(
                enable_chat_memory=True,
                enable_persistence_chat_memory=True,
                enable_chat_memory_summary=True,
                high_watermark_ratio=settings.CTX_HIGH_WATERMARK_RATIO,
                low_watermark_ratio=settings.CTX_LOW_WATERMARK_RATIO,
                summary_prompt=None,
                enable_long_term_memory=True,
                long_term_memory_limit=settings.CTX_LONG_TERM_MEMORY_LIMIT,
                long_term_memory_score_threshold=settings.CTX_LONG_TERM_MEMORY_THRESHOLD,
            )
        ),
    )
