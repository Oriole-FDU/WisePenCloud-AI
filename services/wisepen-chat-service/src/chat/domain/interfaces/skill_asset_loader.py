from abc import ABC, abstractmethod


class SkillAssetLoader(ABC):
    """
    Skill 附件（references / templates / examples ...）的只读懒加载接口
    """

    @abstractmethod
    async def load_asset(self, skill_id: str, version: str, path: str) -> str:
        """
        返回指定 asset 的 UTF-8 文本正文
        文件不存在或路径非法时抛异常，由上层转降级文本
        """
        ...
