from typing import TYPE_CHECKING, Protocol

from chat.application.agents.default_agent import DEFAULT_AGENT_ID, build_default_agent
from chat.application.agents.models import Agent
from common.core.exceptions import RpcError

if TYPE_CHECKING:
    from chat.service_client import AIAssetClient


class AgentResolver(Protocol):
    async def resolve(self, agent_id: str | None, agent_version: int | None = None) -> Agent | None:
        ...


class DefaultAgentResolver:
    def __init__(self) -> None:
        self._default_agent = build_default_agent()

    async def resolve(self, agent_id: str | None, agent_version: int | None = None) -> Agent | None:
        if agent_id is None or agent_id == DEFAULT_AGENT_ID:
            return self._default_agent
        return None


class RemoteAgentResolver:
    """从 Java AI Asset 服务解析自定义 Agent。"""

    # Java AIResourceError values for missing resource/version.
    _NOT_FOUND_CODES = {9111, 9211}

    def __init__(self, ai_asset_client: "AIAssetClient") -> None:
        self._client = ai_asset_client

    async def resolve(self, agent_id: str | None, agent_version: int | None = None) -> Agent | None:
        if not agent_id or agent_id == DEFAULT_AGENT_ID:
            return None
        if agent_version is not None and agent_version <= 0:
            return None
        try:
            agent = (
                await self._client.get_published_agent(agent_id)
                if agent_version is None
                else await self._client.get_agent_with_version(agent_id, agent_version)
            )
        except RpcError as exc:
            if exc.code in self._NOT_FOUND_CODES:
                return None
            raise
        if agent is None:
            return None
        if (
            agent.agent_id != agent_id
            or agent.version <= 0
            or agent.version_status.upper() != "PUBLISHED"
            or not agent.spec.system_prompt.strip()
        ):
            return None
        if agent_version is not None and agent.version != agent_version:
            return None
        return agent


class CompositeAgentResolver:
    def __init__(
        self,
        *,
        primary: AgentResolver | None = None,
        fallback: AgentResolver | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or DefaultAgentResolver()

    async def resolve(self, agent_id: str | None, agent_version: int | None = None) -> Agent | None:
        if agent_id is None or agent_id == DEFAULT_AGENT_ID:
            return await self._fallback.resolve(agent_id, agent_version)
        if self._primary is None:
            return None
        return await self._primary.resolve(agent_id, agent_version)
