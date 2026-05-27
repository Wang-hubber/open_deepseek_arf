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

    def _write(name: str, activation: str = "discoverable", **extra):
        data = {"name": name, "description": f"{name} skill", "activation": activation}
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
