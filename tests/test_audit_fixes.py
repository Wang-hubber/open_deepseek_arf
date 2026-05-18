"""Tests for framework audit fixes."""

import importlib.util
import json
import tempfile
from pathlib import Path


def _load_tool_func(tool_name: str):
    tool_dir = Path(__file__).parent.parent / "src" / "arf" / "resources" / "system" / "tools" / tool_name
    func_file = tool_dir / "function.py"
    spec = importlib.util.spec_from_file_location(f"tool_{tool_name}", func_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "execute")


class TestTask1ConfigRegisterDeepseek:
    """config_register_deepseek reads from config_default.yaml, not Python dict."""

    def test_load_model_default_reads_yaml(self):
        from arf.server.routes import _load_model_default
        from arf.resources.manager import ResourceRegistry
        import arf.resources.system
        sys_dir = Path(arf.resources.system.__file__).parent
        r = ResourceRegistry()
        r.load(str(sys_dir))
        default = _load_model_default(r, "deep_thinking")
        assert default["name"] == "deep_thinking"
        assert default["model_type"] == "deep_thinking"
        assert "config_template" in default
        assert default["config_template"]["model_name"]["placeholder"] == "deepseek-v4-pro"

    def test_no_DEEPSEEK_MODEL_SPECS_global(self):
        from arf.server import routes
        assert not hasattr(routes, 'DEEPSEEK_MODEL_SPECS'), \
            "DEEPSEEK_MODEL_SPECS should be removed"
        assert not hasattr(routes, '_DEFAULT_DS_COMMON'), \
            "_DEFAULT_DS_COMMON should be removed"


class TestTask2CriticalRulesFromYaml:
    """critical_rules section is read from YAML config."""

    def test_user_agent_yaml_has_critical_rules(self):
        from arf.agent.base import resolve_config
        cfg = resolve_config("arf_user_agent.yaml", None)
        assert "critical_rules" in cfg
        assert "R0" in cfg["critical_rules"]

    def test_sys_agent_yaml_has_critical_rules(self):
        from arf.agent.base import resolve_config
        cfg = resolve_config("arf_sys_agent.yaml", None)
        assert "critical_rules" in cfg
        assert "R0" in cfg["critical_rules"]

    def test_critical_rules_section_in_prompt(self):
        from arf.agent.user_agent import UserAgent
        from arf.resources.manager import ResourceRegistry
        tmp = tempfile.mkdtemp()
        from arf.agent.base import generate_default_configs
        generate_default_configs(tmp)
        r = ResourceRegistry()
        r._items["models"]["quick_thinking"] = {
            "type": "model", "name": "quick_thinking", "model_type": "quick_thinking",
            "config": {"base_url": "http://x", "api_key": "k", "model_name": "m"},
            "context_window": 1048576, "source": "user", "readonly": False, "configured": True,
        }
        agent = UserAgent.from_config(r, tmp)
        prompt = agent.build_system_prompt()
        assert "CRITICAL" in prompt
        assert "R0" in prompt


class TestTask3SysAgentProgressiveDisclosure:
    """SysAgent should not load all tools as kernel."""

    def test_sys_agent_kernel_excludes_resource_mgmt(self):
        from arf.agent.base import resolve_config
        cfg = resolve_config("arf_sys_agent.yaml", None)
        kernel = cfg["tools"]["kernel"]
        assert "model_manager" not in kernel
        assert "resource_registrar" not in kernel
        assert "model_switch" not in kernel
        assert "manage_hooks" not in kernel
        assert "resource_loader" in kernel
        assert "file_reader" in kernel
        assert "file_writer" in kernel


