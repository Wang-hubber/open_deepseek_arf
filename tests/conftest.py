"""Shared pytest fixtures for ARF tests."""
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


@pytest.fixture
def temp_root():
    """Temporary directory usable as a filesystem root for providers."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def skill_yaml(temp_root):
    """Factory: write a Skill YAML file into temp_root, return its path."""

    def _write(name: str, **extra):
        data = {"name": name, "description": f"{name} skill"}
        data.update(extra)
        p = temp_root / f"{name}.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        return p

    return _write


@pytest.fixture
def model_yaml(temp_root):
    """Factory: write a Model YAML file into temp_root, return its path."""

    def _write(
        name: str,
        model_type: str = "quick",
        activation: str = "discoverable",
        **extra,
    ):
        data = {
            "name": name,
            "type": model_type,
            "api_type": "openai",
            "model": f"{name}-model",
            "api_base": "https://api.example.com",
            "api_key_env": "EXAMPLE_KEY",
            "context_window": 128000,
            "activation": activation,
        }
        data.update(extra)
        p = temp_root / f"{name}.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        return p

    return _write


@pytest.fixture(autouse=True)
def mock_openai():
    """Block all real OpenAI API calls during tests."""
    with patch("openai.OpenAI", side_effect=RuntimeError(
        "Real OpenAI call blocked by test fixture. Mock your LLM calls."
    )):
        yield


# ── Real resource fixtures (replaces MagicMock-based tests) ──

APP_DIR = Path(__file__).parent.parent / "app" / "arf_default_assistant"


@pytest.fixture
def tools_dir(tmp_path):
    """Create temp dir with sample tools for integration tests."""
    tools_root = tmp_path / "tools"
    tools_root.mkdir()

    _sample_tools = {
        "file_writer": {
            "description": "Create or overwrite a file with content",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "File content"},
                },
                "required": ["path", "content"],
            },
            "fn_body": "async def execute(path: str, content: str, **kwargs): return {'ok': True, 'path': path}",
        },
        "python_exec": {
            "description": "Execute a Python script",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "Script path"},
                },
                "required": ["script"],
            },
            "fn_body": "async def execute(script: str, **kwargs): return {'ok': True, 'script': script}",
        },
        "read_file": {
            "description": "Read text from a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            "fn_body": "async def execute(path: str, **kwargs): return {'ok': True, 'content': ''}",
        },
        "write_file": {
            "description": "Write content to a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            "fn_body": "async def execute(path: str, content: str, **kwargs): return {'ok': True}",
        },
        "delete_file": {
            "description": "Delete a file by path",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            "fn_body": "async def execute(path: str, **kwargs): return {'ok': True}",
        },
        "list_directory": {
            "description": "List directory contents",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            "fn_body": "async def execute(path: str, **kwargs): return {'ok': True, 'entries': []}",
        },
        "search_files": {
            "description": "Search for files matching a pattern",
            "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
            "fn_body": "async def execute(pattern: str, **kwargs): return {'ok': True, 'matches': []}",
        },
        "search_content": {
            "description": "Search for text within files",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            "fn_body": "async def execute(query: str, **kwargs): return {'ok': True, 'results': []}",
        },
        "create_directory": {
            "description": "Create a new directory",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            "fn_body": "async def execute(path: str, **kwargs): return {'ok': True}",
        },
        "get_file_info": {
            "description": "Get metadata about a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            "fn_body": "async def execute(path: str, **kwargs): return {'ok': True, 'size': 0}",
        },
        "move_file": {
            "description": "Move or rename a file",
            "parameters": {"type": "object", "properties": {"src": {"type": "string"}, "dst": {"type": "string"}}, "required": ["src", "dst"]},
            "fn_body": "async def execute(src: str, dst: str, **kwargs): return {'ok': True}",
        },
    }

    import yaml as _yaml
    for name, data in _sample_tools.items():
        tool_dir = tools_root / name
        tool_dir.mkdir()
        (tool_dir / "tool.yaml").write_text(_yaml.dump({
            "name": name,
            "description": data["description"],
            "parameters": data["parameters"],
        }), encoding="utf-8")
        (tool_dir / "function.py").write_text(data["fn_body"], encoding="utf-8")

    return tools_root


@pytest.fixture
def skills_dir(tmp_path):
    """Create temp dir with sample skill YAML files for integration tests."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()

    import yaml as _yaml
    (skills_root / "react-component.yaml").write_text(_yaml.dump({
        "name": "react-component",
        "description": "Create a React component with state management",
    }), encoding="utf-8")
    (skills_root / "api-endpoint.yaml").write_text(_yaml.dump({
        "name": "api-endpoint",
        "description": "Create a REST API endpoint",
    }), encoding="utf-8")

    return skills_root


@pytest.fixture
def tool_provider(tools_dir):
    from arf.resources.providers.tool_provider import ToolProvider
    return ToolProvider(tools_dir)


@pytest.fixture
def skill_provider(skills_dir):
    from arf.resources.providers.skill_provider import SkillProvider
    return SkillProvider(skills_dir)


@pytest.fixture
def resolver(tool_provider, skill_provider):
    from arf.resources.resolver import ResourceResolver
    return ResourceResolver(tool_provider, skill_provider)


@pytest.fixture
def fake_model():
    from tests.fixtures.fake_model_adapter import FakeModelAdapter, FakeResponse
    return FakeModelAdapter(default=FakeResponse(content="hello from fake model"))


@pytest.fixture
def agent_config():
    from arf.agent.config import AgentConfig, SystemPromptConfig, PrefixConfig
    return AgentConfig(
        name="test-agent",
        system_prompt=SystemPromptConfig(
            prefix=PrefixConfig(
                role="You are a test assistant.",
                critical_rules="R1: Always verify file operations.\nR2: Never write outside workspace.",
            ),
        ),
        workspace_dir=str(Path(tempfile.gettempdir()) / "arf-test"),
    )
