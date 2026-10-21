"""公開 Chat 測試：僅替換 Provider、配置與外部儲存，不啟動服務。"""

import asyncio
import json
import os
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ.setdefault("NACOS_SERVER_ADDR", "127.0.0.1:1")

_CONFIG = {
    "LLM_BASE_URL": "http://127.0.0.1:1",
    "LLM_API_KEY": "dummy",
    "DEFAULT_MODEL_ID": "000000000000000000000001",
    "MEMORY_LLM_MODEL": "test",
    "MEMORY_EMBEDDING_MODEL": "test",
    "MEMORY_RERANKER_ZE_MODEL": "test",
    "ZERO_ENTROPY_API_KEY": "dummy",
    "RERANKER_MODEL": "test",
    "SUMMARY_MODEL": "test",
    "FROM_SOURCE_SECRET": "dummy",
    "KAFKA_BOOTSTRAP_SERVERS": "127.0.0.1:1",
    "REDIS_URL": "redis://127.0.0.1:1",
    "MONGODB_URL": "mongodb://127.0.0.1:1",
    "MONGODB_DB_NAME": "test",
    "QDRANT_HOST": "127.0.0.1",
    "QDRANT_PASSWORD": "dummy",
}

with patch("dotenv.find_dotenv", return_value=""), patch(
    "common.cloud.nacos_client.NacosClientManager.pull_config",
    AsyncMock(return_value=json.dumps(_CONFIG)),
):
    from chat.api.endpoints import chat as chat_endpoint
    from chat.application.agents import Agent, AgentSpec, AgentMemoryPolicy
    from chat.application.chat_turn_coordinator import ChatTurnCoordinator
    from chat.application.chat_turn_stream_manager import ChatTurnStreamManager
    from chat.application.chat_turn_tool_policy import ChatTurnToolPolicyBuilder
    from chat.application.llm_provider_resolver import LLMProviderResolver
    from chat.application.token_counter import TokenCounter
    from chat.application.tools.core import ToolRegistry
    from chat.application.subagents.tools import subagent_tools
    from chat.application.tools.core.execution.dispatcher import ToolDispatcher
    from chat.application.tools.core.output_cache.cache_manager import ToolOutputCache
    from chat.container import container
    from chat.domain.entities import ChatMessage, Role, ChatSession
    from chat.domain.entities.model import Model, ModelProviderMapping
    from chat.domain.entities.provider import Provider, ProviderType, ProviderScope
    from chat.domain.entities.message import ToolCallMessage
    from chat.domain.error_codes import ChatErrorCode
    from chat.domain.interfaces.llm import LLMProvider, LLMEventType, LLMStreamEvent, TokenUsage
    from chat.domain.repositories.model_repo import ModelRequestInfo

from beanie import Document, PydanticObjectId
from dependency_injector import providers
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from chat.application.tools.core.definition import (
    ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy, ToolRiskLevel,
)
from common.core.exceptions import ServiceException
from common.security import require_login


class ScriptedProvider(LLMProvider):
    provider_type = ProviderType.LITELLM_OPENAI_COMPATIBLE

    def __init__(self, script):
        self.script = script
        self.requests = []

    async def stream_chat_completion(self, messages, model_request, tools=None):
        request = SimpleNamespace(messages=deepcopy(messages), model=model_request, tools=tools or [])
        self.requests.append(request)
        events = self.script(request)
        if hasattr(events, "__aiter__"):
            async for event in events:
                yield event
        else:
            for event in events:
                yield event


def text(value):
    return [LLMStreamEvent(type=LLMEventType.TEXT_DELTA, delta=value),
            LLMStreamEvent(type=LLMEventType.USAGE, usage=TokenUsage(input_tokens=3, output_tokens=2))]


def call(name, arguments=None, call_id="call-1"):
    return [LLMStreamEvent(type=LLMEventType.TOOL_CALLS, tool_calls=[
        ToolCallMessage(call_id=call_id, name=name, arguments=arguments or {}),
    ]), LLMStreamEvent(type=LLMEventType.USAGE, usage=TokenUsage(input_tokens=3, output_tokens=2))]


class EchoTool:
    def __init__(self, name="echo", *, risk=ToolRiskLevel.LOW):
        self.definition = ToolDefinition(
            llm_spec=ToolLLMSpec(name=name, description="Echo", parameters_schema=ToolParametersSchema({
                "type": "object", "properties": {"value": {"type": "string"}},
                "required": ["value"], "additionalProperties": False,
            })),
            policy=ToolPolicy(expose_by_default=True, risk_level=risk, persist_output=True),
        )
        self.effects = []

    async def execute(self, context, config=None, value=""):
        self.effects.append((context["session_id"], value))
        return value


