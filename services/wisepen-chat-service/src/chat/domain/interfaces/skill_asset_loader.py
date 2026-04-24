from abc import ABC, abstractmethod


class SkillAssetLoader(ABC):
    """
    Skill 附件的只读懒加载接口
    """

    @abstractmethod
    async def load_by_object_key(self, object_key: str) -> bytes:
        """按 OSS object_key 加载资产原始字节"""
        ...

    @abstractmethod
    async def load_asset(self, skill_id: str, version: str, path: str) -> bytes:
        """兼容保留：按 skill_id + version + 相对 path 加载资产原始字节"""
        ...
