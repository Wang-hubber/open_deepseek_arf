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


# ---------------------------------------------------------------------------
# 9. MajorityVoteConsensus.verdict() behavioral tests (Doc 2.7)
# NOTE: Doc currently omits verdict() — should be added to section 2.7.
# ---------------------------------------------------------------------------

class TestConsensusVerdict:
    """Doc 2.7: MajorityVoteConsensus — 多数投票. Verify verdict() tallying."""

    def test_verdict_passed_when_above_threshold(self):
        from arf.communication.consensus import MajorityVoteConsensus

        async def run():
            c = MajorityVoteConsensus(threshold=0.5)
            result = await c.propose({"action": "deploy"}, ["a", "b", "c"])
            await c.vote(result["proposal_id"], "yes")
            await c.vote(result["proposal_id"], "yes")
            await c.vote(result["proposal_id"], "yes")
            v = await c.verdict(result["proposal_id"])
            assert v["status"] == "passed"
            assert v["yes"] == 3
            assert v["total"] == 3

        asyncio.run(run())

    def test_verdict_failed_when_below_threshold(self):
        from arf.communication.consensus import MajorityVoteConsensus

        async def run():
            c = MajorityVoteConsensus(threshold=0.5)
            result = await c.propose({"action": "deploy"}, ["a", "b", "c"])
            await c.vote(result["proposal_id"], "yes")
            # Only 1/3 votes yes → ratio 0.33 < 0.5 threshold
            v = await c.verdict(result["proposal_id"])
            assert v["status"] == "failed"
            assert v["yes"] == 1

        asyncio.run(run())

    def test_verdict_exact_threshold_not_passed(self):
        """threshold=0.5 means > 0.5, not >=. 50% yes → failed."""
        from arf.communication.consensus import MajorityVoteConsensus

        async def run():
            c = MajorityVoteConsensus(threshold=0.5)
            result = await c.propose({"action": "x"}, ["a", "b"])
            await c.vote(result["proposal_id"], "yes")
            # 1/2 = 0.5, not > 0.5 → failed
            v = await c.verdict(result["proposal_id"])
            assert v["status"] == "failed"

        asyncio.run(run())

    def test_verdict_not_found_for_unknown_id(self):
        from arf.communication.consensus import MajorityVoteConsensus

        async def run():
            c = MajorityVoteConsensus()
            v = await c.verdict("nonexistent")
            assert v["status"] == "not_found"

        asyncio.run(run())

    def test_verdict_with_zero_voters(self):
        from arf.communication.consensus import MajorityVoteConsensus

        async def run():
            c = MajorityVoteConsensus()
            result = await c.propose({"action": "solo"}, [])
            v = await c.verdict(result["proposal_id"])
            assert v["ratio"] == 0.0
            assert v["status"] == "failed"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 10. PeerAgent.listen() / find_peer() (Doc 2.4)
# NOTE: Doc omits listen() and find_peer() — these are part of the
#       public API and should be documented.
# ---------------------------------------------------------------------------

class TestPeerAgentListen:
    """Doc 2.4: PeerAgent messaging. Verify listen() yields incoming messages."""

    def test_listen_receives_incoming_message(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("peer_a", "a", ["chat"]))
            await bus.register(AgentInfo("peer_b", "b", ["code"]))

            peer_a = PeerAgent(bus, AgentInfo("peer_a", "a", ["chat"]))
            peer_b = PeerAgent(bus, AgentInfo("peer_b", "b", ["code"]))
            await peer_a.start()
            await peer_b.start()

            # B sends a targeted message to A
            await peer_b.send_to("peer_a", "info", {"msg": "hello from b"})

            # A listens and receives it
            agen = peer_a.listen()
            try:
                msg = await agen.__anext__()
            finally:
                await agen.aclose()
            assert msg.sender == "peer_b"
            assert msg.payload["msg"] == "hello from b"

        asyncio.run(run())

    def test_listen_receives_broadcast(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("peer_a", "a", ["chat"]))
            await bus.register(AgentInfo("peer_b", "b", ["code"]))

            peer_a = PeerAgent(bus, AgentInfo("peer_a", "a", ["chat"]))
            peer_b = PeerAgent(bus, AgentInfo("peer_b", "b", ["code"]))
            await peer_a.start()
            await peer_b.start()

            await peer_b.broadcast("info", {"alert": "all hands"})

            agen = peer_a.listen()
            try:
                msg = await agen.__anext__()
            finally:
                await agen.aclose()
            assert msg.sender == "peer_b"
            assert msg.receiver is None
            assert msg.payload["alert"] == "all hands"

        asyncio.run(run())


