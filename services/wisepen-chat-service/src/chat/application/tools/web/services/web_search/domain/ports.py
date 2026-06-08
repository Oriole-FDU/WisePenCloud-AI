from abc import ABC, abstractmethod

from chat.application.tools.web.services.web_search.dtos import UserSearchProviderConfigUpsertDTO


class BaseSearchProviderConfigRepository(ABC):
    """
    用户联网搜索通道配置仓储抽象基类。
    """

    @abstractmethod
    async def get_by_user_id(self, user_id: str):
        """
        根据全局唯一用户 ID 打捞用户的搜索源通道配置实体。

        :param user_id: 唯一用户 ID
        :return: 对应的配置实体，若从未配置过则返回 None
        """
        pass

    @abstractmethod
    async def upsert(
            self,
            *,
            user_id: str,
            dto: UserSearchProviderConfigUpsertDTO,
    ):
        """
        高内聚的原子化 Upsert。

        若该用户的配置不存在则自动执行就地初始化（附带标准的 Timezone-aware 时区时间）；
        若已存在则根据 DTO 约束的强类型字段进行安全覆盖，并刚性刷新更新时间。

        :param user_id: 唯一用户 ID
        :param dto: 经过强类型规整后的更新载荷实体
        :return: 处于最新状态的持久化实体对象
        """
        pass
