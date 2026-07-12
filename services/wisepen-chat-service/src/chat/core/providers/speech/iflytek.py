import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any, Mapping
from urllib.parse import quote, urlencode

from chat.core.config.app_settings import IflytekSpeechConfig
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces import SpeechCredential, SpeechProvider
from common.core.exceptions import ServiceException


_IFLYTEK_HOST = "iat-api.xfyun.cn"
_IFLYTEK_PATH = "/v2/iat"
_IFLYTEK_SCHEME = "wss"
_CREDENTIAL_TTL_SECONDS = 240


class IflytekSpeechProvider(SpeechProvider):
    def __init__(self, config: IflytekSpeechConfig | None) -> None:
        self._config = config

    def get_recognition_credential(self, options: Mapping[str, Any]) -> SpeechCredential:
        if self._config is None:
            raise ServiceException(ChatErrorCode.SPEECH_PROVIDER_NOT_CONFIGURED)

        now = datetime.now(timezone.utc)
        date = format_datetime(now, usegmt=True)

        signature_origin = "\n".join([
            f"host: {_IFLYTEK_HOST}",
            f"date: {date}",
            f"GET {_IFLYTEK_PATH} HTTP/1.1",
        ])
        signature = base64.b64encode(
            hmac.new(
                self._config.API_SECRET.encode("utf-8"),
                signature_origin.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        authorization_origin = (
            f'api_key="{self._config.API_KEY}", '
            'algorithm="hmac-sha256", '
            'headers="host date request-line", '
            f'signature="{signature}"'
        )
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")

        query = urlencode(
            {
                "authorization": authorization,
                "date": date,
                "host": _IFLYTEK_HOST,
            },
            quote_via=quote,
        )

        return SpeechCredential(
            provider="IFLYTEK",
            expires_at=now + timedelta(seconds=_CREDENTIAL_TTL_SECONDS),
            credential={
                "url": f"{_IFLYTEK_SCHEME}://{_IFLYTEK_HOST}{_IFLYTEK_PATH}?{query}",
                "common": {"app_id": self._config.APP_ID},
            },
        )
