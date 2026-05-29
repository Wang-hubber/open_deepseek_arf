"""Tests for A2A communication behaviors described in docs/a2a-communication.md.

Supplements the fact_check tests with behavioral tests for HandoffManager
resolve/detect/build_target_context, InMemoryLock TTL, ConsensusProtocol, TaskDelegator.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from arf.engine.handoff import HandoffManager
from arf.core.config_base import HandoverRuleConfig, HandoverContextConfig


# ---------------------------------------------------------------------------
# 1. HandoffManager.resolve() — four-level fallback (Doc 2.2)
# ---------------------------------------------------------------------------

class TestHandoffResolve:
    """Doc 2.2: resolve() — single candidate → LLM → keyword → first fallback."""

    def test_single_candidate_returns_directly(self):
        """Tier 1: len(candidates)==1 → return candidates[0].to_agent."""
        rules = [HandoverRuleConfig(
            from_agent="main", to_agent="sys", trigger="create resources",
            context=HandoverContextConfig(),
        )]
        hm = HandoffManager(rules=rules)
        result = asyncio.run(hm.resolve("main", {"task": "create a tool"}))
        assert result == "sys"

    def test_no_candidates_returns_empty_string(self):
        """No rules for from_agent → return ''."""
        hm = HandoffManager(rules=[])
        result = asyncio.run(hm.resolve("unknown", {"task": "anything"}))
        assert result == ""

    def test_multiple_candidates_no_system_model_uses_keyword_fallback(self):
        """Tier 3 (no system_model): keyword match trigger words in task."""
        rules = [
            HandoverRuleConfig(
                from_agent="main", to_agent="coder",
                trigger="write code", context=HandoverContextConfig(),
            ),
            HandoverRuleConfig(
                from_agent="main", to_agent="writer",
                trigger="write document", context=HandoverContextConfig(),
            ),
            HandoverRuleConfig(
                from_agent="main", to_agent="architect",
                trigger="design system", context=HandoverContextConfig(),
            ),
        ]
        hm = HandoffManager(rules=rules)
        result = asyncio.run(hm.resolve("main", {"task": "write code for API"}))
        assert result == "coder"

    def test_multiple_candidates_no_keyword_match_returns_first(self):
        """Tier 4: no keyword match → return first candidate."""
        rules = [
            HandoverRuleConfig(
                from_agent="main", to_agent="coder",
                trigger="write code", context=HandoverContextConfig(),
            ),
            HandoverRuleConfig(
                from_agent="main", to_agent="writer",
                trigger="write document", context=HandoverContextConfig(),
            ),
        ]
        hm = HandoffManager(rules=rules)
        result = asyncio.run(hm.resolve("main", {"task": "do something unrelated"}))
        assert result == "coder"  # first in list

    def test_llm_match_falls_back_gracefully_on_exception(self):
        """LLM match failure → keyword fallback → first fallback."""
        rules = [
            HandoverRuleConfig(
                from_agent="main", to_agent="coder",
                trigger="write code", context=HandoverContextConfig(),
            ),
            HandoverRuleConfig(
                from_agent="main", to_agent="writer",
                trigger="write document", context=HandoverContextConfig(),
            ),
        ]
        # system_model_call that raises
        hm = HandoffManager(
            rules=rules,
            system_model_call=AsyncMock(side_effect=RuntimeError("LLM down")),
        )
        # Should not raise, fallback to keyword → first
        result = asyncio.run(hm.resolve("main", {"task": "unrelated task"}))
        assert result in ("coder", "writer")


# ---------------------------------------------------------------------------
# 2. HandoffManager.detect() — handoff signal detection (Doc 2.2)
# ---------------------------------------------------------------------------

class TestHandoffDetect:
    """Doc 2.2: detect(tool_results) scans for {"handoff": True}."""

    def test_detects_direct_dict_with_handoff_true(self):
        """Plain dict with handoff=True."""
        hm = HandoffManager(rules=[])
        result = hm.detect({"tool1": {"handoff": True, "task": "create file"}})
        assert result is not None
        assert result["handoff"] is True
        assert result["task"] == "create file"

    def test_detects_nested_data_key(self):
        """Dict with nested 'data' key (state tool_results format)."""
        hm = HandoffManager(rules=[])
        result = hm.detect({
            "tool1": {"data": {"handoff": True, "task": "update config"}}
        })
        assert result is not None
        assert result["handoff"] is True

    def test_detects_function_backend_wrapped_result(self):
        """FunctionBackend wraps returns in {"result": ...}."""
        hm = HandoffManager(rules=[])
        result = hm.detect({
            "tool1": {"result": {"handoff": True, "task": "scaffold"}}
        })
        assert result is not None
        assert result["handoff"] is True

    def test_returns_none_when_no_handoff(self):
        """No handoff signal → returns None."""
        hm = HandoffManager(rules=[])
        result = hm.detect({"tool1": {"ok": True, "data": "done"}})
        assert result is None

    def test_returns_none_for_empty_dict(self):
        """Empty results → None."""
        hm = HandoffManager(rules=[])
        assert hm.detect({}) is None


# ---------------------------------------------------------------------------
# 3. HandoffManager.build_target_context() (Doc 2.2)
# ---------------------------------------------------------------------------

class TestHandoffBuildContext:
    """Doc 2.2: build_target_context() — raw_turns, task_summary."""

    def test_includes_system_prompt_first(self):
        hm = HandoffManager(rules=[])
        rule = HandoverRuleConfig(
            from_agent="main", to_agent="sys",
            trigger="test", context=HandoverContextConfig(),
        )
        msgs = hm.build_target_context(
            {"messages": []}, rule, {"task": "t"}, "you are sys agent"
        )
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "you are sys agent"

    def test_adds_task_summary_placeholder_when_enabled(self):
        hm = HandoffManager(rules=[])
        ctx = HandoverContextConfig(task_summary=True)
        rule = HandoverRuleConfig(
            from_agent="main", to_agent="sys", trigger="test", context=ctx,
        )
        msgs = hm.build_target_context(
            {"messages": []}, rule, {"task": "t"}, "prompt"
        )
        placeholders = [m for m in msgs if m.get("content") == "__TASK_SUMMARY_PLACEHOLDER__"]
        assert len(placeholders) == 1

    def test_no_placeholder_when_task_summary_disabled(self):
        hm = HandoffManager(rules=[])
        ctx = HandoverContextConfig(task_summary=False)
        rule = HandoverRuleConfig(
            from_agent="main", to_agent="sys", trigger="test", context=ctx,
        )
        msgs = hm.build_target_context(
            {"messages": []}, rule, {"task": "t"}, "prompt"
        )
        placeholders = [m for m in msgs if m.get("content") == "__TASK_SUMMARY_PLACEHOLDER__"]
        assert len(placeholders) == 0

    def test_raw_turns_zero_excludes_history(self):
        hm = HandoffManager(rules=[])
        ctx = HandoverContextConfig(raw_turns=0)
        rule = HandoverRuleConfig(
            from_agent="main", to_agent="sys", trigger="test", context=ctx,
        )
        msgs = hm.build_target_context(
            {"messages": [
                {"role": "user", "content": "Q1"},
                {"role": "assistant", "content": "A1"},
            ]}, rule, {"task": "t"}, "prompt"
        )
        # Only system prompt + (no placeholder) + handoff user message
        assert len(msgs) == 3  # system, (no placeholder), handoff user msg

    def test_raw_turns_positive_includes_recent(self):
        hm = HandoffManager(rules=[])
        ctx = HandoverContextConfig(raw_turns=2)
        rule = HandoverRuleConfig(
            from_agent="main", to_agent="sys", trigger="test", context=ctx,
        )
        messages = [
            {"role": "user", "content": f"Q{i}"}
            for i in range(10)
        ]
        msgs = hm.build_target_context(
            {"messages": messages}, rule, {"task": "t"}, "prompt"
        )
        # Filter out the handoff message (also role=user)
        user_msgs = [m for m in msgs if m.get("role") == "user"
                     and "Handoff:" not in m.get("content", "")]
        assert len(user_msgs) == 4  # raw_turns(2) * 2 = 4 messages from end

    def test_raw_turns_minus_one_includes_all(self):
        hm = HandoffManager(rules=[])
        ctx = HandoverContextConfig(raw_turns=-1)
        rule = HandoverRuleConfig(
            from_agent="main", to_agent="sys", trigger="test", context=ctx,
        )
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        msgs = hm.build_target_context(
            {"messages": messages}, rule, {"task": "t"}, "prompt"
        )
        # Filter out the handoff message
        user_msgs = [m for m in msgs if m.get("role") == "user"
                     and "Handoff:" not in m.get("content", "")]
        # -1 raw_turns means "all" — both user messages included
        assert len(user_msgs) == 2

    def test_handoff_message_format(self):
        hm = HandoffManager(rules=[])
        rule = HandoverRuleConfig(
            from_agent="main", to_agent="sys",
            trigger="test", context=HandoverContextConfig(),
        )
        msgs = hm.build_target_context(
            {"messages": []}, rule,
            {"task": "create tool", "context": "user needs it"},
            "prompt",
        )
        handoff_msg = msgs[-1]
        assert handoff_msg["role"] == "user"
        assert "Handoff: main → sys" in handoff_msg["content"]
        assert "create tool" in handoff_msg["content"]


# ---------------------------------------------------------------------------
# 4. InMemoryLock TTL behavior (Doc 2.6)
# ---------------------------------------------------------------------------

class TestInMemoryLockTTL:
    """Doc 2.6: InMemoryLock with TTL protection against deadlocks."""

    def test_acquire_succeeds_after_ttl_expiry(self):
        from arf.communication.lock import InMemoryLock

        async def run():
            lock = InMemoryLock()
            # Acquire with TTL=0.05s
            assert await lock.acquire("r1", "a", ttl=0.05) is True
            # Second acquire fails (held by a)
            assert await lock.acquire("r1", "b") is False
            # Wait for TTL to expire
            await asyncio.sleep(0.1)
            # Now b can acquire (a's lock expired)
            assert await lock.acquire("r1", "b") is True

        asyncio.run(run())

    def test_release_only_by_owner(self):
        from arf.communication.lock import InMemoryLock

        async def run():
            lock = InMemoryLock()
            await lock.acquire("r1", "a")
            # b tries to release
            await lock.release("r1", "b")
            # Lock still held by a
            assert await lock.acquire("r1", "b") is False
            # a releases
            await lock.release("r1", "a")
            assert await lock.acquire("r1", "b") is True

        asyncio.run(run())

    def test_reset_clears_all(self):
        from arf.communication.lock import InMemoryLock

        async def run():
            lock = InMemoryLock()
            await lock.acquire("r1", "a")
            lock.reset()
            assert await lock.acquire("r1", "b") is True

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 5. TaskDelegator Protocol (Doc 2.9)
# ---------------------------------------------------------------------------

class TestTaskDelegatorProtocol:
    """Doc 2.9: TaskDelegator protocol."""

    def test_protocol_has_delegate_and_get_result(self):
        import inspect
        from arf.core.protocols.communication import TaskDelegator
        methods = {n for n in dir(TaskDelegator) if not n.startswith("_")}
        assert "delegate" in methods
        assert "get_result" in methods

    def test_delegate_signature(self):
        import inspect
        from arf.core.protocols.communication import TaskDelegator
        sig = inspect.signature(TaskDelegator.delegate)
        params = list(sig.parameters.keys())
        for p in ("task", "from_agent", "to_agent"):
            assert p in params


# ---------------------------------------------------------------------------
# 6. ConsensusProtocol signature verification (Doc 2.9)
# ---------------------------------------------------------------------------

class TestConsensusProtocolSignature:
    """Doc 2.9: ConsensusProtocol."""

    def test_protocol_has_propose_and_vote(self):
        import inspect
        from arf.core.protocols.communication import ConsensusProtocol
        methods = {n for n in dir(ConsensusProtocol) if not n.startswith("_")}
        assert "propose" in methods
        assert "vote" in methods

    def test_propose_signature(self):
        import inspect
        from arf.core.protocols.communication import ConsensusProtocol
        sig = inspect.signature(ConsensusProtocol.propose)
        params = list(sig.parameters.keys())
        assert "proposal" in params
        assert "voters" in params

    def test_vote_signature(self):
        import inspect
        from arf.core.protocols.communication import ConsensusProtocol
        sig = inspect.signature(ConsensusProtocol.vote)
        params = list(sig.parameters.keys())
        assert "proposal_id" in params
        assert "vote" in params


# ---------------------------------------------------------------------------
# 7. InMemoryAgentBus dual-agent scenario (Doc 2.3)
# ---------------------------------------------------------------------------


async def _receive_one(bus, agent_name):
    """Receive exactly one message and close the async generator.
    Must aclose() to prevent the while-True generator from blocking
    event loop finalization."""
    gen = bus.receive(agent_name)
    try:
        return await gen.__anext__()
    finally:
        await gen.aclose()


class TestInMemoryAgentBusDualAgent:
    """Doc 2.3: InMemoryAgentBus in dual-agent message routing."""

    def test_agent_a_sends_targeted_to_agent_b(self):
        """Agent A → targeted send → Agent B receives the exact message."""
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.core.protocols.communication import AgentMessage, AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("agent_a", "user-facing agent", ["chat"]))
            await bus.register(AgentInfo("agent_b", "system agent", ["tool_create"]))

            msg = AgentMessage(
                sender="agent_a", receiver="agent_b", type="task_delegate",
                payload={"task": "create a tool", "name": "file_reader"},
                correlation_id="corr-001",
            )
            await bus.send(msg)
            received = await _receive_one(bus, "agent_b")
            assert received.sender == "agent_a"
            assert received.type == "task_delegate"
            assert received.payload["task"] == "create a tool"

        asyncio.run(run())

    def test_broadcast_delivers_to_all_registered_agents(self):
        """receiver=None → message delivered to all registered agents."""
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.core.protocols.communication import AgentMessage, AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("agent_a", "a", ["chat"]))
            await bus.register(AgentInfo("agent_b", "b", ["code"]))
            await bus.register(AgentInfo("agent_c", "c", ["review"]))

            await bus.send(AgentMessage(
                sender="agent_a", receiver=None, type="info",
                payload={"msg": "hello everyone"},
            ))
            for name in ("agent_a", "agent_b", "agent_c"):
                msg = await _receive_one(bus, name)
                assert msg.payload["msg"] == "hello everyone"
                assert msg.receiver is None

        asyncio.run(run())

    def test_send_auto_creates_queue_for_unknown_receiver(self):
        """After fix: send() auto-creates queue for unregistered receivers,
        so messages aren't silently dropped."""
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.core.protocols.communication import AgentMessage

        async def run():
            bus = InMemoryAgentBus()
            # Send to an agent that never registered
            await bus.send(AgentMessage(
                sender="someone", receiver="late_agent",
                type="info", payload={"msg": "hello"},
            ))
            # Queue was auto-created by send()
            assert "late_agent" in bus._queues
            # Message is waiting in the queue
            assert not bus._queues["late_agent"].empty()
            # Receive it (with cleanup)
            received = await _receive_one(bus, "late_agent")
            assert received.sender == "someone"
            assert received.payload["msg"] == "hello"

        asyncio.run(run())

    def test_agent_b_only_receives_own_messages(self):
        """Agent B should NOT receive messages targeted to Agent C."""
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.core.protocols.communication import AgentMessage, AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("agent_b", "b", []))
            await bus.register(AgentInfo("agent_c", "c", []))

            await bus.send(AgentMessage(
                sender="agent_a", receiver="agent_c", type="query",
                payload={"q": "secret"}, correlation_id="x",
            ))
            assert len(bus.sent_messages) == 1
            assert bus.sent_messages[0].receiver == "agent_c"

        asyncio.run(run())

    def test_full_roundtrip_ab_ba(self):
        """A sends to B, B receives and replies back to A."""
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.core.protocols.communication import AgentMessage, AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("agent_a", "a", ["chat"]))
            await bus.register(AgentInfo("agent_b", "b", ["code"]))

            # A → B
            await bus.send(AgentMessage(
                sender="agent_a", receiver="agent_b", type="task_delegate",
                payload={"task": "write function"}, correlation_id="req-1",
            ))
            req = await _receive_one(bus, "agent_b")
            assert req.sender == "agent_a"

            # B → A
            await bus.send(AgentMessage(
                sender="agent_b", receiver="agent_a", type="info",
                payload={"result": "function created", "path": "/tools/foo.py"},
                correlation_id="req-1", reply_to="agent_b",
            ))
            reply = await _receive_one(bus, "agent_a")
            assert reply.sender == "agent_b"
            assert reply.payload["result"] == "function created"
            assert reply.correlation_id == "req-1"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 8. get_rule() (Doc 2.2)
# ---------------------------------------------------------------------------

class TestGetRule:
    """Doc 2.2: get_rule returns matching HandoverRuleConfig."""

    def test_get_rule_returns_matching_rule(self):
        rules = [
            HandoverRuleConfig(
                from_agent="main", to_agent="sys",
                trigger="create", context=HandoverContextConfig(),
            ),
            HandoverRuleConfig(
                from_agent="main", to_agent="auditor",
                trigger="review", context=HandoverContextConfig(),
            ),
        ]
        hm = HandoffManager(rules=rules)
        rule = hm.get_rule("main", "sys")
        assert rule is not None
        assert rule.to_agent == "sys"
        assert rule.trigger == "create"

    def test_get_rule_returns_none_for_unknown_pair(self):
        hm = HandoffManager(rules=[])
        assert hm.get_rule("main", "unknown") is None

    def test_has_rules(self):
        hm = HandoffManager(rules=[
            HandoverRuleConfig(from_agent="a", to_agent="b", trigger="t",
                              context=HandoverContextConfig()),
        ])
        assert hm.has_rules is True
        hm2 = HandoffManager(rules=[])
        assert hm2.has_rules is False
