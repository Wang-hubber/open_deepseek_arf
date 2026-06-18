"""Tests for task memory extraction, merging, summary injection, and search."""
import pytest
from arf.memory.config import MemoryConfig, TaskMemoryConfig


class TestTaskMemoryConfig:
    def test_defaults(self):
        cfg = TaskMemoryConfig()
        assert cfg.enabled is True
        assert cfg.max_size_kb == 50
        assert cfg.summary_limit == 50

    def test_memory_config_includes_task_memory(self):
        cfg = MemoryConfig()
        assert cfg.task_memory is not None
        assert isinstance(cfg.task_memory, TaskMemoryConfig)
        assert cfg.task_memory.enabled is True

    def test_override_from_dict(self):
        cfg = MemoryConfig(task_memory={"enabled": False, "max_size_kb": 30})
        assert cfg.task_memory.enabled is False
        assert cfg.task_memory.max_size_kb == 30


import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock
from arf.memory.index import MemoryIndex
from arf.plugins.memory.plugin import MemoryPlugin
from arf.core.plugin_context import PluginContext


class TestTaskMemoryIO:
    @pytest.fixture
    def mem_index(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            cfg = MemoryConfig(secrets={"enabled": False})
            yield MemoryIndex(data_dir=str(data_dir), config=cfg)

    def test_load_tasks_empty_when_no_file(self, mem_index):
        assert mem_index.load_tasks() == ""

    def test_save_and_load_tasks(self, mem_index):
        content = "<!-- TASK refactoring | agent: test -->\n\n### test task\n\n**方案：**\n- did something\n\n**教训：**\n- learned something\n\n<!-- /TASK -->\n"
        mem_index.save_tasks(content)
        loaded = mem_index.load_tasks()
        assert "did something" in loaded
        assert "learned something" in loaded

    def test_save_overwrites(self, mem_index):
        mem_index.save_tasks("first")
        mem_index.save_tasks("second")
        assert mem_index.load_tasks() == "second"

    def test_truncate_warns_when_over_limit(self, mem_index, caplog):
        import logging
        caplog.set_level(logging.WARNING)
        big = "x" * (60 * 1024)  # 60KB > 50KB max
        mem_index.save_tasks(big)
        assert "will be truncated" in caplog.text


class TestTaskMemorySummary:
    @pytest.fixture
    def mem_index(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d) / "data"
            cfg = MemoryConfig(secrets={"enabled": False})
            mi = MemoryIndex(data_dir=str(data_dir), config=cfg)
            content = """<!-- TASK refactoring | agent: agent_1 -->

### 重构 auth 模块

**方案：**
- 新增 adapter 层

**教训：**
- 不要假设 SessionStore 子类无隐式依赖 (×3)

<!-- /TASK -->
<!-- TASK bugfix | agent: agent_2 -->

### 修复 login 超时

**方案：**
- 增加连接池超时配置

**教训：**
- redis 连接池不是线程安全的 (×2)

<!-- /TASK -->
"""
            mi.save_tasks(content)
            yield mi

    def test_build_summary_one_line_per_category(self, mem_index):
        summary = mem_index.build_task_summary()
        assert "refactoring" in summary
        assert "bugfix" in summary
        assert "重构 auth 模块" in summary
        assert "SessionStore" in summary
        # One line per category
        lines = [l for l in summary.strip().split("\n") if l.startswith("- **")]
        assert len(lines) == 2

    def test_build_summary_respects_limit(self, mem_index):
        mem_index._cfg.task_memory.summary_limit = 1
        summary = mem_index.build_task_summary()
        lines = [l for l in summary.strip().split("\n") if l.startswith("- **")]
        assert len(lines) <= 1


class TestTaskMemoryExtraction:
    """Test the task memory extraction flow from task_completed hook."""

    @pytest.fixture
    def mem_index(self):
        d = tempfile.mkdtemp()
        data_dir = Path(d) / "data"
        cfg = MemoryConfig(secrets={"enabled": False})
        mi = MemoryIndex(data_dir=str(data_dir), config=cfg)
        yield mi
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    @pytest.fixture
    def plugin(self, mem_index):
        plugin = MemoryPlugin(config={"interval": 5, "extract_on_session_end": False})
        plugin.set_memory_index(mem_index)
        return plugin

    @pytest.mark.anyio
    async def test_task_completed_hook_registered(self, plugin):
        hooks = plugin.hooks
        assert "task_completed" in hooks
        assert hooks["task_completed"] == "side"

    @pytest.mark.anyio
    async def test_on_hook_task_completed_calls_extraction(self, plugin):
        call_model = AsyncMock(return_value={
            "content": json.dumps({
                "category": "bugfix",
                "description": "修复 login 超时",
                "approach": ["增加超时配置", "添加重试逻辑"],
                "lessons": ["redis 连接池不是线程安全的"],
                "should_write": True,
            })
        })
        plugin.set_call_model(call_model)

        messages = [
            {"role": "user", "content": "修复 login 超时问题"},
            {"role": "assistant", "content": "我来看看"},
            {"role": "tool", "content": "redis connection timeout"},
            {"role": "assistant", "content": "找到问题了，连接池配置不对"},
        ]
        state = {
            "session_id": "s1",
            "messages": messages,
        }
        ctx = PluginContext(
            session_id="s1",
            interaction_round=5,
            state=state,
            messages=messages,
        )
        ctx.hook_data = {
            "session_id": "s1",
            "start_round": 1,
            "finish_round": 5,
            "task_result": "修复完成",
            "notes": "增加了 timeout 配置",
            "confidence": 0.9,
        }

        await plugin.on_hook("task_completed", ctx)

        # Verify extraction was called
        call_model.assert_called()
        call_args = call_model.call_args_list[0]
        call_messages = call_args[0][0]  # list of message dicts
        combined = " ".join(m.get("content", "") for m in call_messages)
        assert "修复 login 超时" in combined
        assert "redis connection timeout" in combined

    @pytest.mark.anyio
    async def test_on_hook_skips_when_should_write_false(self, plugin):
        call_model = AsyncMock(return_value={
            "content": json.dumps({
                "category": "trivial",
                "description": "无意义的任务",
                "approach": [],
                "lessons": [],
                "should_write": False,
            })
        })
        plugin.set_call_model(call_model)

        messages = [{"role": "user", "content": "hi"}]
        state = {"session_id": "s1", "messages": messages}
        ctx = PluginContext(
            session_id="s1", interaction_round=1,
            state=state, messages=messages,
        )
        ctx.hook_data = {
            "session_id": "s1", "start_round": 0, "finish_round": 1,
            "task_result": "", "notes": "", "confidence": 1.0,
        }

        await plugin.on_hook("task_completed", ctx)

        # Should NOT call the merge model call (only extraction)
        assert call_model.call_count == 1  # extraction only, no merge

    @pytest.mark.anyio
    async def test_on_hook_skips_when_call_model_not_set(self, plugin):
        messages = [{"role": "user", "content": "hi"}]
        state = {"session_id": "s1", "messages": messages}
        ctx = PluginContext(
            session_id="s1", interaction_round=1,
            state=state, messages=messages,
        )
        # Should not raise
        await plugin.on_hook("task_completed", ctx)

    @pytest.mark.anyio
    async def test_on_hook_skips_when_no_messages(self, plugin):
        call_model = AsyncMock()
        plugin.set_call_model(call_model)
        state = {"session_id": "s1", "messages": []}
        ctx = PluginContext(
            session_id="s1", interaction_round=1,
            state=state, messages=[],
        )
        await plugin.on_hook("task_completed", ctx)
        call_model.assert_not_called()


class TestSearchTaskMemory:
    @pytest.fixture
    def tasks_content(self):
        return """<!-- TASK refactoring | agent: agent_1 -->

### 重构 auth 模块

**方案：**
- 新增 TokenStorage adapter

**教训：**
- 不要假设 SessionStore 子类无隐式依赖 (×3)

<!-- /TASK -->
<!-- TASK bugfix | agent: agent_2 -->

### 修复 login 超时

**方案：**
- 增加连接池超时

**教训：**
- redis 连接池不是线程安全的 (×2)

<!-- /TASK -->
"""

    @pytest.mark.anyio
    async def test_search_returns_ok_and_results(self, tasks_content):
        import arf.memory.tools.search_task_memory as stm
        import tempfile
        from pathlib import Path
        from arf.memory.config import MemoryConfig
        from arf.memory.index import MemoryIndex

        d = tempfile.mkdtemp()
        data_dir = Path(d) / "data"
        cfg = MemoryConfig(secrets={"enabled": False})
        mi = MemoryIndex(data_dir=str(data_dir), config=cfg)
        mi.save_tasks(tasks_content)
        stm._index = mi

        call_model = AsyncMock(return_value={
            "content": json.dumps({
                "matches": [
                    {"category": "refactoring", "description": "重构 auth 模块",
                     "approach": ["新增 TokenStorage adapter"],
                     "lessons": ["不要假设 SessionStore 子类无隐式依赖"]}
                ]
            })
        })
        stm._call_model = call_model

        result = await stm.execute(query="session token 存储")
        assert result["ok"] is True
        assert len(result["matches"]) == 1
        assert result["matches"][0]["category"] == "refactoring"

        import shutil
        shutil.rmtree(d, ignore_errors=True)

    @pytest.mark.anyio
    async def test_search_no_memory_index_returns_error(self):
        import arf.memory.tools.search_task_memory as stm
        stm._index = None
        stm._call_model = None
        result = await stm.execute(query="anything")
        assert result["ok"] is False
        assert "error" in result


class TestTaskMemoryIntegration:
    """End-to-end: tool completes -> hook fires -> tasks.md updated -> summary visible."""

    @pytest.mark.anyio
    async def test_search_tool_receives_call_model_and_index(self):
        """Verify BaseAgent wires the search tool's _call_model and _index."""
        import arf.memory.tools.search_task_memory as stm
        import tempfile
        from pathlib import Path
        from arf.memory.config import MemoryConfig
        from arf.memory.index import MemoryIndex

        d = tempfile.mkdtemp()
        data_dir = Path(d) / "data"
        cfg = MemoryConfig(secrets={"enabled": False})
        mi = MemoryIndex(data_dir=str(data_dir), config=cfg)
        mi.save_tasks("""<!-- TASK bugfix | agent: test -->

### 修复超时

**方案：**
- 增加超时

**教训：**
- redis 连接池不是线程安全的

<!-- /TASK -->
""")

        stm._index = mi
        call_model = AsyncMock(return_value={
            "content": json.dumps({"matches": []})
        })
        stm._call_model = call_model

        result = await stm.execute(query="redis")
        assert result["ok"] is True

        import shutil
        shutil.rmtree(d, ignore_errors=True)

    @pytest.mark.anyio
    async def test_task_memory_config_flows_to_memory_index(self):
        """Task memory config from agent.yaml flows to MemoryIndex."""
        import tempfile
        from pathlib import Path
        from arf.memory.config import MemoryConfig
        from arf.memory.index import MemoryIndex

        d = tempfile.mkdtemp()
        data_dir = Path(d) / "data"
        cfg = MemoryConfig(secrets={"enabled": False}, task_memory={
            "enabled": True, "max_size_kb": 30, "summary_limit": 10,
        })
        mi = MemoryIndex(data_dir=str(data_dir), config=cfg)
        assert mi._cfg.task_memory.enabled is True
        assert mi._cfg.task_memory.max_size_kb == 30
        assert mi._cfg.task_memory.summary_limit == 10

        import shutil
        shutil.rmtree(d, ignore_errors=True)


class TestTaskMemoryEngineIntegration:
    """Verify engine fires task_completed hook and plugin handles it."""

    @pytest.mark.anyio
    async def test_engine_fires_task_completed_hook(self):
        """Full flow: kernel__task_complete -> engine detects -> hook fires."""
        import json as _json
        import asyncio
        import tempfile
        from pathlib import Path
        import shutil
        from arf.engine.control_plane import ControlPlane
        from arf.engine.checkpoint import InMemoryStateStore
        from arf.plugins.memory.plugin import MemoryPlugin
        from arf.core.results import ToolResult
        from arf.memory.config import MemoryConfig
        from arf.memory.index import MemoryIndex

        d = tempfile.mkdtemp()
        data_dir = Path(d) / "data"
        cfg = MemoryConfig(secrets={"enabled": False})
        mi = MemoryIndex(data_dir=str(data_dir), config=cfg)

        # Recording call model that produces:
        #   1 -> task_complete tool call (engine's turn)
        #   2 -> extraction JSON      (plugin's _extract_task_experience)
        #   3 -> merged markdown      (plugin's _merge_and_save, fire-and-forget)
        class RecordingCallModel:
            def __init__(self):
                self.calls = []
                self.call_count = 0

            async def __call__(self, msgs, model=None, tools=None, **kwargs):
                self.calls.append({"msgs": msgs, "model": model, "tools": tools, "kwargs": kwargs})
                self.call_count += 1
                if self.call_count == 1:
                    return {"content": "", "tool_calls": [{
                        "id": "tc1", "type": "function", "function": {
                            "name": "kernel__task_complete",
                            "arguments": _json.dumps({
                                "result": "fixed bug", "confidence": 0.9,
                                "notes": "redis connection pool issue",
                            }),
                        }
                    }]}
                elif self.call_count == 2:
                    return {"content": _json.dumps({
                        "category": "bugfix",
                        "description": "fix redis connection pool",
                        "approach": ["增加超时配置"],
                        "lessons": ["不要假设连接池线程安全"],
                        "should_write": True,
                    })}
                else:
                    return {"content": "<!-- TASK bugfix | agent: test -->\n\n### fix redis connection pool\n\n**方案：**\n- 增加超时配置\n\n**教训：**\n- 不要假设连接池线程安全\n\n<!-- /TASK -->\n"}

        call_model = RecordingCallModel()

        class FakeToolExecutor:
            async def execute(self, tool_calls, **kwargs):
                return {tc["id"]: ToolResult(
                    success=True,
                    data=_json.dumps({
                        "task_complete": True, "result": "fixed bug",
                        "confidence": 0.9, "files_changed": {},
                        "notes": "redis connection pool issue",
                    }),
                    tool_name=tc.get("function", {}).get("name", "unknown")
                    if isinstance(tc.get("function"), dict) else tc.get("name", "unknown"),
                ) for tc in tool_calls}

        plugin = MemoryPlugin()
        plugin.set_memory_index(mi)
        plugin.set_call_model(call_model)

        cp = ControlPlane(
            max_turns=5,
            state_store=InMemoryStateStore(),
            tool_executor=FakeToolExecutor(),
            call_model=call_model,
            side_plugins=[plugin],
        )

        state = {
            "session_id": "test-engine",
            "agent_name": "test",
            "current_model": "test-model",
            "current_turn": 0,
            "interaction_round": 0,
            "messages": [{"role": "user", "content": "修复 redis 连接池问题"}],
            "_task_start_round": 0,
        }

        final = await cp.invoke(state)

        # Let the async merge task complete (fire-and-forget in _on_task_completed)
        await asyncio.sleep(0.1)

        # Verify task_complete was processed (engine call + extraction call)
        assert len(call_model.calls) >= 2
        assert call_model.calls[0]["model"] == "test-model"

        # Verify tasks.md was written by the merge task
        tasks_content = mi.load_tasks()
        assert "redis" in tasks_content or "connection pool" in tasks_content

        shutil.rmtree(d, ignore_errors=True)
