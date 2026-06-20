"""Tests for ToolRegistry and minimal ToolExecutor."""
import pytest
from arf.tooling.registry import ToolRegistry
from arf.tooling.executor import ToolExecutor, ToolResult


def test_register_and_lookup():
    reg = ToolRegistry()

    async def echo(**params):
        return params

    reg.register("echo", {"name": "echo", "description": "echoes"}, echo)
    assert "echo" in reg
    assert reg.get("echo")["description"] == "echoes"
    assert reg.get_executor("echo") is echo


@pytest.mark.anyio
async def test_execute_single_tool():
    reg = ToolRegistry()

    async def echo(message="hi", **kw):
        return {"message": message}

    reg.register("echo", {"name": "echo", "description": ""}, echo)

    executor = ToolExecutor(reg)
    results = await executor.execute([{"id": "t1", "name": "echo", "params": {"message": "hello"}}])

    assert "t1" in results
    assert results["t1"].success
    assert results["t1"].data["message"] == "hello"


@pytest.mark.anyio
async def test_execute_missing_tool():
    reg = ToolRegistry()
    executor = ToolExecutor(reg)
    results = await executor.execute([{"id": "t1", "name": "nonexistent", "params": {}}])
    assert not results["t1"].success
    assert "not found" in results["t1"].error


@pytest.mark.anyio
async def test_execute_multiple_tools():
    reg = ToolRegistry()

    async def add(a=0, b=0, **kw):
        return {"result": a + b}

    async def mul(a=0, b=0, **kw):
        return {"result": a * b}

    reg.register("add", {"name": "add"}, add)
    reg.register("mul", {"name": "mul"}, mul)

    executor = ToolExecutor(reg)
    results = await executor.execute([
        {"id": "1", "name": "add", "params": {"a": 2, "b": 3}},
        {"id": "2", "name": "mul", "params": {"a": 4, "b": 5}},
    ])
    assert results["1"].data["result"] == 5
    assert results["2"].data["result"] == 20


@pytest.mark.anyio
async def test_execute_parallel():
    """Multiple tools should execute concurrently."""
    reg = ToolRegistry()
    order: list[str] = []

    async def slow(name="", delay=0.1, **kw):
        import asyncio
        await asyncio.sleep(delay)
        order.append(name)
        return {"name": name}

    reg.register("a", {"name": "a"}, slow)
    reg.register("b", {"name": "b"}, slow)

    executor = ToolExecutor(reg, timeout=5.0)
    results = await executor.execute([
        {"id": "1", "name": "a", "params": {"name": "A", "delay": 0.1}},
        {"id": "2", "name": "b", "params": {"name": "B", "delay": 0.1}},
    ])
    assert results["1"].success
    assert results["2"].success


@pytest.mark.anyio
async def test_register_batch():
    reg = ToolRegistry()

    async def echo(**params):
        return params

    async def greet(**params):
        return {"greeting": f"Hello, {params.get('name', 'World')}"}

    tools = [
        {"name": "echo", "description": "echo"},
        {"name": "greet", "description": "greet"},
    ]
    executor_map = {"echo": echo, "greet": greet}
    reg.register_batch(tools, executor_map)

    assert "echo" in reg
    assert "greet" in reg
    assert reg.get_executor("echo") is echo
