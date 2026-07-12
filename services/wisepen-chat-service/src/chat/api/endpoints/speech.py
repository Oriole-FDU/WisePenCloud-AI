from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Body, Depends

from chat.api.schemas.speech import GetSpeechCredentialRequest, SpeechCredentialResponse
from chat.container import Container
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces import SpeechProvider
from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import require_login


router = APIRouter()


@router.post(
    "/getCredential",
    response_model=R[SpeechCredentialResponse],
    status_code=200,
    summary="获取语音识别凭证",
    description="""
- 用途：为前端直连语音识别 Provider 申请临时 WebSocket 鉴权信息。
- 请求：provider 指定语音识别 Provider；options 承载 Provider 自定义请求参数；请求体可省略，默认使用 IFLYTEK。
- 约束：当前用户必须已登录；当前服务仅支持 IFLYTEK；服务端必须已配置讯飞语音识别密钥。
- 处理：根据服务端讯飞 APIKey/APISecret 签发短期 WebSocket URL，并通过 credential 返回 Provider 自定义参数；不代理音频流，不保存录音，不调用识别接口。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；语音识别 Provider 不支持 -> ChatErrorCode.SPEECH_PROVIDER_UNSUPPORTED；讯飞配置缺失 -> ChatErrorCode.SPEECH_PROVIDER_NOT_CONFIGURED；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回当前 Provider、建议失效时间和 Provider 自定义 credential。
""",
)
@inject
async def get_speech_credential(
    req: GetSpeechCredentialRequest | None = Body(default=None),
    _user_id: str = Depends(require_login),
    iflytek_provider: SpeechProvider = Depends(
        Provide[Container.iflytek_speech_provider]
    ),
):
    req = req or GetSpeechCredentialRequest()
    if req.provider == "IFLYTEK":
        speech_provider = iflytek_provider
    else:
        raise ServiceException(ChatErrorCode.SPEECH_PROVIDER_UNSUPPORTED)

    credential = speech_provider.get_recognition_credential(options=req.options)

    return R.success(data=SpeechCredentialResponse(
        provider=credential.provider,
        expires_at=credential.expires_at,
        credential=credential.credential,
    ))
