"""[E2E] py-arf.engine: AgentConfig + EngineBuilder + Engine.run roundtrip.

[构造] [方法] [边界]

API notes (verified against py-arf/src/engine.rs):
- EngineBuilder.new(buses) is a staticmethod (not __init__)
- Engine.run(state, user_input) takes state first
- EngineState() is the no-arg constructor
- state.messages is a list of dicts: {role, content, tool_call_id, name,
  tool_calls: [{id, name, arguments, target}]} — added by Step 3.5 of
  Task 4.

Compared to crates/arf-e2e/tests/react_loop.rs::react_single_round_text:
- Rust uses scripted provider; Python uses live MiniMax (skipped when no key).
- Both assert state.messages round-trips correctly.

CheckpointRule test (Phase 6 task 6.22.4): Python bindings for
CheckpointRule / Checkpoint / ActionMessage now exist (engine.rs).
Test verifies CheckpointRule construction and trigger enum membership.
"""
import pytest
from arf._arf import (
    ActionMessage,
    AgentConfig,
    Checkpoint,
    CheckpointRule,
    EngineBuilder,
    EngineState,
    ModelCall,
)


@pytest.mark.asyncio
async def test_engine_roundtrip_text_response(live_bus, minimax_key):
    """[构造] Engine.run() with simple prompt returns text output.

    Mirrors crates/arf-e2e/tests/react_loop.rs::react_single_round_text.
    Live provider path: MiniMax-M3 responds to a deterministic prompt.
    """
    config = AgentConfig(
        agent_id="e2e-text",
        provider="minimax",
        model="MiniMax-M3",
    )
    builder = EngineBuilder.new([live_bus])
    engine = await builder.build(config)
    state = EngineState()
    output = await engine.run(state, "Respond with the single word: PONG")
    assert "PONG" in output or "pong" in output.lower()


@pytest.mark.asyncio
async def test_engine_state_messages_accumulate(live_bus, minimax_key):
    """[方法] state.messages grows as engine runs.

    Mirrors react_loop.rs assertions on state.messages length after one round.
    Expected: system + user + assistant = 3 messages.
    """
    config = AgentConfig(agent_id="e2e-msg", provider="minimax", model="MiniMax-M3")
    engine = await EngineBuilder.new([live_bus]).build(config)
    state = EngineState()
    await engine.run(state, "Say 'hello'")
    assert len(state.messages) >= 3
    assert state.messages[0]["role"] == "system"
    assert state.messages[1]["role"] == "user"
    assert state.messages[2]["role"] == "assistant"


@pytest.mark.asyncio
async def test_engine_roundtrip_multi_round(live_bus, minimax_key):
    """[方法] Multiple chat() calls increase round_count.

    Mirrors multi-round scenario from react_loop.rs. Each Engine.run() on
    the same state is a fresh round (round_count grows).

    Implementation note: Engine.run() currently takes ownership of the
    inner State for the duration of the call. After it returns the State
    is restored (see engine.rs:217-219). Calling run() twice on the
    same Engine should therefore increment round_count.

    We pass two distinct inputs and assert round_count >= 2 after the
    second run.
    """
    config = AgentConfig(agent_id="e2e-multi", provider="minimax", model="MiniMax-M3")
    engine = await EngineBuilder.new([live_bus]).build(config)
    state = EngineState()

    await engine.run(state, "Reply with just the word: ONE")
    rc_after_first = state.round_count
    assert rc_after_first >= 1

    await engine.run(state, "Reply with just the word: TWO")
    rc_after_second = state.round_count
    assert rc_after_second >= rc_after_first + 1


@pytest.mark.asyncio
async def test_engine_roundtrip_final_output_matches_assistant(live_bus, minimax_key):
    """[边界] Engine.run()'s return value matches the last assistant message.

    The engine is expected to return the content of the final assistant
    message from state.messages. We verify that the returned string is
    contained in the last assistant message's content (text-typed run).
    """
    config = AgentConfig(
        agent_id="e2e-final",
        provider="minimax",
        model="MiniMax-M3",
    )
    engine = await EngineBuilder.new([live_bus]).build(config)
    state = EngineState()
    output = await engine.run(state, "What is 2+2? Answer with just the digit.")

    # The assistant's content (last message) should contain the output text
    # (or be equal to it after stripping whitespace).
    assert state.messages, "state.messages should not be empty after run()"
    last = state.messages[-1]
    assert last["role"] == "assistant", (
        f"expected last message role='assistant', got {last['role']!r}"
    )
    assert output.strip() and output.strip() in last["content"], (
        f"engine output {output!r} should be contained in last assistant "
        f"content {last['content']!r}"
    )


@pytest.mark.asyncio
async def test_engine_roundtrip_checkpoint_rule_fires():
    """[方法] CheckpointRule construction + Checkpoint enum membership.

    Phase 6 task 6.22.4 verifies that the Python CheckpointRule binding
    is constructible, exposes its name + trigger, and accepts a list of
    pre-built ActionMessage payloads. The full checkpoint-fires-in-engine
    integration test mirrors
    crates/arf-e2e/tests/recovery.rs::round_end_checkpoint_writes_file_and_returns
    and is covered by test_mcp_facade.py::test_python_engine_receives_cross_bus_tool_result
    (which sets up the full routing path).

    This test focuses on the binding surface:
    - Checkpoint enum has all 5 variants (BeforeModelCall / AfterModelCall /
      BeforeToolExec / AfterToolExec / RoundEnd)
    - CheckpointRule(name, trigger, actions) accepts a name + Checkpoint
      trigger + list of pre-built ActionMessage instances
    - The ModelCall ActionMessage binding exposes its msg_type and
      correlation_id
    """
    # Verify all 5 Checkpoint variants exist.
    assert Checkpoint.BeforeModelCall is not None
    assert Checkpoint.AfterModelCall is not None
    assert Checkpoint.BeforeToolExec is not None
    assert Checkpoint.AfterToolExec is not None
    assert Checkpoint.RoundEnd is not None

    # Verify ModelCall ActionMessage is constructible + exposes wire info.
    call = ModelCall()
    assert call.msg_type == "model_call"
    assert call.correlation_id  # UUID v4 string

    # Build a CheckpointRule from pre-built ActionMessage instances.
    # Each action carries msg_type + correlation_id + payload — what the
    # Engine publishes at the chosen Checkpoint position.
    actions = [
        ActionMessage(msg_type="model_call", payload={"k": "v"}),
        ActionMessage(msg_type="model_call", payload={"k": "v2"}),
    ]
    rule = CheckpointRule(
        name="round_end_broadcast",
        trigger=Checkpoint.RoundEnd,
        actions=actions,
    )
    assert rule.name == "round_end_broadcast"
    assert rule.trigger == Checkpoint.RoundEnd
    assert len(rule.actions) == 2