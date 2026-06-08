import httpx

from chat.application.tools.web.services.web_search.cache import SearchCache
from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.models import CustomProviderCredential
from chat.application.tools.web.services.web_search.runner.custom import CustomProviderRunner


class SearchProviderConfigValidator:
    """
    用户私有搜索源活性热验证。
    依托 CustomProviderRunner 的内聚 verify 机制，将复杂的状态机降维为布尔逻辑。
    """

    def __init__(self, client: httpx.AsyncClient, cache: SearchCache) -> None:
        self._client = client
        self._cache = cache

    async def verify(
            self,
            *,
            user_id: str,
            provider: SearcherName,
            api_key: str,
    ) -> bool:
        """
        闭环热连通性校验入口。

        :return: True 代表通道可用且密钥具备活性；False 代表凭证失效或云厂商网络发生剧烈瘫痪。
        """
        # 组装临时的凭证契约实体
        credential = CustomProviderCredential(
            provider=provider,
            api_key=api_key,
        )

        try:
            # 原地实例化最真实的业务 Runner，借用其内聚的活性探针逻辑
            runner = CustomProviderRunner(
                client=self._client,
                credential=credential,
                cache=self._cache,
                user_id=user_id,
            )
            # 闭环热连通性校验探针
            await runner.verify()
            return True

        except Exception:
            return False
