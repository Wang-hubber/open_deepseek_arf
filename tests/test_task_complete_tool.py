"""Tests for kernel__task_complete tool."""
import pytest


class TestTaskCompleteTool:
    @pytest.mark.anyio
    async def test_returns_task_complete_true_with_all_fields(self):
        from arf.skills.task_complete_tool import execute
        result = await execute(
            result="重构完成", files_changed={"modified": ["app.py"]},
            confidence=0.95, notes="所有测试通过",
        )
        assert result["ok"] is True
        assert result["task_complete"] is True
        assert result["result"] == "重构完成"
        assert result["files_changed"] == {"modified": ["app.py"]}
        assert result["confidence"] == 0.95
        assert result["notes"] == "所有测试通过"

    @pytest.mark.anyio
    async def test_defaults_for_minimal_call(self):
        from arf.skills.task_complete_tool import execute
        result = await execute()
        assert result["task_complete"] is True
        assert result["result"] == ""
        assert result["files_changed"] == {}
        assert result["confidence"] == 1.0
        assert result["notes"] == ""

    @pytest.mark.anyio
    async def test_confidence_clamped_to_0_1(self):
        from arf.skills.task_complete_tool import execute
        assert (await execute(confidence=2.5))["confidence"] == 1.0
        assert (await execute(confidence=-0.5))["confidence"] == 0.0
