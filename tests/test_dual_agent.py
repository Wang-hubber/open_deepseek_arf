"""Tests for dual-agent architecture: Dispatcher, handoff, tool partitioning."""

import importlib.util
import json
from pathlib import Path


def _load_tool_func(tool_name: str):
    """Load a tool's execute function the same way ResourceRegistry does."""
    tool_dir = Path(__file__).parent.parent / "src" / "arf" / "resources" / "system" / "tools" / tool_name
    func_file = tool_dir / "function.py"
    spec = importlib.util.spec_from_file_location(f"tool_{tool_name}", func_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "execute")


class TestHandoffTool:
    """handoff_to_sys tool should return handoff marker."""

    def test_handoff_returns_marker(self):
        execute = _load_tool_func("handoff_to_sys")
        result = execute(
            intent="创建天气查询工具",
            required_actions=["创建 tool", "写入 function.py"],
            reason="缺少 resource_loader",
        )
        assert result["ok"] is True
        assert result["handoff"] is True
        assert result["intent"] == "创建天气查询工具"
        assert len(result["required_actions"]) == 2

    def test_handoff_reason_optional(self):
        execute = _load_tool_func("handoff_to_sys")
        result = execute(
            intent="测试",
            required_actions=["test"],
        )
        assert result["ok"] is True
        assert result["reason"] == ""


class TestFileDownloadTool:
    """file_download should generate download info for workspace files."""

    def test_download_existing_file(self, tmp_path):
        execute = _load_tool_func("file_download")
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = execute(path="test.txt", _workspace_dir=str(tmp_path))
        assert result["ok"] is True
        assert result["filename"] == "test.txt"
        assert "/api/download" in result["download_url"]

    def test_download_missing_file(self, tmp_path):
        execute = _load_tool_func("file_download")
        result = execute(path="nonexistent.txt", _workspace_dir=str(tmp_path))
        assert "error" in result

    def test_download_with_label(self, tmp_path):
        execute = _load_tool_func("file_download")
        f = tmp_path / "data.csv"
        f.write_text("a,b,c")
        result = execute(path="data.csv", label="数据文件", _workspace_dir=str(tmp_path))
        assert result["ok"] is True
        assert result["label"] == "数据文件"


class TestFileWriterRestrictions:
    """file_writer should reject User Agent writes to resource paths."""

    def test_user_mode_rejects_tools_path(self):
        execute = _load_tool_func("file_writer")
        result = execute(
            path="tools/weather/function.py",
            content="# test",
            _agent_mode="user",
        )
        assert "error" in result
        assert "handoff_to_sys" in result["error"]

    def test_user_mode_rejects_skills_path(self):
        execute = _load_tool_func("file_writer")
        result = execute(
            path="skills/test/skill.yaml",
            content="name: test",
            _agent_mode="user",
        )
        assert "error" in result
        assert "handoff_to_sys" in result["error"]

    def test_user_mode_rejects_models_path(self):
        execute = _load_tool_func("file_writer")
        result = execute(
            path="models/test/config.yaml",
            content="name: test",
            _agent_mode="user",
        )
        assert "error" in result
        assert "handoff_to_sys" in result["error"]

    def test_user_mode_allows_regular_path(self, tmp_path):
        execute = _load_tool_func("file_writer")
        p = tmp_path / "output" / "report.md"
        result = execute(
            path=str(p),
            content="# Report",
            _agent_mode="user",
        )
        assert result["ok"] is True
        assert p.exists()

    def test_sys_mode_allows_tools_path(self, tmp_path):
        execute = _load_tool_func("file_writer")
        p = tmp_path / "tools" / "test" / "function.py"
        result = execute(
            path=str(p),
            content="# test",
            _agent_mode="sys",
        )
        assert result["ok"] is True
        assert p.exists()


