"""Tests for resource providers (skills, models, ...)."""
import tempfile
from pathlib import Path
import yaml
from arf.resources.providers.skill_provider import SkillProvider
from arf.resources.providers.model_provider import ModelProvider


def write_skill(dir: Path, name: str, activation="discoverable", **extra):
    data = {"name": name, "description": f"{name} skill", "activation": activation}
    data.update(extra)
    (dir / f"{name}.yaml").write_text(yaml.dump(data), encoding="utf-8")


def test_skill_provider_lists_skills():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_skill(root, "code_review", activation="kernel")
        write_skill(root, "debug", activation="discoverable")

        p = SkillProvider(root)
        skills = p.list()
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"code_review", "debug"}


def test_skill_provider_splits_kernel_and_dynamic():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_skill(root, "code_review", activation="kernel")
        write_skill(root, "debug", activation="discoverable")

        p = SkillProvider(root)
        kernel = p.list_kernel()
        dynamic = p.list_dynamic()

        assert {s.name for s in kernel} == {"code_review"}
        assert {s.name for s in dynamic} == {"debug"}


def test_skill_provider_empty_dir():
    with tempfile.TemporaryDirectory() as td:
        p = SkillProvider(Path(td))
        assert p.list() == []
        assert p.list_kernel() == []
        assert p.list_dynamic() == []


def test_skill_provider_caches_after_first_scan():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_skill(root, "s1")
        p = SkillProvider(root)
        first = p.list()
        # Add a file after scan — shouldn't appear (cache not invalidated)
        write_skill(root, "s2")
        second = p.list()
        assert len(second) == 1  # cached, doesn't see s2


def test_skill_provider_invalidate_rescans():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_skill(root, "s1")
        p = SkillProvider(root)
        p.list()  # populate cache
        write_skill(root, "s2")
        p.invalidate_dynamic()
        after = p.list_dynamic()
        assert {s.name for s in after} == {"s1", "s2"}


# ── ModelProvider tests ──────────────────────────────────────────────


def write_model(dir: Path, name: str, activation="discoverable", **extra):
    data = {
        "name": name, "api_type": "openai", "model": f"{name}-model",
        "api_base": "https://api.example.com", "api_key_env": "EXAMPLE_KEY",
        "context_window": 128000, "activation": activation,
    }
    data.update(extra)
    (dir / f"{name}.yaml").write_text(yaml.dump(data), encoding="utf-8")


def test_model_provider_lists_models():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_model(root, "quick", activation="kernel")
        write_model(root, "deep", activation="discoverable")

        p = ModelProvider(root)
        models = p.list()
        assert len(models) == 2
        names = {m.name for m in models}
        assert names == {"quick", "deep"}


def test_model_provider_splits_kernel_and_dynamic():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_model(root, "quick", activation="kernel")
        write_model(root, "vision", activation="discoverable")

        p = ModelProvider(root)
        assert {m.name for m in p.list_kernel()} == {"quick"}
        assert {m.name for m in p.list_dynamic()} == {"vision"}


def test_model_provider_empty_dir():
    with tempfile.TemporaryDirectory() as td:
        p = ModelProvider(Path(td))
        assert p.list() == []


def test_model_provider_invalidate_rescans():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_model(root, "quick")
        p = ModelProvider(root)
        p.list()
        write_model(root, "deep")
        p.invalidate_dynamic()
        assert {m.name for m in p.list_dynamic()} == {"quick", "deep"}
