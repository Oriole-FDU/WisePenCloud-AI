from dataclasses import dataclass
from typing import Optional

from chat.application.tools.web.services.web_search.enums import ProviderMode, SearcherName


@dataclass(frozen=True, slots=True)
class UserSearchProviderConfigUpsertDTO:
    """
    用户搜索配置增量/覆盖更新强类型 DTO。
    """
    provider_mode: ProviderMode
    provider: Optional[SearcherName] = None
    encrypted_api_key: Optional[str] = None
    masked_key: Optional[str] = None
    is_valid: bool = True
