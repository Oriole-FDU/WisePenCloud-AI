from typing import TYPE_CHECKING, Protocol

from chat.application.agents.default_agent import DEFAULT_AGENT_ID, build_default_agent
from chat.application.agents.models import Agent
from chat.service_client import ResourceClient
from common.core.exceptions import RpcError
from common.security import SecurityContextHolder

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

    def __init__(self, ai_asset_client: "AIAssetClient", resource_client: ResourceClient) -> None:
        self._client = ai_asset_client
        self._resource_client = resource_client

    async def resolve(self, agent_id: str | None, agent_version: int | None = None) -> Agent | None:
        if not agent_id or agent_id == DEFAULT_AGENT_ID:
            return None
        if agent_version is not None and agent_version <= 0:
            return None

        if agent_version is not None and not await self._has_load_permission(agent_id, agent_version):
            return None

        try:
            agent = (
                await self._client.get_published_agent(agent_id)
                if agent_version is None
                else await self._client.get_agent_with_version(agent_id, agent_version)
            )
        except RpcError:
            return None
        if agent is None: return None
        if (agent.version <= 0
                or agent.version_status.upper() != "PUBLISHED"
                or not agent.spec.system_prompt.strip()):
            return None
        if agent_version is not None and agent.version != agent_version:
            return None
        if agent_version is None and not await self._has_load_permission(agent_id, agent.version):
            return None
        return agent

    async def _has_load_permission(self, agent_id: str, agent_version: int) -> bool:
        try:
            return await self._resource_client.has_load_permission(
                resource_id=agent_id,
                user_id=SecurityContextHolder.get_user_id(),
                group_role_map=SecurityContextHolder.get_group_role_map(),
                target_version=agent_version,
            )
        except Exception:
            return False


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