class MemoryTurnStream:
    def __init__(self):
        self.active = {}
        self.frames = {}
        self.changed = asyncio.Event()
        self.released = asyncio.Event()
        self.cancelled = set()
        self.commands = {}
        self.command_read = asyncio.Event()
        self.active_turn_ttl = 0.15
        self.renewed = asyncio.Event()
        self.renew_count = 0

    async def acquire_active_turn(self, user_id, session_id, turn_id):
        key = (user_id, session_id)
        if key in self.active:
            return False
        self.active[key] = turn_id
        self.released.clear()
        return True

    async def get_active_turn(self, user_id, session_id):
        return self.active.get((user_id, session_id))

    async def renew_active_turn(self, user_id, session_id, turn_id):
        self.renew_count += 1
        if self.renew_count >= 5:
            self.renewed.set()
        return self.active.get((user_id, session_id)) == turn_id

    async def release_active_turn(self, user_id, session_id, turn_id):
        self.released.set()
        if await self.renew_active_turn(user_id, session_id, turn_id):
            del self.active[(user_id, session_id)]
            return True
        return False

    async def request_turn_cancel(self, turn_id):
        self.cancelled.add(turn_id)

    async def is_turn_cancel_requested(self, turn_id):
        return turn_id in self.cancelled

    async def submit_subagent_command(self, user_id, session_id, turn_id, command, *, allow_new=True):
        key = (turn_id, command["command_id"])
        if key in self.commands:
            previous = self.commands[key]
            if any(previous.get(field) != command.get(field) for field in ("run_id", "kind", "input")):
                return None
        else:
            if (not allow_new or self.active.get((user_id, session_id)) != turn_id
                    or sum(turn == turn_id for turn, _ in self.commands) >= 256):
                return None
            self.commands[key] = {**deepcopy(command), "status": "accepted"}
        return deepcopy(self.commands[key])

    async def pending_subagent_commands(self, turn_id):
        pending = [deepcopy(c) for (turn, _), c in self.commands.items()
                   if turn == turn_id and c["status"] == "accepted"]
        if pending:
            self.command_read.set()
        return pending

    async def finish_subagent_command(self, turn_id, command_id, status, detail=None):
        self.commands[(turn_id, command_id)].update(status=status, detail=detail)

    async def append_frame(self, turn_id, frame, *, terminal=False):
        self.frames.setdefault(turn_id, []).append((frame, terminal))
        self.changed.set()

    async def iter_frames(self, turn_id):
        index = 0
        while True:
            self.changed.clear()
            frames = self.frames.get(turn_id, [])
            while index < len(frames):
                frame, terminal = frames[index]
                index += 1
                yield frame, terminal
                if terminal:
                    return
            await self.changed.wait()


class MemorySessionRepo:
    def __init__(self):
        self.sessions = {}

    async def get_session_for_user(self, session_id, user_id):
        session = self.sessions.get(session_id)
        if session is None or session.user_id != user_id:
            raise ServiceException(ChatErrorCode.SESSION_NOT_FOUND)
        return session

    async def get_session(self, session_id):
        return self.sessions[session_id]

    async def get_session_attachments(self, session_id, user_id):
        await self.get_session_for_user(session_id, user_id)
        return [], []


class MemoryMessageRepo:
    def __init__(self):
        self.messages = []

    async def save_messages(self, messages):
        for message in messages:
            if message.id is None:
                message.id = PydanticObjectId()
        self.messages.extend(deepcopy(messages))

    async def list_session_messages(self, session_id, **kwargs):
        return [m for m in self.messages if m.session_id == session_id]


class MemoryHotContext:
    def __init__(self):
        self.messages = {}

    async def get_recent_context(self, session_id):
        return deepcopy(self.messages.get(session_id, []))

    async def append_messages(self, session_id, messages):
        self.messages.setdefault(session_id, []).extend(deepcopy(messages))

    async def load_messages(self, session_id, messages):
        self.messages[session_id] = deepcopy(messages)


class MemorySuspendedRepo:
    def __init__(self):
        self.chats = {}
        self.snapshots = []

    async def find_suspended_by_session(self, session_id, user_id):
        return self.chats.get((session_id, user_id))

    async def create(self, chat):
        chat.id = PydanticObjectId()
        snapshot = chat.model_dump(mode="json")
        self.snapshots.append(snapshot)
        self.chats[(chat.session_id, chat.user_id)] = type(chat).model_validate(snapshot)

    async def delete_by_id(self, chat_id):
        self.chats = {key: chat for key, chat in self.chats.items() if str(chat.id) != chat_id}

class MemorySubagentRepo:
    def __init__(self):
        self.runs = {}
        self.roots = {}

    async def save_root(self, root):
        self.roots[root.root_execution_id] = deepcopy(root)

    async def get_root(self, user_id, session_id, root_execution_id):
        root = self.roots.get(root_execution_id)
        return deepcopy(root) if root and root.user_id == user_id and root.session_id == session_id else None

    async def save_run(self, run):
        self.runs[run.run_id] = deepcopy(run)

    async def list_runs(self, user_id, session_id, root_execution_id=None):
        return [deepcopy(run) for run in self.runs.values()
                if run.user_id == user_id and run.session_id == session_id
                and (root_execution_id is None or run.root_execution_id == root_execution_id)]

    async def get_run(self, user_id, session_id, child_id, run_id=None):
        runs = [run for run in await self.list_runs(user_id, session_id)
                if run.child_id == child_id and (run_id is None or run.run_id == run_id)]
        return runs[-1] if runs else None


