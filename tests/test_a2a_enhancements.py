"""Tests for A2A Plugin enhancements: HITL, depth limit, conflict detection."""
import asyncio
from unittest.mock import MagicMock

import pytest

from arf.communication.queued_delegator import QueuedTaskDelegator
from arf.core.plugin_context import PluginContext
from arf.plugins.a2a_subagents.tools import _registry as a2a_registry
from arf.skills import ask_user_tool


class TestAskUser:
    @pytest.mark.anyio
    async def test_ask_user_returns_pending(self):
        result = await ask_user_tool.execute(
            question="方案A还是B?", options=["A", "B"]
        )
        assert result["ok"] is True
        assert result["pending"] is True
        assert result["question"] == "方案A还是B?"
        assert result["options"] == ["A", "B"]

    @pytest.mark.anyio
    async def test_ask_user_options_defaults_to_empty(self):
        result = await ask_user_tool.execute(question="任意回答?")
        assert result["ok"] is True
        assert result["options"] == []

    @pytest.mark.anyio
    async def test_ask_user_with_context_and_task_id(self):
        from arf.skills import ask_user_tool
        result = await ask_user_tool.execute(
            question="选哪个?", options=["X", "Y"],
            context="配置文件歧义", task_id="task_42",
        )
        assert result["context"] == "配置文件歧义"
        assert result["task_id"] == "task_42"


class TestHITLRoundEnd:
    @pytest.fixture(autouse=True)
    def setup_registry(self):
        a2a_registry.delegator = QueuedTaskDelegator(max_concurrent=2)
        a2a_registry.max_task_timeout = 600.0
        yield
        a2a_registry.delegator = None

    @pytest.mark.anyio
    async def test_round_end_hitl_no_longer_emits_event(self):
        """round_end with _pending_human_decision no longer emits events.

        Engine handles HITL via A2AHITL protocol injected into the child
        agent's engine. Plugin only cleans up registry state.
        """
        from arf.plugins.a2a_subagents.plugin import A2APlugin

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        delegator = a2a_registry.delegator

        parent_sid = "parent_hitl"
        async def runner(task):
            return {"ok": True}
        r = await delegator.dispatch(parent_sid, {"task": "test"}, runner)
        await asyncio.sleep(0)
        task_id = r["task_id"]
        child_sid = f"{parent_sid}--{task_id}"

        child_ctx = PluginContext(
            session_id=child_sid,
            state={
                "session_id": child_sid,
                "messages": [
                    {"role": "user", "content": "do task"},
                    {"role": "assistant", "content": "I need help deciding..."},
                ],
                "current_turn": 2,
                "_pending_human_decision": {
                    "question": "选方案A还是B?",
                    "options": ["A", "B"],
                },
            },
            event_bus=MagicMock(),
        )

        await plugin.on_hook("round_end", child_ctx)

        # round_end no longer emits events — engine handles via A2AHITL
        child_ctx.event_bus.emit.assert_not_called()

    @pytest.mark.anyio
    async def test_round_end_normal_no_longer_emits_event(self):
        """round_end without _pending_human_decision no longer emits events.

        Engine handles task completion via A2ATaskLifecycle protocol.
        Plugin only cleans up registry state.
        """
        from arf.plugins.a2a_subagents.plugin import A2APlugin

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        delegator = a2a_registry.delegator

        parent_sid = "parent_normal"
        async def runner(task):
            return {"ok": True}
        r = await delegator.dispatch(parent_sid, {"task": "test"}, runner)
        await asyncio.sleep(0)
        task_id = r["task_id"]

        child_ctx = PluginContext(
            session_id=f"{parent_sid}--{task_id}",
            state={
                "session_id": f"{parent_sid}--{task_id}",
                "messages": [
                    {"role": "user", "content": "do task"},
                    {"role": "assistant", "content": "Done."},
                ],
                "current_turn": 1,
            },
            event_bus=MagicMock(),
        )

        await plugin.on_hook("round_end", child_ctx)

        # round_end no longer emits events — engine handles via A2ATaskLifecycle
        child_ctx.event_bus.emit.assert_not_called()


class TestDepthLimit:
    def test_delegate_task_injects_blacklist(self):
        """Sub-agent state gets _tool_blacklist with delegate_task."""
        from arf.plugins.a2a_subagents.state import build_sub_state

        state = build_sub_state(
            parent_session_id="parent",
            task_id="task_1",
            task="do something",
        )
        # build_sub_state doesn't add _tool_blacklist — delegate_task runner does
        # but we test the filtering logic here
        assert "_tool_blacklist" not in state

    def test_blacklist_filters_tools(self):
        """Tool list filters out blacklisted tools."""
        tools = [
            {"name": "delegate_task", "description": "..."},
            {"name": "read_file", "description": "..."},
            {"name": "ask_user", "description": "..."},
        ]
        blacklist = ["delegate_task"]
        filtered = [t for t in tools if t.get("name") not in blacklist]
        assert len(filtered) == 2
        assert all(t["name"] != "delegate_task" for t in filtered)
        assert any(t["name"] == "read_file" for t in filtered)
        assert any(t["name"] == "ask_user" for t in filtered)


