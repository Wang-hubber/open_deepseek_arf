"""Tests for plan_solve plugin — dependency validation, tools, plugin hooks."""

import json
import tempfile
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock


# ============================================================
# Dependency graph validation
# ============================================================

class TestDependencyValidation:
    """plan_create step validation — indices, symmetry, cycles."""

    def test_valid_dag_passes(self):
        from arf.plugins.plan_solve.validation import validate_steps

        steps = [
            {"index": 1, "description": "read config", "depends_on": [], "blocks": [2]},
            {"index": 2, "description": "analyze", "depends_on": [1], "blocks": [3]},
            {"index": 3, "description": "report", "depends_on": [2], "blocks": []},
        ]
        result = validate_steps(steps)
        assert result["ok"] is True

    def test_duplicate_index_returns_error(self):
        from arf.plugins.plan_solve.validation import validate_steps

        steps = [
            {"index": 1, "description": "step 1", "depends_on": [], "blocks": []},
            {"index": 1, "description": "step 1 dup", "depends_on": [], "blocks": []},
        ]
        result = validate_steps(steps)
        assert result["ok"] is False
        assert "duplicate" in result["error"].lower()

    def test_invalid_depends_on_index_returns_error(self):
        from arf.plugins.plan_solve.validation import validate_steps

        steps = [
            {"index": 1, "description": "step 1", "depends_on": [99], "blocks": []},
        ]
        result = validate_steps(steps)
        assert result["ok"] is False
        assert "99" in result["error"]

    def test_invalid_blocks_index_returns_error(self):
        from arf.plugins.plan_solve.validation import validate_steps

        steps = [
            {"index": 1, "description": "step 1", "depends_on": [], "blocks": [42]},
        ]
        result = validate_steps(steps)
        assert result["ok"] is False
        assert "42" in result["error"]

    def test_asymmetric_dependency_returns_error(self):
        from arf.plugins.plan_solve.validation import validate_steps

        steps = [
            {"index": 1, "description": "step 1", "depends_on": [], "blocks": [2]},
            {"index": 2, "description": "step 2", "depends_on": [], "blocks": []},
            # step 1 blocks step 2, but step 2 doesn't depend on step 1
        ]
        result = validate_steps(steps)
        assert result["ok"] is False
        assert "symmetric" in result["error"].lower() or "symmetry" in result["error"].lower()

    def test_symmetric_dependency_passes(self):
        from arf.plugins.plan_solve.validation import validate_steps

        steps = [
            {"index": 1, "description": "step 1", "depends_on": [], "blocks": [2]},
            {"index": 2, "description": "step 2", "depends_on": [1], "blocks": []},
        ]
        result = validate_steps(steps)
        assert result["ok"] is True

    def test_circular_dependency_returns_error(self):
        from arf.plugins.plan_solve.validation import validate_steps

        steps = [
            {"index": 1, "description": "A", "depends_on": [2], "blocks": [2]},
            {"index": 2, "description": "B", "depends_on": [1], "blocks": [1]},
        ]
        result = validate_steps(steps)
        assert result["ok"] is False
        assert "circular" in result["error"].lower() or "cycle" in result["error"].lower()

    def test_self_dependency_returns_error(self):
        from arf.plugins.plan_solve.validation import validate_steps

        steps = [
            {"index": 1, "description": "step 1", "depends_on": [1], "blocks": []},
        ]
        result = validate_steps(steps)
        assert result["ok"] is False

    def test_empty_steps_returns_error(self):
        from arf.plugins.plan_solve.validation import validate_steps

        result = validate_steps([])
        assert result["ok"] is False

    def test_non_contiguous_indices_passes(self):
        """Non-contiguous indices (1, 3, 5) are fine as long as references are valid."""
        from arf.plugins.plan_solve.validation import validate_steps

        steps = [
            {"index": 1, "description": "step 1", "depends_on": [], "blocks": [3]},
            {"index": 3, "description": "step 3", "depends_on": [1], "blocks": []},
        ]
        result = validate_steps(steps)
        assert result["ok"] is True


# ============================================================
# plan_create tool
# ============================================================

