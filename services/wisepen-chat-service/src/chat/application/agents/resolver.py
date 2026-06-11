from typing import Protocol

from common.logger import error

from chat.application.agents.default_agent import DEFAULT_AGENT_ID, build_default_agent
from chat.application.agents.models import Agent


class AgentResolver(Protocol):
    async def resolve(self, agent_id: str | None) -> Agent | None:
        ...


class DefaultAgentResolver:
    def __init__(self) -> None:
        self._default_agent = build_default_agent()

    async def resolve(self, agent_id: str | None) -> Agent | None:
        if agent_id is None or agent_id == DEFAULT_AGENT_ID:
            return self._default_agent
        return None


class CompositeAgentResolver:
    def __init__(
        self,
        *,
        primary: AgentResolver | None = None,
        fallback: AgentResolver | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or DefaultAgentResolver()

    async def resolve(self, agent_id: str | None) -> Agent | None:
        if self._primary is not None and agent_id is not None:
            try:
                agent = await self._primary.resolve(agent_id)
                if agent is not None:
                    return agent
            except Exception as e:
                error("agent primary resolver failed.", agent_id=agent_id, exc=e)

        return await self._fallback.resolve(agent_id)

