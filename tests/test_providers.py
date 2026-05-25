"""Tests for SkillProvider."""
import tempfile
from pathlib import Path
import yaml
from arf.resources.providers.skill_provider import SkillProvider


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
