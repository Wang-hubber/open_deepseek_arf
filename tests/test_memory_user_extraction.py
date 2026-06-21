"""Tests for MemoryPlugin._rolling_update — user.md extraction."""
import tempfile
from pathlib import Path
import pytest
from arf.memory.config import MemoryConfig
from arf.memory.index import MemoryIndex
from arf.plugins.memory import MemoryPlugin
from arf.harness.context import PluginContext

pytestmark = pytest.mark.anyio


@pytest.fixture
def tmp_dirs():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def memory_index(tmp_dirs):
    cfg = MemoryConfig(secrets={"enabled": False})
    return MemoryIndex(str(tmp_dirs), cfg)


MESSAGES = [
    {"role": "user", "content": "I'm a backend engineer working on a Go microservices project. I prefer concise code with no comments."},
    {"role": "assistant", "content": "Got it, I'll keep things concise and avoid comments."},
    {"role": "user", "content": "We decided to use PostgreSQL over MongoDB because we need ACID transactions."},
]


class FakeAgent:
    def __init__(self):
        self.state = type('State', (), {
            'messages': [],
            'agent_id': 'test-agent',
        })()
        self.inputs = []

    def input(self, role, content):
        self.inputs.append((role, content))


def make_ctx(tmp_dirs, agent=None):
    return PluginContext(
        agent=agent or FakeAgent(),
        session_id="test-sid",
        data_dir=str(tmp_dirs),
    )


def make_plugin(memory_index, call_model=None, data_dir="/tmp/test"):
    plugin = MemoryPlugin()
    # Force init without going through handle()
    plugin._inited = True
    plugin._data_dir = data_dir
    plugin._index = memory_index
    plugin._secrets = None
    plugin._call_model = call_model
    return plugin


class TestRollingUpdate:

    async def test_extracts_user_facts_and_saves(self, tmp_dirs, memory_index):
        """Happy path: LLM returns user facts → save_user called."""
        async def fake_call_model(messages, model_name=""):
            return {"content": "## User Identity\n- Backend engineer, Go\n\n## Decisions\n- PostgreSQL over MongoDB — ACID requirement"}

        agent = FakeAgent()
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index, fake_call_model)

        await plugin._rolling_update(ctx)

        saved = memory_index.load_user()
        assert "Backend engineer" in saved
        assert "PostgreSQL" in saved

    async def test_skips_when_call_model_none(self, tmp_dirs, memory_index):
        """No call_model set → skip without error."""
        agent = FakeAgent()
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index, call_model=None)

        await plugin._rolling_update(ctx)
        assert memory_index.load_user() == ""

    async def test_skips_when_mem_index_none(self, tmp_dirs):
        """No mem_index set → skip without error."""
        async def fake_call_model(messages, model_name=""):
            return {"content": "should not be called"}

        agent = FakeAgent()
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(None, fake_call_model)

        await plugin._rolling_update(ctx)
        # Should not raise

    async def test_skips_no_new_memory(self, tmp_dirs, memory_index):
        """LLM returns NO_NEW_MEMORY → save_user not called."""
        async def fake_call_model(messages, model_name=""):
            return {"content": "NO_NEW_MEMORY"}

        agent = FakeAgent()
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index, fake_call_model)

        memory_index.save_user("## Preferences\n- Existing")
        await plugin._rolling_update(ctx)

        saved = memory_index.load_user()
        assert saved == "## Preferences\n- Existing"

    async def test_skips_empty_response(self, tmp_dirs, memory_index):
        """LLM returns empty string → save_user not called."""
        async def fake_call_model(messages, model_name=""):
            return {"content": "   \n"}

        agent = FakeAgent()
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index, fake_call_model)

        memory_index.save_user("## Preferences\n- Existing")
        await plugin._rolling_update(ctx)

        assert memory_index.load_user() == "## Preferences\n- Existing"

    async def test_handles_llm_exception(self, tmp_dirs, memory_index):
        """LLM raises → log warning, don't crash."""
        async def fake_call_model(messages, model_name=""):
            raise RuntimeError("API down")

        agent = FakeAgent()
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index, fake_call_model)

        await plugin._rolling_update(ctx)
        assert memory_index.load_user() == ""

    async def test_passes_existing_memory_in_prompt(self, tmp_dirs, memory_index):
        """Existing user.md content is included in the extraction instruction."""
        captured_messages = []

        async def fake_call_model(messages, model_name=""):
            captured_messages.extend(messages)
            return {"content": "NO_NEW_MEMORY"}

        agent = FakeAgent()
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index, fake_call_model)

        memory_index.save_user("## User Identity\n- Senior Go engineer")
        await plugin._rolling_update(ctx)

        instruction = captured_messages[-1]["content"]
        assert "Senior Go engineer" in instruction
        all_content = " ".join(m.get("content", "") for m in captured_messages)
        assert "PostgreSQL" in all_content