class TestPlanCreate:
    """plan_create tool — writes plan.json to workspace."""

    @pytest.mark.anyio
    async def test_creates_plan_json_on_valid_steps(self):
        from arf.plugins.plan_solve.tools.plan_create.function import execute

        steps = [
            {"index": 1, "description": "read", "depends_on": [], "blocks": [2]},
            {"index": 2, "description": "write", "depends_on": [1], "blocks": []},
        ]
        result = await execute(
            task="test task",
            steps=steps,
            _workspace="/tmp/test_plan_solve",
        )
        assert result["ok"] is True
        assert result["plan_id"].startswith("plan-")
        assert len(result["steps"]) == 2

    @pytest.mark.anyio
    async def test_rejects_invalid_steps(self):
        from arf.plugins.plan_solve.tools.plan_create.function import execute

        result = await execute(
            task="test",
            steps=[{"index": 1, "description": "x", "depends_on": [99], "blocks": []}],
            _workspace="/tmp/test_plan_solve",
        )
        assert result["ok"] is False
        assert "99" in result["error"]

    @pytest.mark.anyio
    async def test_writes_plan_json_file(self):
        import json
        from pathlib import Path
        from arf.plugins.plan_solve.tools.plan_create.function import execute

        ws = "/tmp/test_plan_solve_create"
        steps = [
            {"index": 1, "description": "step1", "depends_on": [], "blocks": []},
        ]
        result = await execute(task="write test", steps=steps, _workspace=ws)

        plan_path = Path(ws) / "plan.json"
        assert plan_path.exists()
        plan = json.loads(plan_path.read_text())
        assert plan["status"] == "executing"
        assert len(plan["steps"]) == 1
        assert plan["steps"][0]["status"] == "pending"

    @pytest.mark.anyio
    async def test_all_steps_initialized_pending(self):
        from arf.plugins.plan_solve.tools.plan_create.function import execute

        steps = [
            {"index": 1, "description": "a", "depends_on": [], "blocks": [2]},
            {"index": 2, "description": "b", "depends_on": [1], "blocks": []},
        ]
        result = await execute(task="init test", steps=steps, _workspace="/tmp/test_plan_solve_init")
        for s in result["steps"]:
            assert s["status"] == "pending"

    @pytest.mark.anyio
    async def test_requires_task_non_empty(self):
        from arf.plugins.plan_solve.tools.plan_create.function import execute

        result = await execute(task="", steps=[], _workspace="/tmp/test_plan_solve")
        assert result.get("ok") is False or "error" in result


# ============================================================
# plan_dispatch tool
# ============================================================

class TestPlanDispatch:
    """plan_dispatch — precondition checks and sub-engine dispatch."""

    def _write_plan(self, workspace: str, steps: list[dict], status="executing"):
        import json
        from pathlib import Path
        plan = {
            "plan_id": "plan-test",
            "task": "test task",
            "status": status,
            "created_at": 0.0,
            "updated_at": 0.0,
            "steps": steps,
        }
        p = Path(workspace)
        p.mkdir(parents=True, exist_ok=True)
        (p / "plan.json").write_text(json.dumps(plan))

    @pytest.mark.anyio
    async def test_dispatch_blocked_by_pending_dependency(self):
        from arf.plugins.plan_solve.tools.plan_dispatch.function import execute

        ws = "/tmp/test_plan_solve_blocked"
        self._write_plan(ws, [
            {"index": 1, "description": "step1", "status": "pending", "depends_on": [], "blocks": [2], "sub_session_id": None, "result": None, "error": None},
            {"index": 2, "description": "step2", "status": "pending", "depends_on": [1], "blocks": [], "sub_session_id": None, "result": None, "error": None},
        ])
        result = await execute(step_index=2, _workspace=ws)
        assert result["ok"] is False
        assert "step1" in result.get("error", "").lower() or result.get("blocked_by")

    @pytest.mark.anyio
    async def test_dispatch_nonexistent_step(self):
        from arf.plugins.plan_solve.tools.plan_dispatch.function import execute

        ws = "/tmp/test_plan_solve_nonexist"
        self._write_plan(ws, [
            {"index": 1, "description": "only step", "status": "pending", "depends_on": [], "blocks": [], "sub_session_id": None, "result": None, "error": None},
        ])
        result = await execute(step_index=99, _workspace=ws)
        assert result["ok"] is False

    @pytest.mark.anyio
    async def test_dispatch_already_completed_step(self):
        from arf.plugins.plan_solve.tools.plan_dispatch.function import execute

        ws = "/tmp/test_plan_solve_done"
        self._write_plan(ws, [
            {"index": 1, "description": "step1", "status": "done", "depends_on": [], "blocks": [], "sub_session_id": "sub_x", "result": {"content": "ok"}, "error": None},
        ])
        result = await execute(step_index=1, _workspace=ws)
        assert result["ok"] is False

    @pytest.mark.anyio
    async def test_dispatch_when_dependency_is_done_succeeds(self):
        from unittest.mock import AsyncMock, MagicMock
        from arf.plugins.plan_solve.tools.plan_dispatch.function import execute

        ws = "/tmp/test_plan_solve_dispatch_ok"
        self._write_plan(ws, [
            {"index": 1, "description": "done step", "status": "done", "depends_on": [], "blocks": [2], "sub_session_id": "sub_1", "result": {"content": "result1"}, "error": None},
            {"index": 2, "description": "to dispatch", "status": "pending", "depends_on": [1], "blocks": [], "sub_session_id": None, "result": None, "error": None},
        ])

        # Mock engine with call_model (text-only response, exits loop)
        mock_engine = MagicMock()
        mock_engine._call_model = AsyncMock(return_value={"content": "sub result", "tool_calls": []})
        mock_engine.event_bus = MagicMock()
        mock_engine.state_store = MagicMock()
        mock_engine.state_store.put = AsyncMock()
        mock_engine.state_store.get = AsyncMock(return_value={
            "messages": [{"role": "assistant", "content": "sub result"}],
        })

        result = await execute(step_index=2, _engine=mock_engine, _workspace=ws)
        assert result["ok"] is True
        assert "sub result" in result.get("content", "")


