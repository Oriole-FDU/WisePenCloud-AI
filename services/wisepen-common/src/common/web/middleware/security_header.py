from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from common.core.constants import SecurityConstants, CommonConstants
from common.security.context import SecurityContextHolder, _security_context
from common.gray.context import GrayContextHolder, _gray_context


class SecurityHeaderMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, from_source_secret: str):
        super().__init__(app)
        self.from_source_secret = from_source_secret

    async def dispatch(self, request: Request, call_next):
        # 校验防绕过 Token (X-From-Source)
        request_source = request.headers.get(SecurityConstants.HEADER_FROM_SOURCE)
        if request_source != self.from_source_secret:
            return Response(status_code=404, content="Not Found")

        # 提取并设置安全上下文
        user_id = request.headers.get(SecurityConstants.HEADER_USER_ID)
        identity_type = request.headers.get(SecurityConstants.HEADER_IDENTITY_TYPE)
        group_role_map = request.headers.get(SecurityConstants.HEADER_GROUP_ROLE_MAP)

        if user_id:
            SecurityContextHolder.set_user_id(user_id)
            if identity_type:
                SecurityContextHolder.set_identity_type(int(identity_type))
            if group_role_map:
                SecurityContextHolder.set_group_role_map(group_role_map)

        # 提取灰度上下文
        developer = request.headers.get(CommonConstants.GRAY_HEADER_DEV_KEY)
        if developer:
            GrayContextHolder.set_developer_tag(developer)

        try:
            response = await call_next(request)
        finally:
            # 请求结束后清理上下文，防止协程复用时用户信息泄漏到下一个请求
            _security_context.set({})
            _gray_context.set("")

        return response