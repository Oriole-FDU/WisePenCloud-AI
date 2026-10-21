import asyncio
import json
import unittest
from unittest.mock import patch

from tests.chat.support import Document, EchoTool, Harness, Role, ToolRiskLevel, call, text


def output(request):
    return json.loads(json.loads(request.messages[-1].content)["output"])


class SubagentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.collection = patch.object(Document, "get_pymongo_collection", return_value=None)
        self.collection.start()
        self.addCleanup(self.collection.stop)

    async def test_nonblocking_fresh_child_wait_and_parent_integration(self):
        parent_progress = asyncio.Event()
        started = []
        async def script(request):
            if request.messages[0].content == "child role":
                self.assertEqual([m.content for m in request.messages], ["child role", "child task"])
                await parent_progress.wait()
                events = text("child-only result")
            elif request.messages[-1].role == Role.TOOL:
                if request.messages[-1].tool_name == "subagent_start":
                    result = output(request)
                    started.append(result)
                    parent_progress.set()
                    events = call("subagent_wait", {"run_ids": [result["run_id"]]}, "wait")
                else:
                    self.assertEqual(output(request)["runs"][0]["output"], "child-only result")
                    events = text("parent integrated")
            else:
                events = call("subagent_start", {"task": "child task", "instructions": "child role"})
            for event in events:
                yield event

        async with Harness(script) as h:
            h.add_session()
            response, events = await h.post({"session_id": "session-1", "query": "hello", "subagents": {}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(started), 1)
            self.assertTrue(all(started[0][key] for key in ("child_id", "run_id", "root_execution_id")))
            self.assertEqual([e["delta"] for e in events if e["type"] == "text-delta"], ["parent integrated"])
            child_events = [e["data"] for e in events if e["type"] == "data-subagent"]
            self.assertTrue(any(e.get("run", {}).get("status") == "completed" for e in child_events))
            completed = [e["run"] for e in child_events if e.get("run", {}).get("status") == "completed"][-1]
            self.assertEqual(completed["billing_status"], "confirmed")
            self.assertEqual([e["sequence"] for e in child_events], sorted(set(e["sequence"] for e in child_events)))
            self.assertFalse(any(m.content == "child-only result" for m in h.messages.messages))

    async def test_early_parent_answer_waits_then_integrates_with_remaining_iterations(self):
        def script(request):
            if request.messages[0].content == "child role":
                return text("child result")
            if request.messages[-1].metadata.get("subagent_results"):
                self.assertIn("child result", request.messages[-1].content)
                return text("integrated after join")
            if request.messages[-1].role == Role.TOOL:
                return text("premature draft")
            return call("subagent_start", {"task": "task", "instructions": "child role"})

        async with Harness(script) as h:
            h.add_session()
            _, events = await h.post({"session_id": "session-1", "query": "hello", "subagents": {}})
            self.assertEqual([e["delta"] for e in events if e["type"] == "text-delta"], ["integrated after join"])

    async def test_child_cannot_execute_parent_approval_tool_or_recall_shared_memory(self):
        tool = EchoTool(risk=ToolRiskLevel.HIGH)
        def script(request):
            if request.messages[0].content == "child role":
                self.assertNotIn("echo", [s["function"]["name"] for s in request.tools])
                if request.messages[-1].role == Role.TOOL:
                    self.assertIn("Tool Unavailable", request.messages[-1].content)
                    return text("child denied")
                return call("echo", {"value": "must not run"})
            if request.messages[-1].metadata.get("subagent_results"):
                return text("integrated")
            if request.messages[-1].role == Role.TOOL:
                return text("join first")
            return call("subagent_start", {"task": "task", "instructions": "child role"})

        async with Harness(script, tool=tool) as h:
            h.add_session()
            h.policy.memory_policy.enable_long_term_memory = True
            _, events = await h.post({"session_id": "session-1", "query": "hello", "subagents": {}})
            self.assertEqual(tool.effects, [])
            self.assertTrue(any(e.get("data", {}).get("run", {}).get("output") == "child denied" for e in events))
            self.assertFalse(any(e["type"] == "data-tool-approval-request" for e in events))
            self.assertEqual(h.memory.search.await_count, 1)
            stored = h.memory.add_interaction.await_args.kwargs["messages"]
            self.assertFalse(any(m.content == "task" for m in stored))
            self.assertEqual([m.content for m in stored if m.role == Role.USER], ["hello"])

    async def test_join_does_not_open_unbounded_extra_iterations(self):
        def script(request):
            if request.messages[0].content == "child role":
                return text("child result")
            if request.messages[-1].role == Role.TOOL:
                return text("premature draft")
            return call("subagent_start", {"task": "task", "instructions": "child role"})

        async with Harness(script) as h:
            h.add_session()
            h.policy.agent_max_iterations = 2
            _, events = await h.post({"session_id": "session-1", "query": "hello", "subagents": {}})
            answer = "".join(e["delta"] for e in events if e["type"] == "text-delta")
            self.assertIn("未能生成最终答案", answer)
            self.assertIn("2", answer)
            self.assertNotIn("premature draft", answer)
            self.assertEqual(len(h.provider.requests), 3)

    async def test_disabled_or_omitted_delegation_cannot_be_forged(self):
        for config, enabled in ((None, True), ({}, False)):
            with self.subTest(config=config, tools_enabled=enabled):
                def script(request):
                    self.assertNotIn("subagent_start", [s["function"]["name"] for s in request.tools])
                    return text("denied") if request.messages[-1].role == Role.TOOL else call(
                        "subagent_start", {"task": "task", "instructions": "child role"})
                async with Harness(script, tools_enabled=enabled) as h:
                    h.add_session()
                    _, events = await h.post({"session_id": "session-1", "query": "hello", "subagents": config})
                    self.assertFalse(any(e["type"] == "data-subagent" for e in events))
                    self.assertIn("Tool Unavailable", next(e["output"] for e in events if e["type"] == "tool-output-available"))

    async def test_failed_child_keeps_partial_result_and_parent_can_collect_it(self):
        async def script(request):
            if request.messages[0].content == "child role":
                for event in text("partial child result"):
                    yield event
                raise RuntimeError("controlled provider failure")
            if request.messages[-1].role == Role.TOOL:
                name = request.messages[-1].tool_name
                if name == "subagent_start":
                    events = call("subagent_list", call_id="list")
                elif name == "subagent_list":
                    self.assertEqual(len(output(request)["runs"]), 1)
                    events = call("subagent_wait", call_id="wait")
                else:
                    child = output(request)["runs"][0]
                    self.assertEqual(child["status"], "failed")
                    self.assertEqual(child["output"], "partial child result")
                    events = text("parent uses partial result")
            else:
                events = call("subagent_start", {"task": "task", "instructions": "child role"})
            for event in events:
                yield event

        async with Harness(script) as h:
            h.add_session()
            _, events = await h.post({"session_id": "session-1", "query": "hello", "subagents": {}})
            self.assertEqual([e["delta"] for e in events if e["type"] == "text-delta"], ["parent uses partial result"])

    async def test_child_deadline_is_reported_as_timeout_not_user_cancel(self):
        async def script(request):
            if request.messages[0].content == "child role":
                for event in text("partial before timeout"):
                    yield event
                await asyncio.Event().wait()
            if request.messages[-1].role == Role.TOOL:
                if request.messages[-1].tool_name == "subagent_start":
                    events = call("subagent_wait", call_id="wait")
                else:
                    self.assertEqual(output(request)["runs"][0]["status"], "timed_out")
                    events = text("parent handled timeout")
            else:
                events = call("subagent_start", {"task": "task", "instructions": "child role"})
            for event in events:
                yield event

        async with Harness(script) as h:
            h.add_session()
            _, events = await h.post({"session_id": "session-1", "query": "hello", "subagents": {"run_timeout_seconds": 1}})
            self.assertEqual([e["delta"] for e in events if e["type"] == "text-delta"], ["parent handled timeout"])

    async def test_exhausted_child_is_not_reported_as_completed(self):
        tool = EchoTool()
        def script(request):
            if request.messages[0].content == "child role":
                return call("echo", {"value": "tool result"})
            if request.messages[-1].role == Role.TOOL:
                if request.messages[-1].tool_name == "subagent_start":
                    return call("subagent_wait", call_id="wait")
                self.assertEqual(output(request)["runs"][0]["status"], "incomplete")
                return text("parent handled iteration limit")
            return call("subagent_start", {"task": "task", "instructions": "child role"})

        async with Harness(script, tool=tool) as h:
            h.add_session()
            _, events = await h.post({"session_id": "session-1", "query": "hello", "subagents": {"max_iterations": 1}})
            self.assertEqual([e["delta"] for e in events if e["type"] == "text-delta"], ["parent handled iteration limit"])

    async def test_parent_cancel_settles_a_quiet_child_while_waiting(self):
        started = asyncio.Event()
        async def script(request):
            if request.messages[0].content == "child role":
                started.set()
                await asyncio.Event().wait()
            events = call("subagent_wait", call_id="wait") if request.messages[-1].role == Role.TOOL else call(
                "subagent_start", {"task": "task", "instructions": "child role"})
            for event in events:
                yield event

        async with Harness(script) as h:
            h.add_session()
            pending = asyncio.create_task(h.client.post("/chat/completions", json={
                "session_id": "session-1", "query": "hello", "subagents": {},
            }))
            await asyncio.wait_for(started.wait(), timeout=5)
            try:
                cancelled = await asyncio.wait_for(h.client.post("/chat/completions/cancel", json={"session_id": "session-1"}), timeout=2)
                response = await asyncio.wait_for(pending, timeout=2)
            except TimeoutError:
                for task in list(h.manager._tasks.values()):
                    task.cancel()
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                self.fail("Root cancellation did not settle the waiting child")
            self.assertEqual(cancelled.status_code, 200)
            states = [json.loads(line[6:])["data"]["run"]["status"] for line in response.text.splitlines()
                      if line.startswith('data: {"type": "data-subagent"') and '"kind": "state"' in line]
            self.assertEqual(states[-1], "cancelled")

    async def test_cancel_during_last_iteration_join_is_not_exhaustion(self):
        started = asyncio.Event()

        async def script(request):
            if request.messages[0].content == "child role":
                started.set()
                await asyncio.Event().wait()
            for event in call("subagent_start", {"task": "task", "instructions": "child role"}):
                yield event

        async with Harness(script) as h:
            h.add_session()
            h.policy.agent_max_iterations = 1
            pending = asyncio.create_task(h.post({
                "session_id": "session-1", "query": "hello", "subagents": {},
            }))
            await asyncio.wait_for(started.wait(), timeout=5)
            await h.client.post("/chat/completions/cancel", json={"session_id": "session-1"})
            _, events = await asyncio.wait_for(pending, timeout=3)
            self.assertFalse(any(e["type"] == "text-delta" for e in events))
            self.assertTrue(any(e["type"] == "error" for e in events))
            self.assertFalse(any(m.metadata.get("incomplete") for m in h.messages.messages))

    async def test_saved_child_history_is_private_and_resume_creates_a_new_run(self):
        child_ids = []

        def script(request):
            if request.messages[0].content == "child role":
                return text("continued" if request.messages[-1].content == "continue task" else "child answer")
            if request.messages[-1].metadata.get("subagent_results"):
                return text("parent integrated")
            if request.messages[-1].role == Role.TOOL:
                child_ids.append(output(request))
                return text("parent draft")
            if request.messages[-1].content == "resume":
                return call("subagent_resume", {"child_id": child_ids[0]["child_id"], "task": "continue task"})
            return call("subagent_start", {"task": "task", "instructions": "child role"})

        async with Harness(script) as h:
            h.add_session()
            await h.post({"session_id": "session-1", "query": "hello", "subagents": {}})
            first = child_ids[0]
            response = await h.client.get("/chat/subagents", params={"session_id": "session-1",
                                                                    "root_execution_id": first["root_execution_id"]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["runs"][0]["output"], "child answer")
            detail = await h.client.get(f'/chat/subagents/{first["child_id"]}/runs/{first["run_id"]}',
                                        params={"session_id": "session-1"})
            self.assertEqual(detail.status_code, 200)
            self.assertNotIn("child role", detail.text)
            self.assertNotIn("test-only", detail.text)
            self.assertIn("child answer", detail.text)
            await h.post({"session_id": "session-1", "query": "resume", "subagents": {}})
            self.assertEqual(child_ids[1]["child_id"], first["child_id"])
            self.assertNotEqual(child_ids[1]["run_id"], first["run_id"])
            child_requests = [r for r in h.provider.requests if r.messages[0].content == "child role"]
            self.assertEqual([m.content for m in child_requests[-1].messages],
                             ["child role", "task", "child answer", "continue task"])
            self.assertFalse(any(m.content in ("child answer", "continued") for m in h.messages.messages))
            from chat.api.converters.ui_message_converter import convert_to_ui_messages
            ui = convert_to_ui_messages(h.messages.messages)
            self.assertEqual(ui[1]["metadata"]["root_execution_id"], first["root_execution_id"])

    async def test_no_save_and_session_ownership_forbid_history_and_resume(self):
        def script(request):
            if request.messages[0].content == "child role":
                return text("private child answer")
            if request.messages[-1].metadata.get("subagent_results"):
                return text("parent")
            if request.messages[-1].role == Role.TOOL:
                return text("draft")
            return call("subagent_start", {"task": "private task", "instructions": "child role"})

        async with Harness(script) as h:
            h.add_session()
            h.add_session("other", user_id="user-2")
            h.policy.memory_policy.enable_persistence_chat_memory = False
            await h.post({"session_id": "session-1", "query": "hello", "subagents": {}})
            saved = list(h.subagents.runs.values())[0]
            self.assertEqual(saved.messages, [])
            self.assertEqual(saved.output, "")
            self.assertFalse(saved.history_available)
            detail = await h.client.get(f"/chat/subagents/{saved.child_id}/runs/{saved.run_id}",
                                        params={"session_id": "session-1"})
            self.assertEqual(detail.status_code, 400)
            denied = await h.client.get("/chat/subagents", params={"session_id": "other"})
            self.assertEqual(denied.status_code, 400)
            h.sessions.sessions.clear()
            deleted = await h.client.get("/chat/subagents", params={"session_id": "session-1"})
            self.assertEqual(deleted.status_code, 400)

    async def test_rest_input_is_deduplicated_and_applied_at_next_iteration_boundary(self):
        child_started, release = asyncio.Event(), asyncio.Event()

        async def script(request):
            if request.messages[0].content == "child role":
                if len(request.messages) == 2:
                    child_started.set()
                    await release.wait()
                    events = call("echo", {"value": "child step"})
                else:
                    self.assertEqual([m.content for m in request.messages if m.content == "new direction"],
                                     ["new direction"])
                    self.assertEqual(request.messages[-1].content, "new direction")
                    events = text("redirected")
            elif request.messages[-1].role == Role.TOOL:
                events = call("subagent_wait", call_id="wait") if request.messages[-1].tool_name == "subagent_start" else text("parent")
            else:
                events = call("subagent_start", {"task": "task", "instructions": "child role"})
            for event in events:
                yield event

        async with Harness(script, tool=EchoTool()) as h:
            h.add_session()
            pending = asyncio.create_task(h.post({"session_id": "session-1", "query": "hello", "subagents": {}}))
            await asyncio.wait_for(child_started.wait(), timeout=5)
            run = next(iter(h.subagents.runs.values()))
            path = f"/chat/subagents/{run.child_id}/input"
            body = {"session_id": "session-1", "root_execution_id": run.root_execution_id,
                    "run_id": run.run_id, "command_id": "command-1", "input": "new direction"}
            first = await h.client.post(path, json=body)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["data"]["status"], "accepted")
            await h.client.post(path, json=body)
            await asyncio.wait_for(h.stream.command_read.wait(), timeout=2)
            release.set()
            _, events = await pending
            controls = [e["data"] for e in events if e["type"] == "data-subagent" and e["data"]["kind"] == "control"]
            self.assertEqual(len([c for c in controls if c["status"] == "applied"]), 1)
            self.assertEqual(next(iter(h.subagents.runs.values())).output, "redirected")
            retry = await h.client.post(path, json=body)
            self.assertEqual(retry.status_code, 200)
            self.assertEqual(retry.json()["data"]["status"], "applied")
            self.assertEqual((await h.client.post(path, json={**body, "input": "conflicting input"})).status_code, 400)
            self.assertEqual((await h.client.post(path, json={**body, "command_id": "new-inactive-command"})).status_code, 400)

    async def test_nested_delegation_shares_quota_and_fork_drops_parent_system_and_native_payload(self):
        def script(request):
            role = request.messages[0].content
            if role == "grandchild role":
                return text("grand result")
            if role == "child role":
                if request.messages[-1].metadata.get("subagent_results"):
                    return text("child integrated grandchild")
                if request.messages[-1].role == Role.TOOL:
                    return text("child draft")
                self.assertEqual([m.role for m in request.messages], [Role.SYSTEM, Role.USER, Role.USER])
                self.assertEqual([m.content for m in request.messages[1:]], ["hello", "task"])
                self.assertTrue(all(m.provider_payload is None for m in request.messages))
                return call("subagent_start", {"task": "grand task", "instructions": "grandchild role"})
            if request.messages[-1].metadata.get("subagent_results"):
                return text("root integrated")
            if request.messages[-1].role == Role.TOOL:
                return text("root draft")
            return call("subagent_start", {"task": "task", "instructions": "child role", "context_mode": "fork"})

        async with Harness(script) as h:
            h.add_session()
            _, events = await h.post({"session_id": "session-1", "query": "hello",
                                      "subagents": {"max_depth": 2, "max_total_runs": 2}})
            runs = list(h.subagents.runs.values())
            self.assertEqual(len(runs), 2)
            self.assertEqual(runs[1].parent_run_id, runs[0].run_id)
            self.assertEqual(runs[0].output, "child integrated grandchild")
            self.assertEqual([e["delta"] for e in events if e["type"] == "text-delta"], ["root integrated"])

    async def test_independent_heartbeat_renews_quiet_wait_and_child_cancel_keeps_sibling(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def script(request):
            role = request.messages[0].content
            if role == "quiet":
                started.set()
                await asyncio.Event().wait()
            if role == "sibling":
                await release.wait()
                events = text("sibling survived")
            elif request.messages[-1].role == Role.TOOL:
                events = call("subagent_wait", call_id="wait") if request.messages[-1].tool_name == "subagent_start" else text("parent")
            else:
                events = call("subagent_start", {"task": "task", "instructions": "quiet"})
                events[0].tool_calls.extend(call("subagent_start", {"task": "task", "instructions": "sibling"}, "sibling")[0].tool_calls)
            for event in events:
                yield event

        async with Harness(script) as h:
            h.add_session()
            pending = asyncio.create_task(h.post({"session_id": "session-1", "query": "hello", "subagents": {}}))
            await asyncio.wait_for(started.wait(), timeout=5)
            h.stream.renew_count = 0
            h.stream.renewed.clear()
            await asyncio.wait_for(h.stream.renewed.wait(), timeout=2)
            run = next(r for r in h.subagents.runs.values() if r.messages[0].content == "quiet")
            response = await h.client.post(f"/chat/subagents/{run.child_id}/cancel", json={
                "session_id": "session-1", "root_execution_id": run.root_execution_id,
                "run_id": run.run_id, "command_id": "cancel-one",
            })
            self.assertEqual(response.status_code, 200)
            await asyncio.wait_for(h.stream.command_read.wait(), timeout=2)
            release.set()
            await pending
            self.assertEqual({r.status for r in h.subagents.runs.values()}, {"cancelled", "completed"})

    async def test_parallel_start_reserves_slots_and_depth_limit_cannot_be_forged(self):
        def script(request):
            if request.messages[0].content == "child role":
                self.assertNotIn("subagent_start", [s["function"]["name"] for s in request.tools])
                if request.messages[-1].role == Role.TOOL:
                    self.assertIn("Tool Unavailable", request.messages[-1].content)
                    return text("child done")
                return call("subagent_start", {"task": "blocked", "instructions": "too deep"})
            if request.messages[-1].metadata.get("subagent_results"):
                return text("parent integrated")
            if request.messages[-1].role == Role.TOOL:
                self.assertIn("Subagent Quota Exceeded", request.messages[-1].content)
                return text("draft")
            events = call("subagent_start", {"task": "task", "instructions": "child role"})
            events[0].tool_calls.extend(call("subagent_start", {"task": "task", "instructions": "child role"}, "second")[0].tool_calls)
            return events

        async with Harness(script) as h:
            h.add_session()
            await h.post({"session_id": "session-1", "query": "hello",
                          "subagents": {"max_active_runs": 1, "max_total_runs": 1}})
            self.assertEqual(len(h.subagents.runs), 1)
            self.assertEqual(next(iter(h.subagents.runs.values())).status, "completed")
            for field, maximum in (("max_active_runs", 8), ("max_total_runs", 32), ("run_timeout_seconds", 1200),
                                   ("max_iterations", 25), ("max_depth", 3)):
                response = await h.client.post("/chat/completions", json={
                    "session_id": "session-1", "query": "hello", "subagents": {field: maximum + 1},
                })
                self.assertEqual(response.status_code, 422)

    async def test_published_agent_version_and_load_policy_are_revalidated_on_resume(self):
        from tests.chat.support import Agent, AgentSpec, AsyncMock
        asset = Agent(agent_id="asset-1", name="asset", version=7, version_status="PUBLISHED",
                      spec=AgentSpec(system_prompt="private asset role", auto_generate_title=False,
                                     tool_and_skill_policy={"enable_use_tool": False, "enable_use_skill": False},
                                     memory_policy={"enable_long_term_memory": False}))
        ids = []
        def script(request):
            if request.messages[0].content == "private asset role":
                self.assertEqual(request.tools, [])
                return text("asset result")
            if request.messages[-1].metadata.get("subagent_results"):
                return text("parent integrated")
            if request.messages[-1].role == Role.TOOL:
                if request.messages[-1].tool_name == "subagent_resume":
                    self.assertIn("Permission", request.messages[-1].content)
                    return text("resume denied")
                ids.append(output(request))
                return text("draft")
            return call("subagent_resume", {"child_id": ids[0]["child_id"], "task": "next"}) if ids else call(
                "subagent_start", {"agent_id": "asset-1", "agent_version": 7, "task": "asset task"})

        async with Harness(script, tool=EchoTool()) as h:
            h.add_session()
            parent = h.coordinator._agent_resolver.resolve.return_value
            h.coordinator._agent_resolver.resolve.side_effect = lambda agent_id, version=None: asset if agent_id == "asset-1" else parent
            h.coordinator._resource_client = type("Resource", (), {"check_res_permission": AsyncMock(
                return_value=type("Permission", (), {"allows": lambda self, action: action == "LOAD"})())})()
            body = {"session_id": "session-1", "query": "hello",
                    "subagents": {"agents": [{"agent_id": "asset-1", "version": 7}]}}
            await h.post(body)
            self.assertEqual(len(ids), 1)
            check = h.coordinator._resource_client.check_res_permission
            self.assertEqual(check.await_args.kwargs["target_version"], 7)
            self.assertEqual(check.await_args.kwargs["user_id"], "user-1")
            detail = await h.client.get(f'/chat/subagents/{ids[0]["child_id"]}/runs/{ids[0]["run_id"]}',
                                        params={"session_id": "session-1"})
            self.assertNotIn("private asset role", detail.text)
            check.return_value = type("Denied", (), {"allows": lambda self, action: False})()
            _, events = await h.post({**body, "query": "resume"})
            self.assertEqual([e["delta"] for e in events if e["type"] == "text-delta"], ["resume denied"])
            self.assertEqual(len(h.subagents.runs), 1)

    async def test_actual_models_and_per_request_usage_use_their_own_rates_and_fixed_ids(self):
        from dataclasses import replace
        from tests.chat.support import PydanticObjectId, ProviderScope
        target_model = "000000000000000000000003"
        target_provider = "000000000000000000000004"

        def script(request):
            if request.messages[0].content == "child role":
                self.assertEqual(str(request.model.model_id), target_model)
                self.assertEqual(str(request.model.provider_id), target_provider)
                self.assertEqual(request.model.runtime_options, {"temperature": 1.1})
                return text("cross model child")
            if request.messages[-1].metadata.get("subagent_results"):
                return text("parent integrated")
            if request.messages[-1].role == Role.TOOL:
                return text("draft")
            return call("subagent_start", {"task": "task", "instructions": "child role",
                                          "model": target_model, "provider_id": target_provider})

        async with Harness(script) as h:
            h.add_session()
            h.model.mapping.input_billing_ratio = 2
            h.model.mapping.cached_input_billing_ratio = 2
            h.model.mapping.output_billing_ratio = 2
            h.model.provider.scope = ProviderScope.SYSTEM
            target = replace(h.model,
                             model=h.model.model.model_copy(update={"id": PydanticObjectId(target_model)}),
                             mapping=h.model.mapping.model_copy(update={
                                 "provider_id": PydanticObjectId(target_provider), "input_billing_ratio": 7,
                                 "cached_input_billing_ratio": 7, "output_billing_ratio": 7}))
            def resolve(**kw):
                info = target if str(kw["model_id"]) == target_model else h.model
                self.assertEqual(kw["user_id"], "user-1")
                return info.with_runtime_options(kw.get("runtime_options") or {})
            h.coordinator._model_repo.resolve_model_for_chat.side_effect = resolve
            h.provider.runtime_options_manifest = lambda: {
                "json_schema": {"type": "object", "properties": {"temperature": {"type": "number", "maximum": 2}},
                                "additionalProperties": False},
                "defaults": {"temperature": 1},
            }
            await h.post({"session_id": "session-1", "query": "hello", "runtime_options": {"temperature": 0.3},
                          "subagents": {"models": [{"model": target_model, "provider_id": target_provider,
                                                    "runtime_options": {"temperature": 1.1}}]}})
            run = next(iter(h.subagents.runs.values()))
            self.assertEqual(run.token_usage, 5)
            self.assertEqual(run.usage[0].provenance, "observed")
            self.assertEqual(run.usage[0].billable_tokens, 35)
            bills = h.kafka.send_confirmed.await_args_list
            self.assertEqual(len(bills), 4)
            self.assertEqual(sum(c.kwargs["value"]["billableTokens"] for c in bills), 65)
            self.assertEqual({c.kwargs["value"]["traceId"] for c in bills},
                             {u.request_id for root in h.subagents.roots.values() for u in root.usage}
                             | {u.request_id for u in run.usage})
            self.assertEqual(h.kafka.send.await_count, 0)

    async def test_cached_child_usage_keeps_split_mapping_rates_and_provider_counters(self):
        from decimal import Decimal
        from tests.chat.support import ProviderScope

        def script(request):
            if request.messages[0].content == "child role":
                events = text("cached child")
                events[-1].usage.cached_input_tokens = 1
                return events
            if request.messages[-1].metadata.get("subagent_results"):
                return text("integrated")
            if request.messages[-1].role == Role.TOOL:
                return text("draft")
            return call("subagent_start", {"task": "task", "instructions": "child role"})

        async with Harness(script) as h:
            h.add_session()
            h.model.provider.scope = ProviderScope.SYSTEM
            h.model.mapping.input_billing_ratio = Decimal("0.5")
            h.model.mapping.cached_input_billing_ratio = Decimal("0.25")
            h.model.mapping.output_billing_ratio = Decimal("2.1")
            await h.post({"session_id": "session-1", "query": "hello", "subagents": {}})
            run = next(iter(h.subagents.runs.values()))
            usage = run.usage[0]
            self.assertEqual((usage.input_tokens, usage.cached_input_tokens, usage.output_tokens), (3, 1, 2))
            self.assertEqual(usage.billable_tokens, 6)  # ceil(2 * .5 + 1 * .25 + 2 * 2.1)
            self.assertEqual(usage.provider_usage_status, "confirmed")
            counters = h.coordinator._turn_finalizer.provider_repo.increment_usage.await_args_list
            self.assertTrue(any(c.kwargs.get("cached_input_tokens") == 1 and c.kwargs["billable_tokens"] == 6
                                for c in counters))
            bill = next(c.kwargs["value"] for c in h.kafka.send_confirmed.await_args_list
                        if c.kwargs["value"]["traceId"] == usage.request_id)
            self.assertEqual(bill["billingDetail"]["cachedInputTokens"], 1)
            self.assertEqual(bill["billingDetail"]["inputBillingRatio"], "0.5")
            self.assertEqual(bill["billingDetail"]["cachedInputBillingRatio"], "0.25")
            self.assertEqual(bill["billingDetail"]["outputBillingRatio"], "2.1")

    async def test_resource_permission_adapter_sends_fixed_target_version(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from chat.service_client.resource_service_client import ResourceClient

        rpc = SimpleNamespace(post=AsyncMock(return_value={"allowedActions": ["LOAD"]}))
        client = ResourceClient(rpc)
        permission = await client.check_res_permission("asset-1", "42", {}, target_version=7)
        self.assertTrue(permission.allows("LOAD"))
        self.assertEqual(rpc.post.await_args.kwargs["json"], {
            "resourceId": "asset-1", "userId": 42, "groupRoles": {}, "targetVersion": 7,
        })
        await client.check_res_permission("asset-1", "42", {})
        self.assertNotIn("targetVersion", rpc.post.await_args.kwargs["json"])

    async def test_failed_provider_keeps_observed_usage_and_unknown_billing_is_not_retried(self):
        async def script(request):
            if request.messages[0].content == "child role":
                for event in text("partial output"):
                    yield event
                raise RuntimeError("provider failed after usage")
            events = text("parent") if request.messages[-1].metadata.get("subagent_results") else (
                text("draft") if request.messages[-1].role == Role.TOOL else
                call("subagent_start", {"task": "task", "instructions": "child role"}))
            for event in events:
                yield event

        async with Harness(script) as h:
            h.add_session()
            h.kafka.send_confirmed.return_value = {"status": "unknown"}
            await h.post({"session_id": "session-1", "query": "hello", "subagents": {}})
            run = next(iter(h.subagents.runs.values()))
            self.assertEqual((run.status, run.token_usage, run.billing_status), ("failed", 5, "unknown"))
            self.assertEqual(run.usage[0].provenance, "observed")
            count = h.kafka.send_confirmed.await_count
            await h.client.get("/chat/subagents", params={"session_id": "session-1"})
            self.assertEqual(h.kafka.send_confirmed.await_count, count)

    async def test_usage_without_provider_measurement_is_estimated_or_unknown(self):
        async def script(request):
            if request.messages[0].content == "child role":
                if request.messages[-1].content == "fail":
                    raise RuntimeError("no observable usage")
                yield text("estimated result")[0]
                return
            events = text("parent") if request.messages[-1].metadata.get("subagent_results") else (
                text("draft") if request.messages[-1].role == Role.TOOL else
                call("subagent_start", {"task": request.messages[-1].content, "instructions": "child role"}))
            for event in events:
                yield event
        async with Harness(script) as h:
            h.add_session()
            for query in ("hello", "fail"):
                await h.post({"session_id": "session-1", "query": query, "subagents": {}})
            runs = list(h.subagents.runs.values())
            self.assertEqual(runs[0].usage[0].provenance, "estimated")
            self.assertGreater(runs[0].usage[0].token_usage, 0)
            self.assertEqual(runs[1].usage[0].provenance, "unknown")
            self.assertIsNone(runs[1].usage[0].token_usage)
            self.assertEqual(runs[1].usage[0].billing_status, "not_attempted")

    async def test_explicit_zero_usage_is_observed_and_never_replaced_with_an_estimate(self):
        from tests.chat.support import TokenUsage

        def script(request):
            if request.messages[0].content == "child role":
                events = text("zero-token child")
            elif request.messages[-1].metadata.get("subagent_results"):
                events = text("integrated")
            elif request.messages[-1].role == Role.TOOL:
                events = text("draft")
            else:
                events = call("subagent_start", {"task": "task", "instructions": "child role"})
            events[-1].usage = TokenUsage()
            return events

        async with Harness(script) as h:
            h.add_session()
            await h.post({"session_id": "session-1", "query": "hello", "subagents": {}})
            run = next(iter(h.subagents.runs.values()))
            usages = run.usage + next(iter(h.subagents.roots.values())).usage
            self.assertEqual(run.status, "completed")
            self.assertTrue(usages)
            self.assertTrue(all((u.provenance, u.token_usage) == ("observed", 0) for u in usages))
            self.assertEqual(h.kafka.send_confirmed.await_count, 0)
            self.assertEqual(h.kafka.send.await_count, 0)

    async def test_invalid_published_candidates_and_versions_cannot_start_a_child(self):
        from tests.chat.support import Agent, AgentSpec, AsyncMock
        from types import SimpleNamespace

        for case in ("outside-candidates", "wrong-version", "unpublished", "no-load", "empty-role", "model-conflict"):
            with self.subTest(case=case):
                asset = Agent(agent_id="asset-1", name="asset", version=8 if case == "wrong-version" else 7,
                              version_status="DRAFT" if case == "unpublished" else "PUBLISHED",
                              spec=AgentSpec(system_prompt=" " if case == "empty-role" else "private asset role",
                                             model_policy={"allow_request_override": False,
                                                           "default_model_id": "000000000000000000000003"}
                                             if case == "model-conflict" else {}))

                def script(request):
                    return text("delegation rejected") if request.messages[-1].role == Role.TOOL else call(
                        "subagent_start", {"task": "task", "agent_id": "asset-1", "agent_version": 7})

                async with Harness(script) as h:
                    h.add_session()
                    parent = h.coordinator._agent_resolver.resolve.return_value
                    h.coordinator._agent_resolver.resolve.side_effect = lambda agent_id, version=None: asset if agent_id == "asset-1" else parent
                    h.coordinator._resource_client = SimpleNamespace(check_res_permission=AsyncMock(
                        return_value=SimpleNamespace(allows=lambda action: case != "no-load")))
                    _, events = await h.post({"session_id": "session-1", "query": "hello", "subagents": {
                        "agents": [] if case == "outside-candidates" else [{"agent_id": "asset-1", "version": 7}],
                    }})
                    self.assertFalse(h.subagents.runs)
                    self.assertEqual(len(h.provider.requests), 2)
                    self.assertIn("delegation rejected", "".join(e.get("delta", "") for e in events))

    async def test_model_candidate_permission_wallet_and_options_are_enforced(self):
        from dataclasses import replace
        from tests.chat.support import PydanticObjectId, ProviderScope, ServiceException, ChatErrorCode

        target_model, target_provider = "000000000000000000000003", "000000000000000000000004"
        for case in ("outside-candidates", "outside-provider", "permission-revoked", "provider-changed", "wallet-blocked", "invalid-options"):
            with self.subTest(case=case):
                def script(request):
                    return text("model rejected") if request.messages[-1].role == Role.TOOL else call(
                        "subagent_start", {"task": "task", "instructions": "child role", "model": target_model,
                                           "provider_id": "000000000000000000000006" if case == "outside-provider" else target_provider})

                async with Harness(script) as h:
                    h.add_session()
                    target = replace(h.model,
                                     model=h.model.model.model_copy(update={"id": PydanticObjectId(target_model)}),
                                     mapping=h.model.mapping.model_copy(update={
                                         "provider_id": PydanticObjectId(target_provider), "input_billing_ratio": 2}),
                                     provider=h.model.provider.model_copy(update={"scope": ProviderScope.SYSTEM}))
                    target_resolves = 0

                    def resolve(**kw):
                        nonlocal target_resolves
                        if str(kw["model_id"]) != target_model:
                            return h.model
                        target_resolves += 1
                        if target_resolves > 1 and case == "permission-revoked":
                            raise ServiceException(ChatErrorCode.MODEL_NOT_FOUND)
                        info = target
                        if target_resolves > 1 and case == "provider-changed":
                            info = replace(target, mapping=target.mapping.model_copy(update={
                                "provider_id": PydanticObjectId("000000000000000000000006")}))
                        return info.with_runtime_options(kw.get("runtime_options") or {})

                    h.coordinator._model_repo.resolve_model_for_chat.side_effect = resolve
                    h.coordinator._redis.exists.side_effect = lambda *keys: int(case == "wallet-blocked" and target_resolves > 1)
                    h.provider.runtime_options_manifest = lambda: {
                        "json_schema": {"type": "object", "properties": {"temperature": {"type": "number", "maximum": 2}},
                                        "additionalProperties": False}, "defaults": {},
                    }
                    _, events = await h.post({"session_id": "session-1", "query": "hello", "subagents": {
                        "models": [] if case == "outside-candidates" else [{
                            "model": target_model, "provider_id": target_provider,
                            "runtime_options": {"temperature": 3} if case == "invalid-options" else {},
                        }],
                    }})
                    self.assertFalse(h.subagents.runs)
                    self.assertTrue(all(str(req.model.model_id) != target_model for req in h.provider.requests))
                    if case == "invalid-options":
                        self.assertTrue(any(e["type"] == "error" for e in events))
                    else:
                        self.assertIn("model rejected", "".join(e.get("delta", "") for e in events))

    async def test_kafka_ack_is_required_and_enqueue_failure_differs_from_delivery_failure(self):
        from common.kafka.producer import KafkaProducerClient
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        producer = KafkaProducerClient(bootstrap_servers="unused")
        value = {"traceId": "fixed-test-id"}
        self.assertEqual((await producer.send_confirmed("test", value))["status"], "failed")
        ack = asyncio.get_running_loop().create_future()
        ack.set_result(SimpleNamespace(topic="test", partition=1, offset=2))
        producer._producer = SimpleNamespace(send=AsyncMock(return_value=ack))
        result = await producer.send_confirmed("test", value)
        self.assertEqual(result, {"status": "confirmed", "topic": "test", "partition": 1, "offset": 2})
        failed_delivery = asyncio.get_running_loop().create_future()
        failed_delivery.set_exception(RuntimeError("delivery outcome uncertain"))
        producer._producer.send.return_value = failed_delivery
        self.assertEqual((await producer.send_confirmed("test", value))["status"], "unknown")
        producer._producer.send.side_effect = ValueError("rejected before enqueue")
        self.assertEqual((await producer.send_confirmed("test", value))["status"], "failed")

    async def test_approval_settles_children_then_recover_keeps_root_and_total_quota(self):
        child_started = asyncio.Event()
        async def script(request):
            if request.messages[0].content == "child role":
                child_started.set()
                await asyncio.Event().wait()
            if request.messages[-1].metadata.get("subagent_results"):
                for event in text("recovered with original quota"):
                    yield event
                return
            if request.messages[-1].role == Role.TOOL:
                name = request.messages[-1].tool_name
                if name == "echo":
                    events = call("subagent_start", {"task": "extra", "instructions": "extra role"}, "extra")
                elif name == "subagent_start" and "Subagent Quota Exceeded" in request.messages[-1].content:
                    events = text("recovered with original quota")
                else:
                    await child_started.wait()
                    events = call("echo", {"value": "approved tool"}, "approval")
            else:
                events = call("subagent_start", {"task": "task", "instructions": "child role"})
            for event in events:
                yield event

        async with Harness(script, tool=EchoTool(risk=ToolRiskLevel.HIGH)) as h:
            h.add_session()
            _, first = await h.post({"session_id": "session-1", "query": "hello", "subagents": {"max_total_runs": 1}})
            run = next(iter(h.subagents.runs.values()))
            self.assertEqual(run.status, "cancelled")
            context = h.suspended.chats[("session-1", "user-1")].context
            self.assertEqual(context.root_execution_id, run.root_execution_id)
            self.assertIsInstance(context.subagent_state, dict)
            self.assertEqual(context.subagent_state["options"]["max_total_runs"], 1)
            _, recovered = await h.post({"session_id": "session-1",
                                         "tool_approval_status": [{"tool_call_id": "approval", "approved": True}]},
                                        path="/chat/completions/recover")
            snapshot = next(e["data"] for e in recovered if e["type"] == "data-subagent")
            old_events = [e["data"] for e in first if e["type"] == "data-subagent"]
            self.assertEqual(snapshot["root_execution_id"], run.root_execution_id)
            self.assertGreater(snapshot["sequence"], max(e["sequence"] for e in old_events))
            self.assertEqual(len(h.subagents.runs), 1)
            self.assertIn("recovered with original quota", "".join(e.get("delta", "") for e in recovered))

    async def test_lost_lease_interrupts_quiet_children_and_history_does_not_replay(self):
        child_started = asyncio.Event()
        async def script(request):
            if request.messages[0].content == "child role":
                child_started.set()
                await asyncio.Event().wait()
            events = call("subagent_wait", call_id="wait") if request.messages[-1].role == Role.TOOL else call(
                "subagent_start", {"task": "task", "instructions": "child role"})
            for event in events:
                yield event

        async with Harness(script) as h:
            h.add_session()
            pending = asyncio.create_task(h.post({"session_id": "session-1", "query": "hello", "subagents": {}}))
            await asyncio.wait_for(child_started.wait(), timeout=5)
            h.stream.active.clear()
            _, events = await asyncio.wait_for(pending, timeout=3)
            run = next(iter(h.subagents.runs.values()))
            self.assertEqual(run.status, "interrupted")
            count = len(h.provider.requests)
            # 模擬程序在寫入終態前死亡；歷史查詢只調整狀態，不重播任何模型或工具。
            run.status = "running"
            response = await h.client.get("/chat/subagents", params={"session_id": "session-1"})
            self.assertEqual(response.json()["data"]["runs"][0]["status"], "interrupted")
            self.assertEqual(len(h.provider.requests), count)
            self.assertTrue(any(e["type"] == "error" for e in events))


if __name__ == "__main__":
    unittest.main()
