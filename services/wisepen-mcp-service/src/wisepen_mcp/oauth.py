from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from common.security import SecurityContextHolder
from wisepen_mcp.capabilities.capability import CAPABILITY_LABELS, EXTERNAL_CAPABILITIES
from wisepen_mcp.core.config.app_settings import settings

_CODE_KEY = "wisepen:mcp:oauth:code:"
_TOKEN_KEY = "wisepen:mcp:oauth:token:"
_REFRESH_KEY = "wisepen:mcp:oauth:refresh:"

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def build_oauth_router(redis_client) -> APIRouter:
    router = APIRouter(prefix="/oauth")

    @router.get("/authorize")
    async def authorize_get(request: Request):
        """第三方 Host 首次跳转过来展示同意页"""

        # 读 query 参数
        client_id = request.query_params.get("client_id", "")
        redirect_uri = request.query_params.get("redirect_uri", "")
        code_challenge = request.query_params.get("code_challenge", "")
        code_challenge_method = request.query_params.get("code_challenge_method", "")
        state = request.query_params.get("state", "")

        # 校验
        parsed = urlparse(redirect_uri)
        loopback = parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS
        # client_id 非空即可（DCR 签发，public client 不参与安全判定）
        # method 必须是 S256
        # code_challenge 非空
        # redirect 必须是 http + loopback host
        # state 非空
        if (not client_id or code_challenge_method != "S256"
                or not code_challenge or not loopback or not state):
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        # 校验登录态
        user_id = SecurityContextHolder.get_user_id()
        if not user_id:
            return HTMLResponse("<h1>请先登录 WisePen 后再进行授权</h1>", status_code=401)

        # 渲染 consent.html
        html = _templates.get_template("consent.html").render(
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            state=state,
            capabilities=[{"key": k, "label": v} for k, v in CAPABILITY_LABELS.items()],
        )
        return HTMLResponse(html)

    @router.post("/authorize")
    async def authorize_post(request: Request):
        # 读 form 字段
        form = await request.form()
        client_id = form.get("client_id", "")
        redirect_uri = form.get("redirect_uri", "")
        code_challenge = form.get("code_challenge", "")
        code_challenge_method = form.get("code_challenge_method", "")
        state = form.get("state", "")
        # 与 EXTERNAL_CAPABILITIES 取交集，过滤掉非法值
        capabilities = [c for c in form.getlist("capability") if c in EXTERNAL_CAPABILITIES]

        parsed = urlparse(redirect_uri)
        loopback = parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS

        # capabilities 至少勾选一个能力才能授权
        if (not client_id or code_challenge_method != "S256"
                or not code_challenge or not loopback or not capabilities):
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        # 校验登录态
        user_id = SecurityContextHolder.get_user_id()
        if not user_id:
            return HTMLResponse("<h1>请先登录 WisePen 后再进行授权</h1>", status_code=401)

        # 生成 Code 并写入 Redis
        code = secrets.token_hex(16)
        await redis_client.set(
            _CODE_KEY + code,
            json.dumps({
                "userId": str(user_id),
                "clientId": client_id,
                "redirectUri": redirect_uri,
                "codeChallenge": code_challenge,
                "capabilities": capabilities,
            }),
            ex=settings.oauth.code_ttl_seconds,
        )
        sep = "&" if "?" in redirect_uri else "?"
        # 302 重定向回 redirect_uri，带上 code 和 state（原样回传，由客户端自己校验）
        return RedirectResponse(url=redirect_uri + sep + urlencode({"code": code, "state": state}), status_code=302)

    @router.post("/token")
    async def token(request: Request):
        """用 code 或 refresh_token 换 token"""
        form = await request.form()
        grant_type = form.get("grant_type", "")
        client_id = form.get("client_id", "")

        if grant_type == "authorization_code":
            # 从 Redis 读取 Code
            raw = await redis_client.get(_CODE_KEY + form.get("code", ""))
            if raw is None: # 没读到
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            data = json.loads(raw)
            # 移除一次性 Code
            await redis_client.delete(_CODE_KEY + form.get("code", ""))

            # 校验 PKCE
            digest = hashlib.sha256(form.get("code_verifier", "").encode("ascii")).digest()
            computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            if (client_id != data.get("clientId") or form.get("redirect_uri", "") != data.get("redirectUri")
                    or computed != data.get("codeChallenge")):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)

            # 通过后取 userId 和 capabilities
            user_id = data["userId"]
            capabilities = data.get("capabilities", [])

        elif grant_type == "refresh_token":
            # refresh token 每次使用即轮换
            raw = await redis_client.get(_REFRESH_KEY + form.get("refresh_token", ""))
            if raw is None:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            data = json.loads(raw)
            await redis_client.delete(_REFRESH_KEY + form.get("refresh_token", ""))
            # 校验 client_id 匹配
            if client_id != data.get("clientId"):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            user_id = data["userId"]
            capabilities = data.get("capabilities", [])
        else:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        # 下发 access_token 和 refresh_token
        access_token = secrets.token_hex(16)
        refresh_token = secrets.token_hex(16)

        await redis_client.set(
            _TOKEN_KEY + access_token,
            json.dumps({"userId": str(user_id), "capabilities": capabilities, "clientId": client_id}),
            ex=settings.oauth.access_token_ttl_seconds,
        )
        await redis_client.set(
            _REFRESH_KEY + refresh_token,
            json.dumps({"userId": str(user_id), "capabilities": capabilities, "clientId": client_id}),
            ex=settings.oauth.refresh_token_ttl_seconds,
        )
        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": settings.oauth.access_token_ttl_seconds,
            "refresh_token": refresh_token,
        })

    @router.post("/revoke")
    async def revoke(request: Request):
        form = await request.form()
        token = form.get("token", "")
        if token:
            await redis_client.delete(_TOKEN_KEY + token)
            await redis_client.delete(_REFRESH_KEY + token)
        return JSONResponse({}, status_code=200)

    @router.post("/register")
    async def register():
        """RFC 7591 动态客户端注册
        签发 client_id，客户端无需用户交互即可开始授权

        public client，client_id 不参与安全判定，也不做按 client 差异化策略，因此只签发、不落库
        authorize/token 仅要求 client_id 非空
        """
        return JSONResponse({
            "client_id": secrets.token_hex(16),
            "client_id_issued_at": int(time.time()),
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        })

    return router


__all__ = ["build_oauth_router"]
