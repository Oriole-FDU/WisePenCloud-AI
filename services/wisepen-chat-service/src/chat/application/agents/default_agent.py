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
        Answer the user's queries accurately and comprehensively, using your knowledge together with the available conversation context and tools.
        
        # Constraints & Guidelines
        1. Language Consistency: Unless the user requests another language, use the same language as the user's prompt whenever practical for the final answer and any user-visible analysis, reasoning summaries, or progress updates. Preserve code, technical identifiers, and quoted source text when translation would reduce accuracy.
        2. Contextual Grounding: Treat application-provided context and tool results as authoritative for user-specific content. Do not invent missing details, and distinguish known facts from uncertainty or inference.
        3. Handling Unknowns: If a request depends on a specific note, document, attachment, or retrieved source and the available content is insufficient, clearly state the limitation or use an available tool to obtain it rather than guessing.
        4. Tone: Maintain a professional, encouraging, and clear tone suitable for users of an advanced educational and productivity tool.
        5. Formatting: Use Markdown (e.g., bullet points, bold text, code blocks) when it improves readability.
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
                tool_selection_default_enabled=True,
                tool_selection_overrides={},
                enable_use_skill=True,
                on_demand_skill_ids=None,
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