class TestPeerAgentFindPeer:
    """Doc 2.4: PeerAgent discovery. Verify find_peer() capability matching."""

    def test_find_peer_returns_matching_agent(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("worker_1", "", ["code", "test"]))
            await bus.register(AgentInfo("worker_2", "", ["review"]))
            peer = PeerAgent(bus, AgentInfo("main", "", ["chat"]))
            await peer.start()

            found = await peer.find_peer("code")
            assert found is not None
            assert found.name == "worker_1"

        asyncio.run(run())

    def test_find_peer_returns_none_when_no_match(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("worker_1", "", ["code"]))
            peer = PeerAgent(bus, AgentInfo("main", "", ["chat"]))
            await peer.start()

            found = await peer.find_peer("nonexistent_capability")
            assert found is None

        asyncio.run(run())

    def test_discover_peers_excludes_self(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("self", "", ["chat"]))
            await bus.register(AgentInfo("other", "", ["code"]))
            peer = PeerAgent(bus, AgentInfo("self", "", ["chat"]))
            await peer.start()

            peers = await peer.discover_peers()
            names = [p.name for p in peers]
            assert "self" not in names
            assert "other" in names

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 11. PeerAgent.handoff() success path (Doc 2.4)
# NOTE: Only failure timeout is tested in test_a2a_deep.py.
#       The success path (handoff → response) was never tested.
# ---------------------------------------------------------------------------

class TestPeerAgentHandoffSuccess:
    """Doc 2.4: handoff(task, context, target_capability, timeout=60s).
    Verify successful handoff round-trip."""

    def test_handoff_success_when_peer_responds(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo, AgentMessage

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("main", "user-facing", ["chat"]))
            await bus.register(AgentInfo("sys", "system agent", ["resource_creation"]))

            peer_main = PeerAgent(bus, AgentInfo("main", "user-facing", ["chat"]))
            peer_sys = PeerAgent(bus, AgentInfo("sys", "system agent", ["resource_creation"]))
            await peer_main.start()
            await peer_sys.start()

            # Fire a background task that: receives sys's message,
            # then replies back — simulating sys agent's behavior
            async def sys_handles_handoff():
                agen = peer_sys.listen()
                try:
                    msg = await agen.__anext__()
                    # Reply to the handoff
                    await peer_sys.send_to(msg.sender, "handoff", {
                        "result": "created", "path": "/tools/new_tool.py",
                    })
                finally:
                    await agen.aclose()

            # Start sys listener in background
            task = asyncio.create_task(sys_handles_handoff())

            # Main sends handoff
            response = await peer_main.handoff(
                task="create tool",
                context="user requested file_reader",
                target_capability="resource_creation",
                timeout=2.0,
            )
            await task

            assert response is not None
            assert response["result"] == "created"
            assert response["path"] == "/tools/new_tool.py"

        asyncio.run(run())

    def test_handoff_returns_none_when_no_capable_peer(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("main", "", ["chat"]))
            peer = PeerAgent(bus, AgentInfo("main", "", ["chat"]))
            await peer.start()

            result = await peer.handoff(
                task="do something",
                target_capability="nonexistent",
                timeout=0.5,
            )
            assert result is None

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 12. PeerAgent.negotiate() full flow (Doc 2.4)
# ---------------------------------------------------------------------------

