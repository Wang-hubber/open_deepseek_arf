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
def tools_dir():
    return APP_DIR / "tools"


@pytest.fixture
def skills_dir():
    return APP_DIR / "skills"


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
    from arf.agent.config import AgentConfig
    return AgentConfig.from_yaml(str(APP_DIR / "agent.yaml"))
