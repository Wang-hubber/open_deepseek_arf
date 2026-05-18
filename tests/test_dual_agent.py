"""Tests for dual-agent architecture: config resolution, handoff, tool partitioning."""

import importlib.util
import json
import tempfile
from pathlib import Path


def _load_tool_func(tool_name: str):
    """Load a tool's execute function the same way ResourceRegistry does."""
    tool_dir = Path(__file__).parent.parent / "src" / "arf" / "resources" / "system" / "tools" / tool_name
    func_file = tool_dir / "function.py"
    spec = importlib.util.spec_from_file_location(f"tool_{tool_name}", func_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "execute")


# ------------------------------------------------------------------
# config resolution
# ------------------------------------------------------------------

class TestConfigResolution:
    """resolve_config merges workspace over framework defaults."""

    def test_framework_only_returns_defaults(self):
        from arf.agent.base import resolve_config
        cfg = resolve_config("arf_user_agent.yaml", None)
        assert cfg["agent"]["model"] == "quick_thinking"
        assert cfg["agent"]["mode"] == "user"
        assert cfg["agent"]["classifier_enabled"] is True
        assert "handoff_to_sys" in cfg["tools"]["kernel"]

    def test_workspace_overrides_framework(self):
        from arf.agent.base import resolve_config
        import tempfile
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "arf_user_agent.yaml").write_text(
            "agent:\n  model: deep_thinking\n  max_turns: 12\n",
            encoding="utf-8"
        )
        cfg = resolve_config("arf_user_agent.yaml", tmp)
        assert cfg["agent"]["model"] == "deep_thinking"  # overridden
        assert cfg["agent"]["max_turns"] == 12            # overridden
        assert cfg["agent"]["mode"] == "user"              # from framework
        assert cfg["agent"]["classifier_enabled"] is True  # from framework

    def test_deep_merge_preserves_nested(self):
        from arf.agent.base import resolve_config
        import tempfile
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "arf_user_agent.yaml").write_text(
            "tools:\n  preload:\n    - weather\n",
            encoding="utf-8"
        )
        cfg = resolve_config("arf_user_agent.yaml", tmp)
        assert cfg["tools"]["preload"] == ["weather"]
        assert "handoff_to_sys" in cfg["tools"]["kernel"]  # unchanged


# ------------------------------------------------------------------
# agent from_config
# ------------------------------------------------------------------

class TestAgentFromConfig:
    """from_config should resolve model, set all attributes from YAML."""

    def _make_agents(self, tmp):
        from arf.agent.base import generate_default_configs
        from arf.resources.manager import ResourceRegistry
        generate_default_configs(tmp)

        r = ResourceRegistry()
        # Register a minimal model so from_config can resolve
        r._items["models"]["deep_thinking"] = {
            "type": "model", "name": "deep_thinking",
            "model_type": "deep_thinking",
            "config": {"base_url": "http://x", "api_key": "k", "model_name": "m"},
            "context_window": 1048576,
            "source": "user", "readonly": False, "configured": True,
        }
        r._items["models"]["quick_thinking"] = {
            "type": "model", "name": "quick_thinking",
            "model_type": "quick_thinking",
            "config": {"base_url": "http://x", "api_key": "k", "model_name": "m"},
            "context_window": 1048576,
            "source": "user", "readonly": False, "configured": True,
        }
        return r

    def test_user_agent_from_config(self):
        from arf.agent.user_agent import UserAgent
        tmp = tempfile.mkdtemp()
        r = self._make_agents(tmp)
        agent = UserAgent.from_config(r, tmp)
        assert agent.agent_mode == "user"
        assert agent.default_model == "quick_thinking"
        assert agent.max_turns == 6
        assert agent.classifier_enabled is True
        assert "handoff_to_sys" in agent.kernel_tools
        assert "resource_loader" not in agent.kernel_tools

    def test_sys_agent_from_config(self):
        from arf.agent.sys_agent import SysAgent
        tmp = tempfile.mkdtemp()
        r = self._make_agents(tmp)
        agent = SysAgent.from_config(r, tmp)
        assert agent.agent_mode == "sys"
        assert agent.default_model == "deep_thinking"
        assert agent.max_turns == 10
        assert agent.classifier_enabled is False
        assert "resource_loader" in agent.kernel_tools
        assert "handoff_to_sys" not in agent.kernel_tools

    def test_workspace_override_works(self):
        from arf.agent.user_agent import UserAgent
        tmp = tempfile.mkdtemp()
        r = self._make_agents(tmp)
        # Write workspace override
        (Path(tmp) / "arf_user_agent.yaml").write_text(
            "agent:\n  max_turns: 3\n  classifier_enabled: false\n",
            encoding="utf-8"
        )
        agent = UserAgent.from_config(r, tmp)
        assert agent.max_turns == 3  # overridden
        assert agent.classifier_enabled is False  # overridden
        assert agent.agent_mode == "user"  # from framework