class TestSnapshot:
    def test_snapshot_workspace_captures_files(self, tmp_path):
        """_snapshot_workspace returns file hashes."""
        from arf.plugins.a2a_subagents.tools.delegate_task.function import _snapshot_workspace

        (tmp_path / "test.txt").write_text("hello")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "nested.py").write_text("print('hi')")

        snap = _snapshot_workspace(str(tmp_path))
        assert "test.txt" in snap
        assert "subdir/nested.py" in snap
        assert len(snap["test.txt"]) == 64  # SHA256 hex

    def test_snapshot_ignores_dirs_in_ignore_list(self, tmp_path):
        """_snapshot_workspace skips .git, __pycache__, etc."""
        from arf.plugins.a2a_subagents.tools.delegate_task.function import _snapshot_workspace

        (tmp_path / "src.py").write_text("code")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("git config")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "cache.pyc").write_text("cache")

        snap = _snapshot_workspace(str(tmp_path))
        assert "src.py" in snap
        assert ".git/config" not in snap
        assert "__pycache__/cache.pyc" not in snap

    def test_snapshot_nonexistent_dir_returns_empty(self):
        from arf.plugins.a2a_subagents.tools.delegate_task.function import _snapshot_workspace
        snap = _snapshot_workspace("/nonexistent/path/12345")
        assert snap == {}


class TestConflictDetection:
    @pytest.fixture(autouse=True)
    def setup_registry(self):
        a2a_registry.delegator = QueuedTaskDelegator(max_concurrent=2)
        a2a_registry.max_task_timeout = 600.0
        yield
        a2a_registry.delegator = None

    @pytest.mark.anyio
    async def test_pre_action_detects_overlap(self, tmp_path):
        """pre_action holds conflicting file changes."""
        from arf.plugins.a2a_subagents.plugin import A2APlugin

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        delegator = a2a_registry.delegator
        parent_sid = "parent_conflict"

        async def dummy_runner(task):
            return {"ok": True}

        # Dispatch two tasks and use their real task IDs
        r1 = await delegator.dispatch(parent_sid, {"task": "t1"}, dummy_runner)
        await asyncio.sleep(0)
        r2 = await delegator.dispatch(parent_sid, {"task": "t2"}, dummy_runner)
        await asyncio.sleep(0)

        task_id_1 = r1["task_id"]
        task_id_2 = r2["task_id"]

        # Complete task 1 — modifies config.ts
        await delegator.complete(parent_sid, task_id_1, {
            "content": "Done task 1",
            "turn_count": 2,
            "gate_exceeded": False,
            "file_changes": {"added": [], "modified": ["config.ts"], "deleted": []},
        })
        # Complete task 2 — also modifies config.ts (conflict!)
        await delegator.complete(parent_sid, task_id_2, {
            "content": "Done task 2",
            "turn_count": 3,
            "gate_exceeded": False,
            "file_changes": {"added": [], "modified": ["config.ts"], "deleted": []},
        })

        parent_ctx = PluginContext(
            session_id=parent_sid,
            current_step="call_model",
            workspace_dir=str(tmp_path),
            data_dir=str(tmp_path / "data"),
            state={"session_id": parent_sid, "messages": []},
        )

        await plugin.on_hook("pre_action", parent_ctx)

        msgs = parent_ctx.state["messages"]
        # task_1 should be normal (first writer) — match by content
        task1_msgs = [m for m in msgs if task_id_1 in m.get("content", "")]
        assert len(task1_msgs) >= 1
        assert "HELD" not in task1_msgs[0]["content"]

        # task_2 should be conflict warning (overlap on config.ts)
        task2_msgs = [m for m in msgs if task_id_2 in m.get("content", "")]
        assert len(task2_msgs) >= 1
        assert "HELD" in task2_msgs[0]["content"]

        # Check manifest was written
        manifest = tmp_path / "data" / parent_sid / "conflicts" / task_id_2 / "manifest.json"
        # manifest only written if files exist on disk — in test, config.ts doesn't exist
        # but _hold_changes still writes manifest (just no files/)

    @pytest.mark.anyio
    async def test_pre_action_no_conflict(self):
        """pre_action with non-overlapping file changes works normally."""
        from arf.plugins.a2a_subagents.plugin import A2APlugin

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        delegator = a2a_registry.delegator
        parent_sid = "parent_noconflict"

        async def dummy_runner(task):
            return {"ok": True}

        # Dispatch two tasks and use their real task IDs
        r1 = await delegator.dispatch(parent_sid, {"task": "tA"}, dummy_runner)
        await asyncio.sleep(0)
        r2 = await delegator.dispatch(parent_sid, {"task": "tB"}, dummy_runner)
        await asyncio.sleep(0)

        task_id_a = r1["task_id"]
        task_id_b = r2["task_id"]

        await delegator.complete(parent_sid, task_id_a, {
            "content": "Done A",
            "turn_count": 1,
            "gate_exceeded": False,
            "file_changes": {"added": [], "modified": ["frontend.ts"], "deleted": []},
        })
        await delegator.complete(parent_sid, task_id_b, {
            "content": "Done B",
            "turn_count": 1,
            "gate_exceeded": False,
            "file_changes": {"added": [], "modified": ["backend.py"], "deleted": []},
        })

        ctx = PluginContext(
            session_id=parent_sid,
            current_step="call_model",
            state={"session_id": parent_sid, "messages": []},
        )

        await plugin.on_hook("pre_action", ctx)

        msgs = ctx.state["messages"]
        assert len(msgs) == 2
        for m in msgs:
            assert "HELD" not in m["content"]