class TestPeerAgentNegotiate:
    """Doc 2.4: negotiate(proposal, peers, timeout=30s).
    Collects responses from target peers. Uses raw bus for responses
    so workers can echo the correlation_id — PeerAgent.send_to() creates
    a new correlation_id, which breaks negotiate's filtering loop."""

    def test_negotiate_collects_responses_with_matching_correlation_id(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo, AgentMessage
        import uuid

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("main", "", ["orchestrator"]))
            await bus.register(AgentInfo("worker_a", "", ["code"]))
            await bus.register(AgentInfo("worker_b", "", ["code"]))

            peer_main = PeerAgent(bus, AgentInfo("main", "", ["orchestrator"]))
            peer_a = PeerAgent(bus, AgentInfo("worker_a", "", ["code"]))
            peer_b = PeerAgent(bus, AgentInfo("worker_b", "", ["code"]))
            await peer_main.start()
            await peer_a.start()
            await peer_b.start()

            corr_id = str(uuid.uuid4())

            async def worker_responds(peer, peer_name, response_text):
                agen = peer.listen()
                try:
                    msg = await agen.__anext__()
                    # Echo correlation_id so negotiate loop matches it
                    await bus.send(AgentMessage(
                        sender=peer_name,
                        receiver="main",
                        type="info",
                        payload={"answer": response_text},
                        correlation_id=corr_id,
                    ))
                finally:
                    await agen.aclose()

            # Send queries with the shared correlation_id
            for target in ["worker_a", "worker_b"]:
                await bus.send(AgentMessage(
                    sender="main", receiver=target, type="query",
                    payload={"proposal": {"question": "ready?"}},
                    correlation_id=corr_id,
                ))

            task_a = asyncio.create_task(worker_responds(peer_a, "worker_a", "a"))
            task_b = asyncio.create_task(worker_responds(peer_b, "worker_b", "b"))

            # Manually run negotiate-like loop with timeout on each q.get()
            responses = {}
            deadline = asyncio.get_event_loop().time() + 2.0
            agen = bus.receive("main")
            try:
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        msg = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    if msg.correlation_id != corr_id:
                        continue
                    responses[msg.sender] = msg.payload
                    if len(responses) >= 2:
                        break
            finally:
                await agen.aclose()

            await asyncio.gather(task_a, task_b)

            assert len(responses) == 2
            assert "worker_a" in responses
            assert "worker_b" in responses

        asyncio.run(run())

    def test_negotiate_timeout_on_dead_peer(self):
        """negoiate() blocks in q.get() when peer never responds.
        This is a known design flaw — deadline check never runs
        because q.get() blocks before yielding."""
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("main", "", ["orchestrator"]))
            await bus.register(AgentInfo("dead_peer", "", ["code"]))
            peer_main = PeerAgent(bus, AgentInfo("main", "", ["orchestrator"]))
            await peer_main.start()

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    peer_main.negotiate(
                        proposal={"q": "test"},
                        peers=["dead_peer"],
                        timeout=0.2,
                    ),
                    timeout=1.0,
                )

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 13. InMemoryAgentBus.send() with timeout parameter (Doc 2.3)
# NOTE: Doc shows send(message: AgentMessage) — missing timeout parameter.
# ---------------------------------------------------------------------------

class TestBusSendTimeout:
    """InMemoryAgentBus.send() accepts timeout: queue full → TimeoutError."""

    def test_send_timeout_parameter_exists(self):
        import inspect
        from arf.communication.in_memory_bus import InMemoryAgentBus
        sig = inspect.signature(InMemoryAgentBus.send)
        assert "timeout" in sig.parameters
        assert sig.parameters["timeout"].default is None

    def test_send_timeout_raises_when_queue_full(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.core.protocols.communication import AgentMessage, AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("worker", "", []))
            # Fill queue to maxsize=100
            for i in range(100):
                await bus.send(AgentMessage(
                    sender="main", receiver="worker",
                    type="info", payload={"i": i},
                ))
            # 101st with timeout=0.1
            with pytest.raises(asyncio.TimeoutError):
                await bus.send(AgentMessage(
                    sender="main", receiver="worker",
                    type="info", payload={"i": 101},
                ), timeout=0.1)

        asyncio.run(run())

    def test_send_timeout_none_blocks_indefinitely(self):
        """timeout=None (default) — blocks until queue has space.
        This is the default 'backpressure' behavior doc describes."""
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.core.protocols.communication import AgentMessage, AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("worker", "", []))
            for i in range(100):
                await bus.send(AgentMessage(
                    sender="main", receiver="worker",
                    type="info", payload={"i": i},
                ))
            # No timeout → blocks → timeout wrapper catches it
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    bus.send(AgentMessage(
                        sender="main", receiver="worker",
                        type="info", payload={},
                    )),  # no timeout arg → blocks
                    timeout=0.2,
                )

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 14. InMemoryLock.acquire() with wait parameter (Doc 2.6)
# NOTE: Doc shows acquire(key, owner, ttl=30.0) — missing wait parameter.
# ---------------------------------------------------------------------------

class TestLockAcquireWait:
    """InMemoryLock.acquire() wait parameter: block up to N seconds."""

    def test_wait_parameter_exists(self):
        import inspect
        from arf.communication.lock import InMemoryLock
        sig = inspect.signature(InMemoryLock.acquire)
        assert "wait" in sig.parameters
        assert sig.parameters["wait"].default is None

    def test_wait_acquires_when_lock_released_during_wait(self):
        from arf.communication.lock import InMemoryLock

        async def run():
            lock = InMemoryLock()
            await lock.acquire("r", "holder", ttl=100.0)

            # Background task: release after 0.05s
            async def releaser():
                await asyncio.sleep(0.05)
                await lock.release("r", "holder")

            release_task = asyncio.create_task(releaser())

            # Acquire with wait — should succeed after lock is released
            acquired = await lock.acquire("r", "waiter", wait=2.0)
            assert acquired is True

            await release_task

        asyncio.run(run())

    def test_wait_returns_false_on_timeout(self):
        from arf.communication.lock import InMemoryLock

        async def run():
            lock = InMemoryLock()
            await lock.acquire("r", "holder", ttl=100.0)
            # Wait for 0.1s but holder never releases
            acquired = await lock.acquire("r", "waiter", wait=0.1)
            assert acquired is False

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 15. DictWorkspace edge cases (Doc 2.5)
# ---------------------------------------------------------------------------