# ============================================================
# plan_summarize tool
# ============================================================

class TestPlanSummarize:
    """plan_summarize — all steps must be done/failed before summarizing."""

    def _write_plan(self, workspace: str, steps: list[dict]):
        import json
        from pathlib import Path
        plan = {
            "plan_id": "plan-test", "task": "test", "status": "executing",
            "created_at": 0.0, "updated_at": 0.0, "steps": steps,
        }
        p = Path(workspace)
        p.mkdir(parents=True, exist_ok=True)
        (p / "plan.json").write_text(json.dumps(plan))

    @pytest.mark.anyio
    async def test_rejects_when_steps_pending(self):
        from arf.plugins.plan_solve.tools.plan_summarize.function import execute

        ws = "/tmp/test_plan_solve_summarize_pending"
        self._write_plan(ws, [
            {"index": 1, "description": "done", "status": "done", "depends_on": [], "blocks": [], "sub_session_id": "s1", "result": {"content": "ok"}, "error": None},
            {"index": 2, "description": "pending", "status": "pending", "depends_on": [], "blocks": [], "sub_session_id": None, "result": None, "error": None},
        ])
        result = await execute(_workspace=ws)
        assert result["ok"] is False
        assert "pending_steps" in result or "pending" in result.get("error", "").lower()

    @pytest.mark.anyio
    async def test_allows_when_all_done_or_failed(self):
        from unittest.mock import AsyncMock, MagicMock
        from arf.plugins.plan_solve.tools.plan_summarize.function import execute

        ws = "/tmp/test_plan_solve_summarize_ok"
        self._write_plan(ws, [
            {"index": 1, "description": "done", "status": "done", "depends_on": [], "blocks": [], "sub_session_id": "s1", "result": {"content": "result1"}, "error": None},
            {"index": 2, "description": "failed", "status": "failed", "depends_on": [], "blocks": [], "sub_session_id": "s2", "result": None, "error": "crashed"},
        ])

        mock_engine = MagicMock()
        mock_engine._call_model = AsyncMock(return_value={"content": "summary of results", "tool_calls": []})

        result = await execute(_engine=mock_engine, _workspace=ws)
        assert result["ok"] is True
        assert "summary" in result

    @pytest.mark.anyio
    async def test_no_plan_returns_error(self):
        from arf.plugins.plan_solve.tools.plan_summarize.function import execute

        result = await execute(_workspace="/tmp/no_plan_here")
        assert result["ok"] is False


# ============================================================
# plan_status tool
# ============================================================

class TestPlanStatus:
    """plan_status — read-only progress snapshot."""

    @pytest.mark.anyio
    async def test_returns_progress_snapshot(self):
        import json
        from pathlib import Path
        from arf.plugins.plan_solve.tools.plan_status.function import execute

        ws = "/tmp/test_plan_solve_status"
        plan = {
            "plan_id": "plan-st", "task": "test", "status": "executing",
            "created_at": 0.0, "updated_at": 0.0,
            "steps": [
                {"index": 1, "description": "step1", "status": "done", "depends_on": [], "blocks": [2], "sub_session_id": "s1", "result": {"content": "ok"}, "error": None},
                {"index": 2, "description": "step2", "status": "pending", "depends_on": [1], "blocks": [], "sub_session_id": None, "result": None, "error": None},
            ],
        }
        p = Path(ws)
        p.mkdir(parents=True, exist_ok=True)
        (p / "plan.json").write_text(json.dumps(plan))

        result = await execute(_workspace=ws)
        assert result["ok"] is True
        assert result["plan_id"] == "plan-st"
        assert result["status"] == "executing"
        assert len(result["steps"]) == 2

    @pytest.mark.anyio
    async def test_no_plan_returns_empty(self):
        from arf.plugins.plan_solve.tools.plan_status.function import execute

        result = await execute(_workspace="/tmp/no_plan_status")
        assert result["ok"] is False


# ============================================================
# PlanSolvePlugin hooks
# ============================================================

