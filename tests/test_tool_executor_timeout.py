"""Tests for tool execution timeout in ConcurrentToolExecutor."""
import asyncio
import pytest
from arf.engine.tool_executor import ConcurrentToolExecutor
from arf.core.results import ToolResult


class SlowResolver:
    """Resolver whose execute() can be configured to hang."""
    def __init__(self, delay: float = 10.0):
        self._delay = delay
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name: str, params: dict) -> ToolResult:
        self.calls.append((name, params))
        await asyncio.sleep(self._delay)
        return ToolResult(tool_name=name, success=True, data={"done": True})


@pytest.mark.anyio
async def test_tool_timeout_returns_error_result():
    """A tool that exceeds timeout returns ToolResult with error, not exception."""
    resolver = SlowResolver(delay=10.0)
    executor = ConcurrentToolExecutor(
        resolver,
        strategy="sequential",
        tool_timeout=0.5,  # 500ms timeout, tool sleeps 10s
    )

    results = await executor.execute([
        {"id": "slow_1", "name": "slow_tool", "params": {}}
    ])

    result = results["slow_1"]
    assert not result.success
    assert "timed out" in result.error.lower()
    assert result.blocked is False  # not a guard block, it's a timeout


@pytest.mark.anyio
async def test_tool_within_timeout_succeeds():
    """A tool that completes within timeout returns its real result."""
    resolver = SlowResolver(delay=0.01)
    executor = ConcurrentToolExecutor(
        resolver,
        strategy="sequential",
        tool_timeout=5.0,
    )

    results = await executor.execute([
        {"id": "fast_1", "name": "fast_tool", "params": {}}
    ])

    result = results["fast_1"]
    assert result.success
    assert result.data == {"done": True}


@pytest.mark.anyio
async def test_timeout_does_not_affect_other_tools():
    """When one tool times out, other parallel tools still complete."""
    resolver = SlowResolver(delay=0.01)
    # Override execute to make just one tool slow
    original_execute = resolver.execute

    call_count = [0]
    async def selective_slow(name, params):
        call_count[0] += 1
        if call_count[0] == 1:
            await asyncio.sleep(10.0)  # first tool hangs
            return ToolResult(tool_name=name, success=True, data={})
        return await original_execute(name, params)

    resolver.execute = selective_slow
    executor = ConcurrentToolExecutor(
        resolver,
        strategy="parallel",
        tool_timeout=0.5,
    )

    results = await executor.execute([
        {"id": "hanging", "name": "hanging_tool", "params": {}},
        {"id": "ok", "name": "ok_tool", "params": {}},
    ])

    assert not results["hanging"].success
    assert "timed out" in results["hanging"].error.lower()
    assert results["ok"].success
