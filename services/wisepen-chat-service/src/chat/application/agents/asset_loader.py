from chat.application.agents.models import Agent
from chat.domain.interfaces.file_loader import FileLoader


class AgentAssetNotFoundError(LookupError):
    pass


class AgentAssetUnavailableError(ValueError):
    pass


# 预留：等待 Agent 资产按需读取能力接入 Chat Tool。
class AgentAssetLoader:
    """Read a declared asset from an already resolved Agent version snapshot."""

    def __init__(self, file_loader: FileLoader) -> None:
        self._file_loader = file_loader

    async def load_by_path(self, agent: Agent, path: str) -> bytes:
        asset = next((asset for asset in agent.assets_manifest if asset.path == path), None)
        if asset is None:
            raise AgentAssetNotFoundError(f"Agent asset is not declared: {path}")
        if asset.upload_status.upper() != "AVAILABLE":
            raise AgentAssetUnavailableError(f"Agent asset is not ready: {path}")
        if not asset.object_key:
            raise AgentAssetUnavailableError(f"Agent asset has no object key: {path}")
        return await self._file_loader.load_by_object_key(asset.object_key)
