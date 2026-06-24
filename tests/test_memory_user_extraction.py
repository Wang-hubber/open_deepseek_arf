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
    def __init__(self, call_model=None):
        self.state = type('State', (), {
            'messages': [],
            'agent_id': 'test-agent',
        })()
        self.inputs = []
        self._call_model = call_model

    def input(self, role, content):
        self.inputs.append((role, content))


def make_ctx(tmp_dirs, agent=None):
    return PluginContext(
        agent=agent or FakeAgent(),
        session_id="test-sid",
        data_dir=str(tmp_dirs),
    )


def make_plugin(memory_index, data_dir="/tmp/test"):
    plugin = MemoryPlugin()
    # Force init without going through handle()
    plugin._inited = True
    plugin._data_dir = data_dir
    plugin._index = memory_index
    plugin._secrets = None
    return plugin


def make_fake_result(content: str):
    """Return a minimal object matching ModelResult.content interface."""
    return type('FakeResult', (), {'content': content, 'tool_calls': [], 'usage': {}, 'finish_reason': 'stop'})()


class TestRollingUpdate:

    async def test_extracts_user_facts_and_saves(self, tmp_dirs, memory_index):
        """Happy path: LLM returns user facts → save_user called."""
        async def fake_call_model(messages, tools=None):
            return make_fake_result("## User Identity\n- Backend engineer, Go\n\n## Decisions\n- PostgreSQL over MongoDB — ACID requirement")

        agent = FakeAgent(call_model=fake_call_model)
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index)

        await plugin._rolling_update(ctx)

        saved = memory_index.load_user()
        assert "Backend engineer" in saved
        assert "PostgreSQL" in saved

    async def test_skips_when_mem_index_none(self, tmp_dirs):
        """No mem_index set → skip without error."""
        async def fake_call_model(messages, tools=None):
            return make_fake_result("should not be called")

        agent = FakeAgent(call_model=fake_call_model)
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(None)

        await plugin._rolling_update(ctx)
        # Should not raise

    async def test_skips_no_new_memory(self, tmp_dirs, memory_index):
        """LLM returns NO_NEW_MEMORY → save_user not called."""
        async def fake_call_model(messages, tools=None):
            return make_fake_result("NO_NEW_MEMORY")

        agent = FakeAgent(call_model=fake_call_model)
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index)

        memory_index.save_user("## Preferences\n- Existing")
        await plugin._rolling_update(ctx)

        saved = memory_index.load_user()
        assert saved == "## Preferences\n- Existing"

    async def test_skips_empty_response(self, tmp_dirs, memory_index):
        """LLM returns empty string → save_user not called."""
        async def fake_call_model(messages, tools=None):
            return make_fake_result("   \n")

        agent = FakeAgent(call_model=fake_call_model)
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index)

        memory_index.save_user("## Preferences\n- Existing")
        await plugin._rolling_update(ctx)

        assert memory_index.load_user() == "## Preferences\n- Existing"

    async def test_handles_llm_exception(self, tmp_dirs, memory_index):
        """LLM raises → log warning, don't crash."""
        async def fake_call_model(messages, tools=None):
            raise RuntimeError("API down")

        agent = FakeAgent(call_model=fake_call_model)
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index)

        await plugin._rolling_update(ctx)
        assert memory_index.load_user() == ""

    async def test_passes_existing_memory_in_prompt(self, tmp_dirs, memory_index):
        """Existing user.md content is included in the extraction instruction."""
        captured_messages = []

        async def fake_call_model(messages, tools=None):
            captured_messages.extend(messages)
            return make_fake_result("NO_NEW_MEMORY")

        agent = FakeAgent(call_model=fake_call_model)
        agent.state.messages = [
            type('Msg', (), {'role': m['role'], 'content': m['content']})()
            for m in MESSAGES
        ]
        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index)

        memory_index.save_user("## User Identity\n- Senior Go engineer")
        await plugin._rolling_update(ctx)

        instruction = captured_messages[-1]["content"]
        assert "Senior Go engineer" in instruction
        all_content = " ".join(m.get("content", "") for m in captured_messages)
        assert "PostgreSQL" in all_content

    async def test_deepcopy_preserves_system_prompts(self, tmp_dirs, memory_index):
        """DI-injected system prompts are copied and form a stable cache prefix."""
        captured_messages = []

        async def fake_call_model(messages, tools=None):
            captured_messages.extend(messages)
            return make_fake_result("NO_NEW_MEMORY")

        # Simulate a realistic agent state with DI-injected system prompts
        agent = FakeAgent(call_model=fake_call_model)
        agent.state.messages = [
            # Framework-injected system prompt (from agent.yaml)
            type('Msg', (), {'role': 'system', 'content': (
                "You are the data specialist. Your available tools:\n\n"
                "- list_directory(path)\n- read_file(path)\n- csv_to_md(path)"
            )})(),
            # Skill inventory (injected via agent.input role=system name=MCP)
            type('Msg', (), {'role': 'system', 'content': (
                "## Available Skills\n"
                "- **plan_solve**: Task planning with dependency tracking\n"
                "- **filesystem**: Cross-platform filesystem operations"
            ), 'name': 'MCP'})(),
            # User message
            type('Msg', (), {'role': 'user', 'content': 'Analyze the sales data in /data/'})(),
            # Assistant with text
            type('Msg', (), {'role': 'assistant', 'content': 'I will start by exploring the data directory.'})(),
            # Assistant with tool_calls (dict content)
            type('Msg', (), {'role': 'assistant', 'content': {
                'content': None,
                'tool_calls': [{'id': 'tc1', 'name': 'list_directory', 'params': {'path': '/data/'}}],
            }})(),
            # Tool result
            type('Msg', (), {'role': 'tool', 'content': {
                'tool_call_id': 'tc1', 'name': 'list_directory',
                'result': {'ok': True, 'entries': ['sales.csv', 'customers.csv']},
            }})(),
            # Assistant (text with tool_calls)
            type('Msg', (), {'role': 'assistant', 'content': {
                'content': 'Found 2 files.',
                'tool_calls': [{'id': 'tc2', 'name': 'csv_to_md', 'params': {'path': '/data/sales.csv'}}],
            }})(),
            # Tool result
            type('Msg', (), {'role': 'tool', 'content': {
                'tool_call_id': 'tc2', 'name': 'csv_to_md',
                'result': '| date | revenue |\n| 2024-01 | 10000 |',
            }})(),
        ]

        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index)

        await plugin._rolling_update(ctx)

        # 1. System prompts are preserved as the prefix
        assert len(captured_messages) >= 2
        assert captured_messages[0]["role"] == "system"
        assert "data specialist" in captured_messages[0]["content"]
        assert captured_messages[1]["role"] == "system"
        assert "Available Skills" in captured_messages[1]["content"]
        assert "plan_solve" in captured_messages[1]["content"]

        # 2. Tool messages are filtered (user memory → noise)
        tool_roles = [m["role"] for m in captured_messages]
        assert "tool" not in tool_roles, "tool messages should be filtered for user memory"

        # 3. Assistant messages with tool_calls are flattened to text-only
        for m in captured_messages:
            if m["role"] == "assistant":
                assert isinstance(m["content"], str), (
                    f"assistant content should be str, got {type(m['content'])}"
                )

        # 4. The last message is the extraction instruction (not part of agent state)
        assert captured_messages[-1]["role"] == "user"
        assert "extract user-specific facts" in captured_messages[-1]["content"].lower()

        # 5. Prefix stability: all messages except the last form the cacheable prefix.
        # The prefix comes from deepcopy(agent.state.messages) → _user_memory_messages,
        # so it's identical to what the agent's model_call would send (minus tools).
        prefix = captured_messages[:-1]
        content_hashes = [
            m["role"] + ":" + (m["content"][:50] if isinstance(m["content"], str) else str(m["content"])[:50])
            for m in prefix
        ]
        # Same prefix → same cache key → server-side cache hit on first N-1 messages
        assert len(content_hashes) > 0
        # System messages come first (framework convention)
        assert prefix[0]["role"] == "system"
        assert prefix[1]["role"] == "system"


