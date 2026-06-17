"""Tests for Memory system — config, secrets, index, generator."""
import tempfile
from pathlib import Path
import pytest
from arf.memory.config import MemoryConfig, ProjectMemoryConfig, UserMemoryConfig, SecretsConfig
from arf.memory.secrets_store import SecretsStore
from arf.memory.index import MemoryIndex


class TestMemoryConfig:
    def test_defaults(self):
        cfg = MemoryConfig()
        assert cfg.project.enabled is True
        assert cfg.project.max_size_kb == 100
        assert cfg.user.enabled is True
        assert cfg.user.max_size_kb == 50
        assert cfg.secrets.enabled is True
        assert cfg.secrets.master_key_env == "ARF_MASTER_KEY"

    def test_disable_layers(self):
        cfg = MemoryConfig(
            project={"enabled": False},
            user={"enabled": False},
            secrets={"enabled": False},
        )
        assert cfg.project.enabled is False
        assert cfg.user.enabled is False
        assert cfg.secrets.enabled is False


class TestSecretsStore:
    def test_set_get_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            s = SecretsStore(d, "test-key")
            s.set("API_KEY", "sk-abc123")
            assert s.get("API_KEY") == "sk-abc123"

    def test_list_names(self):
        with tempfile.TemporaryDirectory() as d:
            s = SecretsStore(d, "test-key")
            s.set("A", "1")
            s.set("B", "2")
            assert s.list_names() == ["A", "B"]

    def test_get_unknown_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            s = SecretsStore(d, "test-key")
            assert s.get("NOPE") is None

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            s1 = SecretsStore(d, "test-key")
            s1.set("TOKEN", "secret")
            # New instance same key
            s2 = SecretsStore(d, "test-key")
            assert s2.get("TOKEN") == "secret"

    def test_wrong_key_gibberish(self):
        with tempfile.TemporaryDirectory() as d:
            s1 = SecretsStore(d, "correct-key")
            s1.set("TOKEN", "secret")
            s2 = SecretsStore(d, "wrong-key")
            # Should not crash, but won't return correct value
            val = s2.get("TOKEN")
            assert val != "secret" or val is None

    def test_encrypted_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            s = SecretsStore(d, "test-key")
            s.set("API_KEY", "sk-abc123")
            enc = (Path(d) / "memory" / "secrets.enc").read_bytes()
            assert b"sk-abc123" not in enc

    def test_empty_file_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as d:
            s = SecretsStore(d, "test-key")
            assert s.load() == {}
            assert s.list_names() == []


class TestMemoryIndex:
    def test_build_injected_messages(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MemoryConfig()
            idx = MemoryIndex(d, cfg)
            idx.save_project("# Project\n\nTest project memory")
            idx.save_user("# User\n\nPrefers pytest")

            msgs = idx.build_injected_messages()
            assert len(msgs) == 2  # project + user, no secrets store
            assert any("Project Memory" in m["content"] for m in msgs)
            assert any("User Memory" in m["content"] for m in msgs)

    def test_build_injected_messages_with_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MemoryConfig()
            secrets = SecretsStore(d, "test-key")
            secrets.set("API_KEY", "sk-123")
            idx = MemoryIndex(d, cfg, secrets_store=secrets)
            idx.save_project("# P")
            idx.save_user("# U")

            msgs = idx.build_injected_messages()
            assert len(msgs) == 3  # project + user + secrets
            assert any("Available Secrets" in m["content"] for m in msgs)
            assert "API_KEY" in [m["content"] for m in msgs if "Secrets" in m["content"]][0]

    def test_disabled_layers_not_injected(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MemoryConfig(
                project={"enabled": False},
                user={"enabled": False},
                secrets={"enabled": False},
            )
            idx = MemoryIndex(d, cfg)
            idx.save_project("# P")
            idx.save_user("# U")

            msgs = idx.build_injected_messages()
            assert len(msgs) == 0

    def test_has_project_file(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MemoryConfig()
            idx = MemoryIndex(d, cfg)
            assert idx.has_project_file() is False
            idx.save_project("# P")
            assert idx.has_project_file() is True

    def test_load_empty_returns_empty_string(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MemoryConfig()
            idx = MemoryIndex(d, cfg)
            assert idx.load_project() == ""
            assert idx.load_user() == ""


class TestProjectMemoryGenerator:
    def test_needs_generation(self, tmp_path):
        from arf.memory.project_generator import ProjectMemoryGenerator
        cfg = MemoryConfig()
        idx = MemoryIndex(str(tmp_path), cfg)
        gen = ProjectMemoryGenerator(str(tmp_path), idx)
        assert gen.needs_generation() is True

        idx.save_project("# P")
        assert gen.needs_generation() is False

    def test_scan_produces_output(self, tmp_path):
        from arf.memory.project_generator import ProjectMemoryGenerator
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')")

        cfg = MemoryConfig()
        idx = MemoryIndex(str(tmp_path), cfg)
        gen = ProjectMemoryGenerator(str(tmp_path), idx)
        ctx = gen._scan()

        assert ctx["project_name"] != ""
        assert "# Test" in ctx["readme"]
        assert "src/" in ctx["tree"]
        assert "name='test'" in ctx["deps"]
