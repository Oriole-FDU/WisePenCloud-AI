"""本機 Tauri 驗收入口：真實 Chat runtime，Provider 與外部儲存完全受控。

執行：.venv/bin/python -m uvicorn tests.chat.tauri_fixture:app --host 127.0.0.1 --port 19200
只綁 loopback，不讀個人憑證，也不呼叫真實模型。Session / model / resource 列表為驗收資料。
"""

import asyncio
import json
import re
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tests.chat.support import (
    Document, EchoTool, Harness, Role, ServiceException,
    call, chat_endpoint, require_login, text,
)
from chat.api.converters.ui_message_converter import convert_to_ui_messages
from common.core.domain import R

fixture = None
requests_seen = []


async def script(request):
    if request.messages[0].content == "fixture-child":
        last_user = next(m for m in reversed(request.messages) if m.role == Role.USER)
        if last_user.metadata.get("subagent_input") or not last_user.content.startswith("slow"):
            events = text("子任務已接續指令：" + last_user.content)
        else:
            await asyncio.sleep(8)
            events = call("echo", {"value": "受控 child 仍在執行"}, f"echo-{len(request.messages)}")
        for event in events:
            yield event
        return
    last = request.messages[-1]
    if last.metadata.get("subagent_results") or last.role == Role.TOOL and last.tool_name == "subagent_wait":
        events = text("主代理已收束並整合子任務；child 的文字沒有混入父輸出。")
    elif last.role == Role.TOOL:
        events = call("subagent_wait", call_id="wait-all")
    elif "subagent_start" not in [schema["function"]["name"] for schema in request.tools]:
        events = text("舊請求相容性已確認：本輪沒有 subagents。")
    elif match := re.search(r'child_id=("(?:[^"\\]|\\.)*")', last.content):
        task = re.search(r'task=("(?:[^"\\]|\\.)*")', last.content)
        events = call("subagent_resume", {"child_id": json.loads(match[1]),
                                         "task": json.loads(task[1]) if task else "resume task"})
    else:
        events = call("subagent_start", {"task": "slow A", "instructions": "fixture-child"})
        events[0].tool_calls.extend(call(
            "subagent_start", {"task": "slow B", "instructions": "fixture-child"}, "start-B",
        )[0].tool_calls)
    for event in events:
        yield event


@asynccontextmanager
async def lifespan(app):
    global fixture
    with patch.object(Document, "get_pymongo_collection", return_value=None):
        async with Harness(script, tool=EchoTool()) as harness:
            fixture = harness
            fixture.add_session("fixture-session")
            app.dependency_overrides[require_login] = lambda: "user-1"
            yield
            fixture = None


app = FastAPI(lifespan=lifespan)
app.include_router(chat_endpoint.router, prefix="/chat")


@app.exception_handler(ServiceException)
async def service_error(_request, exc):
    return JSONResponse(status_code=400, content={"code": exc.code, "msg": exc.msg})


@app.middleware("http")
async def audit(request: Request, call_next):
    if request.method == "POST":
        body = await request.json()
        requests_seen.append({"path": request.url.path, "body": body})
    return await call_next(request)


def session_view(session_id):
    return {**fixture.sessions.sessions[session_id].model_dump(mode="json"), "id": session_id}


@app.get("/chat/session/listSessions")
async def sessions():
    items = [session_view(sid) for sid in fixture.sessions.sessions]
    return R.success(data={"list": items, "total": len(items), "total_page": 1})


@app.get("/chat/session/getSession")
async def session(session_id: str):
    return R.success(data=session_view(session_id))


@app.post("/chat/session/createSession")
async def create_session():
    session_id = f"fixture-session-{len(fixture.sessions.sessions) + 1}"
    fixture.add_session(session_id)
    return R.success(data=session_view(session_id))


@app.get("/chat/session/listHistoryMessages")
async def history(session_id: str):
    messages = [message for message in fixture.messages.messages if message.session_id == session_id]
    items = convert_to_ui_messages(messages)
    return R.success(data={"list": items, "total": len(items), "total_page": 1})


@app.get("/chat/model/listAvailableModels")
async def models():
    model = fixture.model
    return R.success(data={"system_models": [], "user_models": [{
        **model.model.model_dump(mode="json"), "id": str(model.model_id),
        "mappings": [{"model_id": str(model.model_id), "provider_id": str(model.provider_id),
                      "provider_name": "受控 Provider（不付費）", "provider_model_name": model.model_name,
                      "support_runtime_options": {}, "is_preferred": True, "is_active": True, "priority": 0}],
    }]})


@app.get("/chat/tool/listAvailableTools")
async def tools():
    return R.success(data={"tools": []})


@app.get("/resource/item/listResources")
async def resources():
    return R.success(data={"list": [], "total": 0, "totalPage": 0})


@app.get("/__test/evidence")
async def evidence():
    return {
        "requests": requests_seen,
        "runs": [run.public_view() for run in fixture.subagents.runs.values()],
        "roots": [root.model_dump(mode="json") for root in fixture.subagents.roots.values()],
        "model_requests": [{"model": str(req.model.model_id), "provider": str(req.model.provider_id),
                            "message_count": len(req.messages)} for req in fixture.provider.requests],
    }