class TestTaskExperienceExtraction:

    async def test_preserves_tool_messages_and_system_prompts(self, tmp_dirs, memory_index):
        """Task memory keeps tool results and DI-injected system prompts."""
        captured_messages = []

        async def fake_call_model(messages, tools=None):
            captured_messages.extend(messages)
            return make_fake_result('{"category":"bugfix","description":"fix x","approach":[],"lessons":[],"should_write":true}')

        agent = FakeAgent(call_model=fake_call_model)
        agent.state.messages = [
            type('Msg', (), {'role': 'system', 'content': 'You are the PM agent.'})(),
            type('Msg', (), {'role': 'system', 'content': '## Available Skills\n- **plan_solve**: Task planning', 'name': 'MCP'})(),
            type('Msg', (), {'role': 'user', 'content': 'Fix the login timeout bug.'})(),
            type('Msg', (), {'role': 'assistant', 'content': {
                'content': None,
                'tool_calls': [{'id': 't1', 'name': 'search_content', 'params': {'pattern': 'timeout'}}],
            }})(),
            type('Msg', (), {'role': 'tool', 'content': {
                'tool_call_id': 't1', 'name': 'search_content',
                'result': 'Found timeout in auth.py:42 — hardcoded 2s',
            }})(),
            type('Msg', (), {'role': 'assistant', 'content': 'Found the issue in auth.py.'})(),
        ]

        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index)

        await plugin._extract_task_experience(ctx, "Fixed", "", 1.0)

        # 1. System prompts preserved
        assert captured_messages[0]["role"] == "system"
        assert "PM agent" in captured_messages[0]["content"]
        assert captured_messages[1]["role"] == "system"
        assert "Available Skills" in captured_messages[1]["content"]

        # 2. Tool messages ARE preserved (valuable context for task memory)
        tool_roles = [m["role"] for m in captured_messages]
        assert "tool" in tool_roles, "tool messages should be preserved for task memory"

        # 3. Tool result content is intact
        tool_msgs = [m for m in captured_messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "auth.py" in str(tool_msgs[0]["content"])

        # 4. Extraction instruction is the last message
        assert captured_messages[-1]["role"] == "user"
        assert "Fixed" in captured_messages[-1]["content"]  # {task_result} substituted

    async def test_skips_leading_orphaned_tool_messages(self, tmp_dirs, memory_index):
        """Leading tool messages without preceding assistant are skipped."""
        captured_messages = []

        async def fake_call_model(messages, tools=None):
            captured_messages.extend(messages)
            return make_fake_result('{"category":"bugfix","description":"fix","approach":[],"lessons":[],"should_write":false}')

        agent = FakeAgent(call_model=fake_call_model)
        agent.state.messages = [
            type('Msg', (), {'role': 'tool', 'content': {
                'tool_call_id': 'orphan', 'name': 'read_file', 'result': 'orphan result',
            }})(),  # orphan — no preceding assistant with tool_calls
            type('Msg', (), {'role': 'system', 'content': 'System prompt after orphan.'})(),
            type('Msg', (), {'role': 'user', 'content': 'Hello.'})(),
        ]

        ctx = make_ctx(tmp_dirs, agent)
        plugin = make_plugin(memory_index)

        await plugin._extract_task_experience(ctx, "Done", "", 1.0)

        # The orphan tool message should be skipped
        roles = [m["role"] for m in captured_messages]
        assert "tool" not in roles, "orphaned tool message should be skipped"
        assert captured_messages[0]["role"] == "system"
        assert "System prompt after orphan" in captured_messages[0]["content"]
