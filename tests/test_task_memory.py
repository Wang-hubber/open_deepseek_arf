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


import tempfile
from pathlib import Path
from arf.memory.index import MemoryIndex


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