# ------------------------------------------------------------------
# handoff / file_download tools
# ------------------------------------------------------------------

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
        result = execute(intent="测试", required_actions=["test"])
        assert result["ok"] is True
        assert result["reason"] == ""


class TestFileDownloadTool:
    """file_download should generate download info for workspace files."""

    def test_download_existing_file(self, tmp_path):
        execute = _load_tool_func("file_download")
        (tmp_path / "test.txt").write_text("hello")
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
        (tmp_path / "data.csv").write_text("a,b,c")
        result = execute(path="data.csv", label="数据文件", _workspace_dir=str(tmp_path))
        assert result["ok"] is True
        assert result["label"] == "数据文件"


# ------------------------------------------------------------------
# file_writer / file_deleter restrictions
# ------------------------------------------------------------------

class TestFileWriterRestrictions:
    """file_writer should reject User Agent writes to resource paths."""

    def test_user_mode_rejects_tools_path(self):
        execute = _load_tool_func("file_writer")
        result = execute(path="tools/weather/function.py", content="# test", _agent_mode="user")
        assert "error" in result
        assert "handoff_to_sys" in result["error"]

    def test_user_mode_rejects_skills_path(self):
        execute = _load_tool_func("file_writer")
        result = execute(path="skills/test/skill.yaml", content="name: test", _agent_mode="user")
        assert "error" in result
        assert "handoff_to_sys" in result["error"]

    def test_user_mode_rejects_models_path(self):
        execute = _load_tool_func("file_writer")
        result = execute(path="models/test/config.yaml", content="name: test", _agent_mode="user")
        assert "error" in result
        assert "handoff_to_sys" in result["error"]

    def test_user_mode_allows_regular_path(self, tmp_path):
        execute = _load_tool_func("file_writer")
        p = tmp_path / "output" / "report.md"
        result = execute(path=str(p), content="# Report", _agent_mode="user")
        assert result["ok"] is True
        assert p.exists()

    def test_sys_mode_allows_tools_path(self, tmp_path):
        execute = _load_tool_func("file_writer")
        p = tmp_path / "tools" / "test" / "function.py"
        result = execute(path=str(p), content="# test", _agent_mode="sys")
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


# ------------------------------------------------------------------
# Dispatcher handoff detection
# ------------------------------------------------------------------

class TestDispatcherHandoffDetection:
    """Dispatcher should detect handoff_to_sys in tool events."""

    def test_detect_handoff_positive(self):
        from arf.engine.dispatcher import Dispatcher
        events = [
            {"type": "tool_result", "tool": "handoff_to_sys", "id": "2",
             "result": '{"ok":true,"handoff":true,"intent":"test","required_actions":["create"]}'},
        ]
        assert Dispatcher._detect_handoff(events) is True

    def test_detect_handoff_negative(self):
        from arf.engine.dispatcher import Dispatcher
        events = [
            {"type": "tool_result", "tool": "file_reader", "id": "1", "result": '{"ok": true}'},
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


# ------------------------------------------------------------------
# prompt building
# ------------------------------------------------------------------

class TestPromptBuilding:
    """Agents should build correct prompts from config."""

    def _make_registry(self):
        from arf.resources.manager import ResourceRegistry
        r = ResourceRegistry()
        r._items["models"]["quick_thinking"] = {
            "type": "model", "name": "quick_thinking", "model_type": "quick_thinking",
            "config": {"base_url": "http://x", "api_key": "k", "model_name": "m"},
            "context_window": 1048576,
            "source": "user", "readonly": False, "configured": True,
        }
        r._items["models"]["deep_thinking"] = {
            "type": "model", "name": "deep_thinking", "model_type": "deep_thinking",
            "config": {"base_url": "http://x", "api_key": "k", "model_name": "m"},
            "context_window": 1048576,
            "source": "user", "readonly": False, "configured": True,
        }
        return r

    def test_user_prompt_contains_handoff(self):
        from arf.agent.user_agent import UserAgent
        tmp = tempfile.mkdtemp()
        from arf.agent.base import generate_default_configs
        generate_default_configs(tmp)
        r = self._make_registry()
        agent = UserAgent.from_config(r, tmp)
        prompt = agent.build_system_prompt()
        assert "handoff_to_sys" in prompt
        assert "Intent Translation" in prompt

    def test_sys_prompt_contains_gates(self):
        from arf.agent.sys_agent import SysAgent
        tmp = tempfile.mkdtemp()
        from arf.agent.base import generate_default_configs
        generate_default_configs(tmp)
        r = self._make_registry()
        agent = SysAgent.from_config(r, tmp)
        prompt = agent.build_system_prompt()
        assert "Gate 1" in prompt
        assert "resource_loader" in prompt