class TestFileDeleterRestrictions:
    """file_deleter should reject User Agent deletes on resource paths."""

    def test_user_mode_rejects_tools_delete(self, tmp_path):
        execute = _load_tool_func("file_deleter")
        p = tmp_path / "tools" / "test.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("test")
        result = execute(path=str(p), _agent_mode="user")
        assert "error" in result
        assert "handoff_to_sys" in result["error"]

    def test_sys_mode_allows_tools_delete(self, tmp_path):
        execute = _load_tool_func("file_deleter")
        p = tmp_path / "tools" / "test.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("test")
        result = execute(path=str(p), _agent_mode="sys")
        assert result["ok"] is True


class TestDispatcherHandoffDetection:
    """Dispatcher should detect handoff_to_sys in tool events."""

    def test_detect_handoff_positive(self):
        from arf.engine.dispatcher import Dispatcher
        events = [
            {"type": "tool_call", "tool": "file_reader", "arguments": "{}", "id": "1"},
            {"type": "tool_result", "tool": "file_reader", "id": "1",
             "result": '{"ok": true}'},
            {"type": "tool_call", "tool": "handoff_to_sys",
             "arguments": '{"intent":"test","required_actions":["create"]}', "id": "2"},
            {"type": "tool_result", "tool": "handoff_to_sys", "id": "2",
             "result": '{"ok":true,"handoff":true,"intent":"test","required_actions":["create"]}'},
        ]
        assert Dispatcher._detect_handoff(events) is True

    def test_detect_handoff_negative(self):
        from arf.engine.dispatcher import Dispatcher
        events = [
            {"type": "tool_call", "tool": "file_reader", "arguments": "{}", "id": "1"},
            {"type": "tool_result", "tool": "file_reader", "id": "1",
             "result": '{"ok": true}'},
        ]
        assert Dispatcher._detect_handoff(events) is False

    def test_extract_handoff_params(self):
        from arf.engine.dispatcher import Dispatcher
        events = [
            {"type": "tool_result", "tool": "handoff_to_sys", "id": "1",
             "result": '{"ok":true,"handoff":true,"intent":"创建工具","required_actions":["write"],"reason":"test"}'},
        ]
        info = Dispatcher._extract_handoff(events)
        assert info["intent"] == "创建工具"
        assert info["required_actions"] == ["write"]


class TestAgentToolPartitioning:
    """UserAgent and SysAgent should have correct tool sets."""

    def test_user_agent_has_no_resource_loader(self):
        from arf.agent.user_agent import UserAgent
        assert "resource_loader" not in UserAgent.KERNEL_TOOLS
        assert "resource_registrar" not in UserAgent.KERNEL_TOOLS
        assert "model_manager" not in UserAgent.KERNEL_TOOLS
        assert "model_switch" not in UserAgent.KERNEL_TOOLS
        assert "manage_hooks" not in UserAgent.KERNEL_TOOLS

    def test_user_agent_has_handoff(self):
        from arf.agent.user_agent import UserAgent
        assert "handoff_to_sys" in UserAgent.KERNEL_TOOLS

    def test_user_agent_has_file_tools(self):
        from arf.agent.user_agent import UserAgent
        assert "file_reader" in UserAgent.KERNEL_TOOLS
        assert "file_writer" in UserAgent.KERNEL_TOOLS
        assert "file_deleter" in UserAgent.KERNEL_TOOLS
        assert "file_download" in UserAgent.KERNEL_TOOLS

    def test_sys_agent_has_all_kernel_tools(self):
        from arf.agent.sys_agent import SysAgent
        sys_tools = {"resource_loader", "resource_registrar", "model_manager",
                     "model_switch", "manage_hooks"}
        assert sys_tools.issubset(SysAgent.KERNEL_TOOLS)

    def test_sys_agent_no_handoff(self):
        from arf.agent.sys_agent import SysAgent
        assert "handoff_to_sys" not in SysAgent.KERNEL_TOOLS


class TestAgentMode:
    """Agent modes should be correct."""

    def test_user_agent_mode(self):
        from arf.agent.user_agent import UserAgent
        assert UserAgent.AGENT_MODE == "user"

    def test_sys_agent_mode(self):
        from arf.agent.sys_agent import SysAgent
        assert SysAgent.AGENT_MODE == "sys"
