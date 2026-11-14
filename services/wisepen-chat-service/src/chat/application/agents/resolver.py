from typing import TYPE_CHECKING, Protocol

from chat.application.agents.default_agent import DEFAULT_AGENT_ID, build_default_agent
from chat.application.agents.models import Agent
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import RpcError, ServiceException
from common.security import SecurityContextHolder

if TYPE_CHECKING:
    from chat.domain.entities import ChatSession
    from chat.service_client import AIAssetClient, ResourceClient


class AgentResolver(Protocol):
    async def resolve(self, agent_id: str | None, agent_version: int | None = None) -> Agent | None:
        ...

    async def resolve_draft(self, user_id: str, agent_id: str | None, draft_version: int | None) -> Agent | None:
        ...


async def resolve_session_agent(
    resolver: AgentResolver, session: "ChatSession", *, require_prompt: bool = True,
) -> Agent | None:
    if not session.is_draft_debug:
        return await resolver.resolve(session.agent_id, session.agent_version)
    agent = await resolver.resolve_draft(session.user_id, session.agent_id, session.agent_version)
    if agent is None or (require_prompt and not agent.spec.system_prompt.strip()):
        raise ServiceException(ChatErrorCode.AGENT_DRAFT_UNAVAILABLE)
    return agent


class DefaultAgentResolver:
    def __init__(self) -> None:
        self._default_agent = build_default_agent()

    async def resolve(self, agent_id: str | None, agent_version: int | None = None) -> Agent | None:
        if agent_id is None or agent_id == DEFAULT_AGENT_ID:
            return self._default_agent
        return None

    async def resolve_draft(self, user_id: str, agent_id: str | None, draft_version: int | None) -> None:
        return None


class RemoteAgentResolver:
    """从 Java AI Asset 服务解析自定义 Agent。"""

    def __init__(self, ai_asset_client: "AIAssetClient", resource_client: "ResourceClient") -> None:
        self._client = ai_asset_client
        self._resource_client = resource_client

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
        except RpcError:
            return None
        if agent is None: return None
        if (agent.version <= 0
                or agent.version_status.upper() != "PUBLISHED"
                or not agent.spec.system_prompt.strip()):
            return None
        return agent

    async def resolve_draft(self, user_id: str, agent_id: str | None, draft_version: int | None) -> Agent | None:
        if not agent_id or agent_id == DEFAULT_AGENT_ID or not draft_version or draft_version <= 0:
            return None
        try:
            permission = await self._resource_client.check_res_permission(
                agent_id, user_id, SecurityContextHolder.get_group_role_map(),
            )
            if permission.resource_access_role != "OWNER":
                return None
            agent = await self._client.get_agent_with_version(agent_id, draft_version)
        except RpcError:
            return None
        if (agent is None
                or agent.agent_id != agent_id
                or agent.version != draft_version
                or agent.version_status.upper() != "DRAFT"):
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

    async def resolve_draft(self, user_id: str, agent_id: str | None, draft_version: int | None) -> Agent | None:
        if self._primary is None:
            return None
        return await self._primary.resolve_draft(user_id, agent_id, draft_version)