class TestPlanSolvePlugin:
    """PlanSolvePlugin — pre_action contract validation, round_start plan_resumable."""

    def _write_plan(self, workspace: str, steps: list[dict], status="executing"):
        import json
        from pathlib import Path
        plan = {
            "plan_id": "plan-p", "task": "t", "status": status,
            "created_at": 0.0, "updated_at": 0.0, "steps": steps,
        }
        p = Path(workspace)
        p.mkdir(parents=True, exist_ok=True)
        (p / "plan.json").write_text(json.dumps(plan))

    def test_hooks_declared(self):
        from arf.plugins.plan_solve.plugin import PlanSolvePlugin
        p = PlanSolvePlugin()
        assert p.name == "plan_solve"
        assert "pre_action" in p.hooks
        assert "round_start" in p.hooks

    @pytest.mark.anyio
    async def test_pre_action_blocks_blocked_dispatch(self):
        from arf.plugins.plan_solve.plugin import PlanSolvePlugin
        from arf.core.plugin_context import PluginContext

        ws = "/tmp/test_plan_solve_plugin_block"
        self._write_plan(ws, [
            {"index": 1, "description": "s1", "status": "pending", "depends_on": [], "blocks": [2], "sub_session_id": None, "result": None, "error": None, "tool_hint": ""},
            {"index": 2, "description": "s2", "status": "pending", "depends_on": [1], "blocks": [], "sub_session_id": None, "result": None, "error": None, "tool_hint": ""},
        ])

        plugin = PlanSolvePlugin({"workspace_dir": ws})
        ctx = PluginContext(
            workspace_dir=ws,
            state={"messages": [], "_pending_tool_calls": [
                {"id": "call_1", "name": "plan_solve__plan_dispatch", "params": {"step_index": 2}},
            ]},
            session_id="test",
            interaction_round=1,
            turn=1,
            current_step="",
            messages=[],
            tool_definitions=[],
            system_prompt="",
            model="",
        )

        await plugin.on_hook("pre_action", ctx)
        # Tool should be removed from _pending_tool_calls after injection
        remaining = ctx.state.get("_pending_tool_calls", [])
        assert len(remaining) == 0, f"Blocked dispatch should be removed from pending: {remaining}"

    @pytest.mark.anyio
    async def test_pre_action_allows_valid_dispatch(self):
        from arf.plugins.plan_solve.plugin import PlanSolvePlugin
        from arf.core.plugin_context import PluginContext

        ws = "/tmp/test_plan_solve_plugin_allow"
        self._write_plan(ws, [
            {"index": 1, "description": "s1", "status": "done", "depends_on": [], "blocks": [2], "sub_session_id": "s1", "result": {"content": "ok"}, "error": None, "tool_hint": ""},
            {"index": 2, "description": "s2", "status": "pending", "depends_on": [1], "blocks": [], "sub_session_id": None, "result": None, "error": None, "tool_hint": ""},
        ])

        plugin = PlanSolvePlugin({"workspace_dir": ws})
        ctx = PluginContext(
            workspace_dir=ws,
            state={"messages": [], "_pending_tool_calls": [
                {"id": "call_1", "name": "plan_solve__plan_dispatch", "params": {"step_index": 2}},
            ]},
            session_id="test", interaction_round=1, turn=1,
            current_step="", messages=[], tool_definitions=[],
            system_prompt="", model="",
        )

        await plugin.on_hook("pre_action", ctx)
        # With dep satisfied, tool should NOT be removed (plugin passes through)
        remaining = ctx.state.get("_pending_tool_calls", [])
        assert len(remaining) == 1, f"Valid dispatch should stay in pending: {remaining}"

    @pytest.mark.anyio
    async def test_round_start_emits_plan_resumable(self):
        from arf.plugins.plan_solve.plugin import PlanSolvePlugin
        from arf.core.plugin_context import PluginContext

        ws = "/tmp/test_plan_solve_plugin_resume"
        self._write_plan(ws, [
            {"index": 1, "description": "done", "status": "done", "depends_on": [], "blocks": [], "sub_session_id": "s1", "result": {"content": "ok"}, "error": None, "tool_hint": ""},
            {"index": 2, "description": "pending", "status": "pending", "depends_on": [], "blocks": [], "sub_session_id": None, "result": None, "error": None, "tool_hint": ""},
        ])

        emitted = []
        plugin = PlanSolvePlugin({"workspace_dir": ws})
        ctx = PluginContext(
            workspace_dir=ws,
            state={},
            session_id="test", interaction_round=1, turn=0,
            current_step="", messages=[], tool_definitions=[],
            system_prompt="", model="",
        )
        # Mock emit on ctx
        ctx.emit = lambda event_type, data: emitted.append((event_type, data))

        await plugin.on_hook("round_start", ctx)
        assert len(emitted) == 1
        assert emitted[0][0] == "plan_resumable"
        assert emitted[0][1]["plan_id"] == "plan-p"
