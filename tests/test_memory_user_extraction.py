"""Tests for MemoryPlugin._rolling_update — user.md extraction."""
import tempfile
from pathlib import Path
import pytest
from arf.memory.config import MemoryConfig
from arf.memory.index import MemoryIndex
from arf.plugins.memory.plugin import MemoryPlugin
from arf.core.plugin_context import PluginContext

pytestmark = pytest.mark.anyio


@pytest.fixture
def tmp_dirs():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def memory_index(tmp_dirs):
    cfg = MemoryConfig(secrets={"enabled": False})
    return MemoryIndex(str(tmp_dirs), cfg)


@pytest.fixture
def ctx(tmp_dirs):
    return PluginContext(
        session_id="test-sid",
        interaction_round=5,
        memory_dir=str(tmp_dirs / "memory"),
        state_dir=str(tmp_dirs / "state"),
    )


MESSAGES = [
    {"role": "user", "content": "I'm a backend engineer working on a Go microservices project. I prefer concise code with no comments."},
    {"role": "assistant", "content": "Got it, I'll keep things concise and avoid comments."},
    {"role": "user", "content": "We decided to use PostgreSQL over MongoDB because we need ACID transactions."},
]


class TestRollingUpdate:

    async def test_extracts_user_facts_and_saves(self, tmp_dirs, memory_index, ctx):
        """Happy path: LLM returns user facts → save_user called."""
        plugin = MemoryPlugin()
        plugin.set_memory_index(memory_index)

        async def fake_call_model(messages, model_name="", tools=None):
            return {"content": "## User Identity\n- Backend engineer, Go\n\n## Decisions\n- PostgreSQL over MongoDB — ACID requirement"}

        plugin.set_call_model(fake_call_model)

        await plugin._rolling_update(ctx, MESSAGES)

        saved = memory_index.load_user()
        assert "Backend engineer" in saved
        assert "PostgreSQL" in saved

    async def test_skips_when_call_model_none(self, tmp_dirs, memory_index, ctx):
        """No call_model set → skip without error."""
        plugin = MemoryPlugin()
        plugin.set_memory_index(memory_index)

        await plugin._rolling_update(ctx, MESSAGES)

        assert memory_index.load_user() == ""

    async def test_skips_when_mem_index_none(self, tmp_dirs, ctx):
        """No mem_index set → skip without error."""
        plugin = MemoryPlugin()

        async def fake_call_model(messages, model_name="", tools=None):
            return {"content": "should not be called"}

        plugin.set_call_model(fake_call_model)

        await plugin._rolling_update(ctx, MESSAGES)
        # Should not raise

    async def test_skips_no_new_memory(self, tmp_dirs, memory_index, ctx):
        """LLM returns NO_NEW_MEMORY → save_user not called."""
        plugin = MemoryPlugin()
        plugin.set_memory_index(memory_index)

        async def fake_call_model(messages, model_name="", tools=None):
            return {"content": "NO_NEW_MEMORY"}

        plugin.set_call_model(fake_call_model)

        # Pre-populate user.md so we can verify it's unchanged
        memory_index.save_user("## Preferences\n- Existing")
        await plugin._rolling_update(ctx, MESSAGES)

        saved = memory_index.load_user()
        assert saved == "## Preferences\n- Existing"

    async def test_skips_empty_response(self, tmp_dirs, memory_index, ctx):
        """LLM returns empty string → save_user not called."""
        plugin = MemoryPlugin()
        plugin.set_memory_index(memory_index)

        async def fake_call_model(messages, model_name="", tools=None):
            return {"content": "   \n"}

        plugin.set_call_model(fake_call_model)

        memory_index.save_user("## Preferences\n- Existing")
        await plugin._rolling_update(ctx, MESSAGES)

        assert memory_index.load_user() == "## Preferences\n- Existing"

    async def test_handles_llm_exception(self, tmp_dirs, memory_index, ctx):
        """LLM raises → log warning, don't crash."""
        plugin = MemoryPlugin()
        plugin.set_memory_index(memory_index)

        async def fake_call_model(messages, model_name="", tools=None):
            raise RuntimeError("API down")

        plugin.set_call_model(fake_call_model)

        # Should not raise
        await plugin._rolling_update(ctx, MESSAGES)
        assert memory_index.load_user() == ""

    async def test_passes_existing_memory_in_prompt(self, tmp_dirs, memory_index, ctx):
        """Existing user.md content is included in the extraction instruction."""
        plugin = MemoryPlugin()
        plugin.set_memory_index(memory_index)
        memory_index.save_user("## User Identity\n- Senior Go engineer")

        captured_messages = []

        async def fake_call_model(messages, model_name="", tools=None):
            captured_messages.extend(messages)
            return {"content": "NO_NEW_MEMORY"}

        plugin.set_call_model(fake_call_model)

        await plugin._rolling_update(ctx, MESSAGES)

        # Instruction is the last message appended after conversation prefix
        instruction = captured_messages[-1]["content"]
        assert "Senior Go engineer" in instruction
        # Conversation content is in the prefix messages
        all_content = " ".join(m.get("content", "") for m in captured_messages)
        assert "PostgreSQL" in all_content
