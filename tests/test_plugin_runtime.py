"""Tests for PluginRuntime."""
import json
import os

from arf.core.plugin_runtime import PluginRuntime


def test_plugin_runtime_creation():
    rt = PluginRuntime(
        python_executable="/usr/bin/python3",
        env_vars={"DEEPSEEK_API_KEY": "sk-test"},
        memory_dir="/app/memory",
        workspace_dir="/app/workspace",
        trace_dir="/app/traces",
        session_id="default",
        interaction_round=42,
        system_model="quick",
        model_configs={
            "deep": {"api_base": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"},
        },
    )
    assert rt.python_executable == "/usr/bin/python3"
    assert rt.session_id == "default"
    assert rt.interaction_round == 42


def test_to_dict_and_from_dict():
    rt = PluginRuntime(
        python_executable="/usr/bin/python3",
        env_vars={"KEY": "val"},
        memory_dir="/app/memory",
        workspace_dir="/app/workspace",
        trace_dir="/app/traces",
        session_id="s1",
        interaction_round=1,
        system_model="quick",
        model_configs={},
    )
    d = rt.to_dict()
    rt2 = PluginRuntime.from_dict(d)
    assert rt2.python_executable == rt.python_executable
    assert rt2.session_id == rt.session_id
    assert rt2.interaction_round == rt.interaction_round
    assert rt2.env_vars == rt.env_vars


def test_json_roundtrip():
    rt = PluginRuntime(
        python_executable="/usr/bin/python3",
        env_vars={"KEY": "val"},
        memory_dir="/app/memory",
        workspace_dir="/app/workspace",
        trace_dir="/app/traces",
        session_id="default",
        interaction_round=5,
        system_model="quick",
        model_configs={"deep": {}},
    )
    json_str = json.dumps(rt.to_dict())
    restored = PluginRuntime.from_dict(json.loads(json_str))
    assert restored.session_id == "default"
    assert restored.interaction_round == 5


def test_defaults():
    rt = PluginRuntime()
    assert rt.python_executable != ""
    assert rt.session_id == "default"
    assert rt.interaction_round == 0
    assert isinstance(rt.env_vars, dict)


def test_state_dir_in_to_dict():
    """BUG-007: state_dir must be present in to_dict() so hooks can find state files."""
    rt = PluginRuntime()
    d = rt.to_dict()
    assert "state_dir" in d, "BUG-007: state_dir missing from PluginRuntime.to_dict()"
    assert d["state_dir"] == "./data/state"


def test_state_dir_custom_value():
    rt = PluginRuntime(state_dir="/custom/state")
    assert rt.state_dir == "/custom/state"
    d = rt.to_dict()
    assert d["state_dir"] == "/custom/state"


def test_state_dir_roundtrip():
    """state_dir must survive JSON serialization roundtrip."""
    import json
    rt = PluginRuntime(state_dir="/app/data/state")
    json_str = json.dumps(rt.to_dict())
    restored = PluginRuntime.from_dict(json.loads(json_str))
    assert restored.state_dir == "/app/data/state"
