import asyncio
import json
import unittest
from unittest.mock import patch

from tests.chat.support import (
    Document, EchoTool, Harness, Role, ToolRiskLevel, call, text,
)


class ChatRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.collection = patch.object(Document, "get_pymongo_collection", return_value=None)
        self.collection.start()
        self.addCleanup(self.collection.stop)

    async def test_public_text_chat_persists_and_completes_sse(self):
        async with Harness(lambda _request: text("answer")) as h:
            h.add_session()
            response, events = await h.post()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.text.endswith("data: [DONE]\n\n"))
            self.assertEqual([e["delta"] for e in events if e["type"] == "text-delta"], ["answer"])
            await asyncio.gather(*list(h.manager._tasks.values()))
            self.assertEqual([(m.role, m.content) for m in h.messages.messages],
                             [(Role.USER, "hello"), (Role.ASSISTANT, "answer")])

    async def test_persistence_finishes_before_done_without_waiting_for_title(self):
        async with Harness(lambda _request: text("answer")) as h:
            h.add_session()
            title_started, title_continue = asyncio.Event(), asyncio.Event()

            async def slow_title(**kwargs):
                title_started.set()
                await title_continue.wait()

            h.policy.auto_generate_title = True
            h.coordinator._turn_finalizer.auto_generate_title = slow_title
            try:
                response, _ = await h.post()
                self.assertTrue(response.text.endswith("data: [DONE]\n\n"))
                self.assertEqual([m.content for m in h.messages.messages], ["hello", "answer"])
                await asyncio.wait_for(title_started.wait(), timeout=1)
                self.assertIsNone(await h.stream.get_active_turn("user-1", "session-1"))
                await asyncio.sleep(h.stream.active_turn_ttl)
                self.assertFalse(h.manager._lost_leases)
            finally:
                title_continue.set()

    async def test_tool_disabled_rejects_forged_call_at_execution_gate(self):
        tool = EchoTool()
        def script(request):
            if request.messages[-1].role == Role.TOOL:
                return text("rejected")
            self.assertEqual(request.tools, [])
            return call("echo", {"value": "must not execute"})

        async with Harness(script, tool=tool, tools_enabled=False) as h:
            h.add_session()
            _, events = await h.post()
            output = next(e["output"] for e in events if e["type"] == "tool-output-available")
            self.assertIn("Tool Unavailable", output)
            self.assertEqual(tool.effects, [])

    async def test_public_tool_roundtrip_keeps_scope_and_result(self):
        tool = EchoTool()
        def script(request):
            return text("integrated") if request.messages[-1].role == Role.TOOL else call("echo", {"value": "tool answer"})

        async with Harness(script, tool=tool) as h:
            h.add_session()
            _, events = await h.post()
            output = json.loads(next(e["output"] for e in events if e["type"] == "tool-output-available"))
            self.assertEqual(output["output"], "tool answer")
            self.assertEqual(tool.effects, [("session-1", "tool answer")])

    async def test_approval_snapshot_recovers_without_replaying_model_step(self):
        tool = EchoTool(risk=ToolRiskLevel.HIGH)
        def script(request):
            return text("approved answer") if request.messages[-1].role == Role.TOOL else call("echo", {"value": "approved"})

        async with Harness(script, tool=tool) as h:
            h.add_session()
            _, suspended_events = await h.post()
            self.assertTrue(any(e["type"] == "data-tool-approval-request" for e in suspended_events))
            self.assertEqual(tool.effects, [])
            self.assertEqual(len(h.suspended.snapshots), 1)
            await asyncio.gather(*list(h.manager._tasks.values()))
            _, events = await h.post({"session_id": "session-1", "tool_approval_status": [
                {"tool_call_id": "call-1", "approved": True},
            ]}, path="/chat/completions/recover")
            self.assertEqual(tool.effects, [("session-1", "approved")])
            self.assertEqual(len(h.provider.requests), 2)
            self.assertEqual(h.suspended.chats, {})
            self.assertEqual([e["delta"] for e in events if e["type"] == "text-delta"], ["approved answer"])

    async def test_session_ownership_is_checked_before_model_execution(self):
        async with Harness(lambda _request: text("not allowed")) as h:
            h.add_session(user_id="someone-else")
            response, _ = await h.post()
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["code"], 40001)
            self.assertEqual(h.provider.requests, [])

    async def test_client_tool_snapshot_roundtrip_uses_submitted_result(self):
        def script(request):
            if request.messages[-1].role == Role.TOOL:
                return text(request.messages[-1].content)
            return call("client_echo", {"value": "client input"})

        async with Harness(script) as h:
            h.add_session()
            _, events = await h.post({"session_id": "session-1", "query": "hello",
                                      "client_tool_capabilities": [{"name": "client_echo", "description": "client",
                                          "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}}}]})
            self.assertFalse(any(e["type"] == "tool-output-available" for e in events))
            _, recovered = await h.post({"session_id": "session-1", "client_tool_results": [
                {"tool_call_id": "call-1", "output": "client result"},
            ]}, path="/chat/completions/recover")
            self.assertIn("client result", "".join(e["delta"] for e in recovered if e["type"] == "text-delta"))
            self.assertEqual(h.suspended.chats, {})

    async def test_cancellation_does_not_leak_between_concurrent_turns(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        async def script(request):
            if request.messages[-1].session_id == "cancel-me":
                started.set()
                await cancelled.wait()
                for event in text("must not appear"):
                    yield event
            else:
                await started.wait()
                for event in text("other turn is intact"):
                    yield event

        async with Harness(script) as h:
            h.add_session("cancel-me")
            h.add_session("keep-me")
            pending = asyncio.create_task(h.client.post("/chat/completions", json={"session_id": "cancel-me", "query": "go"}))
            await asyncio.wait_for(started.wait(), timeout=5)
            turn_id = h.stream.active[("user-1", "cancel-me")]
            await h.stream.request_turn_cancel(turn_id)
            cancelled.set()
            response, _ = await h.post({"session_id": "keep-me", "query": "go"})
            stopped = await pending
            self.assertNotIn("must not appear", stopped.text)
            self.assertIn("other turn is intact", response.text)

    async def test_bound_tool_execution_is_isolated_and_cannot_expand_scope(self):
        from chat.application.execution_bindings import ExecutionBindings
        from chat.application.tools.core.registry import ToolScope
        from chat.application.tools.core.llm.invocation import ToolInvocation
        from chat.application.tools.core.execution.dispatcher import ToolDispatcher
        from chat.application.tools.core.output_cache.cache_manager import ToolOutputCache

        tool = EchoTool()
        scope = ToolScope(tools={"echo": tool}, context={"session_id": "session-1"})
        gate = asyncio.Event()
        async def first(**kwargs):
            await gate.wait()
            return "first run"
        async def second(**kwargs):
            gate.set()
            return "second run"

        dispatcher = ToolDispatcher(output_cache=ToolOutputCache(tool_content_store=None))
        invocation = ToolInvocation(tool_call_id="one", tool_name="echo", tool_call_arguments={"value": "ignored"})
        a, b = await asyncio.gather(
            dispatcher.dispatch([invocation], scope, bindings=ExecutionBindings(tool_handlers={"echo": first})),
            dispatcher.dispatch([invocation], scope, bindings=ExecutionBindings(tool_handlers={"echo": second})),
        )
        self.assertEqual([a[0].tool_output.content, b[0].tool_output.content], ["first run", "second run"])
        missing = ToolScope(tools={}, context={"session_id": "session-1"})
        denied = await dispatcher.dispatch([invocation], missing, bindings=ExecutionBindings(tool_handlers={"echo": first}))
        self.assertEqual(denied[0].tool_execution_error.reason, "Tool Unavailable")
        self.assertEqual(scope.to_suspension_data()["context"], {"session_id": "session-1"})
        self.assertEqual(tool.effects, [])


if __name__ == "__main__":
    unittest.main()