class Harness:
    def __init__(self, script, *, tool=None, tools_enabled=True):
        self.provider = ScriptedProvider(script)
        self.sessions = MemorySessionRepo()
        self.messages = MemoryMessageRepo()
        self.hot = MemoryHotContext()
        self.suspended = MemorySuspendedRepo()
        self.subagents = MemorySubagentRepo()
        self.stream = MemoryTurnStream()
        self.manager = ChatTurnStreamManager(stream_repo=self.stream)
        self.policy = AgentSpec(
            system_prompt="parent role", auto_generate_title=False,
            memory_policy=AgentMemoryPolicy(enable_long_term_memory=False, enable_chat_memory_summary=False),
            tool_and_skill_policy={"enable_use_tool": tools_enabled, "enable_use_skill": False},
        )
        self.model = ModelRequestInfo(
            model=Model(id=PydanticObjectId(_CONFIG["DEFAULT_MODEL_ID"]), display_name="test"),
            mapping=ModelProviderMapping(model_id=PydanticObjectId(_CONFIG["DEFAULT_MODEL_ID"]),
                                         provider_id=PydanticObjectId("000000000000000000000002"),
                                         provider_model_name="gpt-4o"),
            provider=Provider(name="test", api_key="test-only", scope=ProviderScope.USER),
            runtime_options={},
        )
        self.memory = SimpleNamespace(search=AsyncMock(return_value=[]), add_interaction=AsyncMock())
        self.kafka = SimpleNamespace(send=AsyncMock(), send_confirmed=AsyncMock(return_value={
            "status": "confirmed", "topic": "test", "partition": 0, "offset": 1,
        }))
        self.config_repo = SimpleNamespace(list_tool_configs=AsyncMock(return_value=[]))
        self.registry = ToolRegistry(tool_config_repo=self.config_repo)
        for delegation_tool in subagent_tools():
            self.registry.register(delegation_tool)
        if tool:
            self.registry.register(tool)
        resolver = LLMProviderResolver(*([self.provider] * 5))
        self.coordinator = ChatTurnCoordinator(
            llm_provider_resolver=resolver, text_llm=None, token_counter=TokenCounter(), memory=self.memory,
            model_repo=SimpleNamespace(resolve_model_for_chat=AsyncMock(return_value=self.model)),
            provider_repo=SimpleNamespace(increment_usage=AsyncMock()),
            session_repo=self.sessions, message_repo=self.messages, hot_context_repo=self.hot,
            suspended_chat_repo=self.suspended, tool_registry=self.registry,
            tool_dispatcher=ToolDispatcher(output_cache=ToolOutputCache(tool_content_store=None)),
            kafka_producer=self.kafka,
            tool_policy_builder=ChatTurnToolPolicyBuilder(skill_matcher=None), oss_file_loader=None,
            agent_resolver=SimpleNamespace(resolve=AsyncMock(return_value=Agent(
                agent_id="default-chat-agent", name="test", spec=self.policy,
            ))), redis_client=SimpleNamespace(exists=AsyncMock(return_value=0)),
            subagent_repo=self.subagents,
            turn_stream_repo=self.stream,
        )
        self.app = FastAPI()
        self.app.include_router(chat_endpoint.router, prefix="/chat")
        self.app.dependency_overrides[require_login] = lambda: "user-1"

        @self.app.exception_handler(ServiceException)
        async def service_error(_request, exc):
            return JSONResponse(status_code=400, content={"code": exc.code, "msg": exc.msg})

    def add_session(self, name="session-1", user_id="user-1"):
        self.sessions.sessions[name] = ChatSession(user_id=user_id)

    async def __aenter__(self):
        self.overrides = [
            container.chat_turn_coordinator.override(providers.Object(self.coordinator)),
            container.chat_turn_stream_manager.override(providers.Object(self.manager)),
            container.session_repo.override(providers.Object(self.sessions)),
        ]
        container.wire(modules=[chat_endpoint])
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()
        tasks = list(self.manager._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for override in reversed(self.overrides):
            override.__exit__(*args)

    async def post(self, body=None, *, path="/chat/completions"):
        response = await asyncio.wait_for(self.client.post(path, json=body or {
            "session_id": "session-1", "query": "hello",
        }), timeout=10)
        if response.status_code != 200:
            return response, []
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: ") and line != "data: [DONE]"]
        await asyncio.wait_for(self.stream.released.wait(), timeout=10)
        return response, events
