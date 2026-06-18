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