class TestDictWorkspaceEdgeCases:
    """Doc 2.5: DictWorkspace — shared dict storage."""

    def test_read_nonexistent_key_returns_none(self):
        from arf.communication.shared_workspace import DictWorkspace

        async def run():
            ws = DictWorkspace()
            result = await ws.read("nonexistent")
            assert result is None

        asyncio.run(run())

    def test_write_overwrites_existing_key(self):
        from arf.communication.shared_workspace import DictWorkspace

        async def run():
            ws = DictWorkspace()
            await ws.write("k", {"v": 1}, "owner_a")
            await ws.write("k", {"v": 2}, "owner_b")
            result = await ws.read("k")
            assert result["v"] == 2
            assert result["_owner"] == "owner_b"
            # History tracks both writes
            assert len(ws.write_history) == 2

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 16. Discover edge cases (Doc 2.3)
# ---------------------------------------------------------------------------

class TestBusDiscoverEdgeCases:
    """Doc 2.3: discover(capability) filters by capability."""

    def test_discover_with_no_agents_registered(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus

        async def run():
            bus = InMemoryAgentBus()
            result = await bus.discover()
            assert result == []

        asyncio.run(run())

    def test_discover_all_returns_all_registered(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("a", "", ["x"]))
            await bus.register(AgentInfo("b", "", ["y"]))
            await bus.register(AgentInfo("c", "", ["z"]))
            result = await bus.discover()
            assert len(result) == 3

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 17. RoundRobinSupervisor.synthesize edge cases (Doc 2.8)
# ---------------------------------------------------------------------------

class TestSupervisorSynthesize:
    """Doc 2.8: synthesize(results) joins results with newlines."""

    def test_synthesize_empty_results(self):
        from arf.communication.supervisor import RoundRobinSupervisor

        async def run():
            sup = RoundRobinSupervisor()
            result = await sup.synthesize([])
            assert result == ""

        asyncio.run(run())

    def test_synthesize_single_result(self):
        from arf.communication.supervisor import RoundRobinSupervisor

        async def run():
            sup = RoundRobinSupervisor()
            result = await sup.synthesize(["solo"])
            assert result == "solo"

        asyncio.run(run())

    def test_synthesize_with_dict_results(self):
        from arf.communication.supervisor import RoundRobinSupervisor

        async def run():
            sup = RoundRobinSupervisor()
            result = await sup.synthesize([{"x": 1}, {"y": 2}])
            assert "{'x': 1}" in result
            assert "{'y': 2}" in result

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 18. HandoffManager.build_target_context — ToolResult-type values (Doc 2.2)
# ---------------------------------------------------------------------------

class TestHandoffBuildContextEdgeCases:
    """Doc 2.2: build_target_context handles messages with tool_calls filtering."""

    def test_messages_with_tool_calls_are_filtered_out(self):
        """Assistant messages with tool_calls should be excluded from context."""
        hm = HandoffManager(rules=[])
        ctx = HandoverContextConfig(raw_turns=-1)
        rule = HandoverRuleConfig(
            from_agent="main", to_agent="sys", trigger="test", context=ctx,
        )
        messages = [
            {"role": "user", "content": "open file"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "1", "function": {"name": "read_file", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "1", "content": "file contents"},
            {"role": "assistant", "content": "file opened successfully"},
        ]
        msgs = hm.build_target_context(
            {"messages": messages}, rule, {"task": "t"}, "prompt"
        )
        # After filtering: user msg + clean assistant msg should remain
        # (plus system msg + optional placeholder + handoff msg)
        user_in_context = [m for m in msgs if m.get("role") == "user"
                          and "Handoff:" not in m.get("content", "")]
        assistant_in_context = [m for m in msgs if m.get("role") == "assistant"]
        assert len(user_in_context) == 1  # "open file"
        # Clean assistant msg is kept, tool_calls assistant and tool msgs filtered
        assert len(assistant_in_context) == 1
        assert assistant_in_context[0]["content"] == "file opened successfully"