class TestTask4LazyToolLoading:
    """Tool functions are loaded lazily on first access."""

    def test_get_tool_lazy_loads_function(self):
        from arf.resources.manager import ResourceRegistry
        import arf.resources.system
        sys_dir = Path(arf.resources.system.__file__).parent
        r = ResourceRegistry()
        r.load(str(sys_dir))
        tool = r.get_tool("handoff_to_sys")
        assert tool is not None
        assert callable(tool.get("function"))
        result = tool["function"](intent="test", required_actions=["x"])
        assert result["ok"] is True

    def test_function_none_before_first_access(self):
        from arf.resources.manager import ResourceRegistry
        import arf.resources.system
        sys_dir = Path(arf.resources.system.__file__).parent
        r = ResourceRegistry()
        r.load(str(sys_dir))
        item = r._items["tools"].get("resource_registrar")
        assert item is not None
        # function should be None before get_tool is called
        assert item.get("function") is None
        assert item.get("_function_path") is not None


class TestTask5ToolCategory:
    """tool_category uses registry source, not @sys/ prefix."""

    def test_system_tools_have_source_system(self):
        from arf.resources.manager import ResourceRegistry
        import arf.resources.system
        sys_dir = Path(arf.resources.system.__file__).parent
        r = ResourceRegistry()
        r.load(str(sys_dir))
        system_tools = {n for n, i in r._items["tools"].items() if i["source"] == "system"}
        assert "file_reader" in system_tools
        assert "resource_loader" in system_tools
        assert "handoff_to_sys" in system_tools

    def test_system_tool_names_injected_to_engine(self):
        import tempfile
        from arf.agent.user_agent import UserAgent
        from arf.agent.base import generate_default_configs
        from arf.resources.manager import ResourceRegistry
        import arf.resources.system
        tmp = tempfile.mkdtemp()
        generate_default_configs(tmp)
        sys_dir = Path(arf.resources.system.__file__).parent
        r = ResourceRegistry()
        r.load(str(sys_dir))
        # Register a configured model so from_config() can resolve
        r._items["models"]["quick_thinking"] = {
            "type": "model", "name": "quick_thinking", "model_type": "quick_thinking",
            "config": {"base_url": "http://x", "api_key": "k", "model_name": "m"},
            "context_window": 1048576, "source": "user", "readonly": False, "configured": True,
        }
        agent = UserAgent.from_config(r, tmp)
        engine = agent._build_graph_engine()
        assert len(engine._system_tool_names) > 0, f"got empty system_tool_names from {len(r._items['tools'])} tools"
        assert "file_reader" in engine._system_tool_names


class TestTask7TraceDualWrite:
    """Trace events are written to workspace file alongside SQLite."""

    def test_write_trace_file_creates_jsonl(self, tmp_path):
        from arf.server.database import _write_trace_file
        sid = "20260518_test"
        events = [
            {"session_id": sid, "turn": 1, "node": "call_model", "status": "ok"},
            {"session_id": sid, "turn": 1, "node": "execute_tools", "status": "ok"},
        ]
        _write_trace_file(str(tmp_path), sid, events)
        trace_file = tmp_path / "memory" / "traces" / f"{sid}.jsonl"
        assert trace_file.exists()
        lines = trace_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert 'call_model' in lines[0]


class TestTask8SessionStart:
    """SessionStart hook is in HOOK_EVENTS."""

    def test_session_start_in_hook_events(self):
        from arf.server.hook_runner import HOOK_EVENTS
        assert "SessionStart" in HOOK_EVENTS


class TestTask9SessionEnd:
    """SessionEnd fires on normal completion."""

    def test_fire_session_end_exists(self):
        from arf.server.session_manager import SessionManager
        assert hasattr(SessionManager, 'fire_session_end')


class TestAllImports:
    """Verify all modules import correctly after changes."""

    def test_manager_imports(self):
        from arf.resources.manager import ResourceRegistry
        assert hasattr(ResourceRegistry, 'get_tool')

    def test_routes_imports(self):
        from arf.server.routes import _load_model_default, _DEEPSEEK_MODEL_TYPES
        assert len(_DEEPSEEK_MODEL_TYPES) == 3

    def test_database_imports(self):
        from arf.server.database import _write_trace_file, insert_trace_events
        assert callable(_write_trace_file)
        assert callable(insert_trace_events)
