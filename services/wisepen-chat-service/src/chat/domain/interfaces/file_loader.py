from abc import ABC, abstractmethod


class FileLoader(ABC):

    @abstractmethod
    async def load_by_object_key(self, object_key: str) -> bytes:
        """按 object_key 加载资产原始字节"""
        ...