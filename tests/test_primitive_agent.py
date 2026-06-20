"""Tests for PrimitiveAgent — 6 primitives."""
import pytest
from arf.agent.state import AgentState, Message, WaitItem, ModelResult
from arf.agent.primitive import PrimitiveAgent


async def fake_call_model(messages, tools=None):
    return ModelResult(content="fake response", tool_calls=[], usage={}, finish_reason="stop")


@pytest.fixture
def agent():
    return PrimitiveAgent(
        agent_id="a1",
        model_config={"api_base": "https://x.com/v1", "api_key_env": "K", "model_name": "m", "context_window": 128000},
        call_model=fake_call_model,
    )


class TestInput:
    def test_input_appends_message_by_default(self, agent):
        msg = agent.input("user", "hello")
        assert msg.role == "user"
        assert msg.content == "hello"
        assert len(agent.state.messages) == 1
        assert agent.state.messages[0].message_id == msg.message_id

    def test_input_generates_unique_message_ids(self, agent):
        m1 = agent.input("user", "a")
        m2 = agent.input("user", "b")
        assert m1.message_id != m2.message_id

    def test_input_position_begin(self, agent):
        agent.input("user", "first")
        agent.input("system", "inserted", position="begin")
        assert agent.state.messages[0].role == "system"
        assert agent.state.messages[0].content == "inserted"

    def test_input_position_index(self, agent):
        agent.input("user", "a")
        agent.input("user", "b")
        agent.input("system", "middle", position=1)
        assert agent.state.messages[1].content == "middle"


class TestModelCall:
    @pytest.mark.anyio
    async def test_model_call_returns_result(self, agent):
        agent.input("user", "hi")
        result = await agent.model_call()
        assert result.content == "fake response"
        assert result.tool_calls == []

    @pytest.mark.anyio
    async def test_model_call_passes_messages_to_call_model(self):
        captured = []

        async def capture_call(messages, tools=None):
            captured.append(messages)
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        ag = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=capture_call)
        ag.input("user", "test message")
        await ag.model_call()
        assert len(captured) == 1
        msgs = captured[0]
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "test message"


class TestWait:
    def test_wait_appends_to_state(self, agent):
        wi = agent.wait("before_tools", "need approval")
        assert wi.hook_name == "before_tools"
        assert agent.state.waiting["before_tools"][0] is wi

    def test_wait_generates_unique_ids(self, agent):
        w1 = agent.wait("before_tools", "a")
        w2 = agent.wait("before_tools", "b")
        assert w1.wait_id != w2.wait_id
        assert len(agent.state.waiting["before_tools"]) == 2


class TestFinishWait:
    def test_finish_wait_removes_item(self, agent):
        wi = agent.wait("before_tools", "x")
        remaining = agent.finish_wait(wi.wait_id)
        assert "before_tools" not in remaining or len(remaining.get("before_tools", [])) == 0

    def test_finish_wait_returns_updated_waiting(self, agent):
        w1 = agent.wait("before_tools", "a")
        w2 = agent.wait("before_tools", "b")
        remaining = agent.finish_wait(w1.wait_id)
        assert len(remaining["before_tools"]) == 1
        assert remaining["before_tools"][0].wait_id == w2.wait_id


class TestStop:
    def test_stop_returns_state(self, agent):
        agent.input("user", "hello")
        state = agent.stop()
        assert isinstance(state, AgentState)
        assert len(state.messages) == 1

    @pytest.mark.anyio
    async def test_stop_deactivates_agent(self, agent):
        agent.stop()
        with pytest.raises(RuntimeError):
            await agent.model_call()


class TestResume:
    @pytest.mark.anyio
    async def test_resume_restores_full_state(self):
        ag1 = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ag1.input("user", "msg1")
        ag1.wait("before_tools", "approval")
        state = ag1.stop()

        ag2 = PrimitiveAgent.resume(state, fake_call_model)
        assert ag2.state.agent_id == "a1"
        assert ag2.state.session_id == ""
        assert len(ag2.state.messages) == 1
        assert ag2.state.messages[0].content == "msg1"
        assert len(ag2.state.waiting["before_tools"]) == 1

    @pytest.mark.anyio
    async def test_resume_agent_can_call_model(self):
        ag1 = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ag1.input("user", "test")
        state = ag1.stop()

        ag2 = PrimitiveAgent.resume(state, fake_call_model)
        result = await ag2.model_call()
        assert result.content == "fake response"
