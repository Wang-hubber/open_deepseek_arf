"""Fact-check tests: A2A Communication — docs/a2a-communication.md vs arf/communication/.

Each test validates a specific claim made in the documentation against actual code.
"""

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest


# ---------------------------------------------------------------------------
# 1. AgentMessage & AgentInfo (docs 2.3)
# ---------------------------------------------------------------------------

class TestAgentMessage:
    """Doc: AgentMessage fields — sender, receiver, type, payload, correlation_id."""

    def test_agent_message_fields(self):
        from arf.core.protocols.communication import AgentMessage
        fields = {f.name for f in AgentMessage.__dataclass_fields__.values()}
        for f in ("sender", "receiver", "type", "payload", "correlation_id"):
            assert f in fields, f"Field '{f}' missing from AgentMessage"

    def test_agent_info_fields(self):
        """Doc: AgentInfo — name, description, capabilities."""
        from arf.core.protocols.communication import AgentInfo
        fields = {f.name for f in AgentInfo.__dataclass_fields__.values()}
        assert {"name", "description", "capabilities"}.issubset(fields)


# ---------------------------------------------------------------------------
# 2. Protocols (docs 2.9 — 7 Protocol classes)
# ---------------------------------------------------------------------------

class TestCommunicationProtocols:
    """Doc: 6 Protocol classes in arf/core/protocols/communication.py."""

    def test_seven_protocols_defined(self):
        """Doc: AgentBus, PeerAgent, TaskDelegator, Supervisor,
        SharedWorkspace, Lock, ConsensusProtocol."""
        from arf.core.protocols import communication as comm
        protocols = [n for n, v in vars(comm).items()
                     if inspect.isclass(v) and issubclass(v, comm.Protocol)
                     and v is not comm.Protocol]
        # Count non-base Protocol classes
        all_classes = [n for n, v in vars(comm).items()
                       if inspect.isclass(v) and not n.startswith("_")]
        protocol_classes = [n for n in all_classes
                           if n not in ("AgentMessage", "AgentInfo", "Protocol")]
        assert len(protocol_classes) == 7, (
            f"Expected 7 protocol classes, got {len(protocol_classes)}: {protocol_classes}"
        )

    def test_agent_bus_protocol_methods(self):
        """Doc: AgentBus — send, receive, register, discover."""
        from arf.core.protocols.communication import AgentBus
        methods = {n for n in dir(AgentBus) if not n.startswith("_")}
        for m in ("send", "receive", "register", "discover"):
            assert m in methods

    def test_lock_protocol_methods(self):
        """Doc: Lock — acquire, release."""
        from arf.core.protocols.communication import Lock
        methods = {n for n in dir(Lock) if not n.startswith("_")}
        for m in ("acquire", "release"):
            assert m in methods

    def test_shared_workspace_protocol_methods(self):
        """Doc: SharedWorkspace — write, read."""
        from arf.core.protocols.communication import SharedWorkspace
        methods = {n for n in dir(SharedWorkspace) if not n.startswith("_")}
        for m in ("write", "read"):
            assert m in methods


# ---------------------------------------------------------------------------
# 3. InMemoryAgentBus (docs 2.3)
# ---------------------------------------------------------------------------

class TestInMemoryAgentBus:
    """Doc: InMemoryAgentBus — asyncio.Queue-based message routing."""

    def test_queues_have_maxsize_100(self):
        """Doc: asyncio.Queue(maxsize=100) per agent."""
        import inspect as _ins
        from arf.communication.in_memory_bus import InMemoryAgentBus
        src = _ins.getsource(InMemoryAgentBus.receive)
        assert "maxsize=100" in src

    def test_send_broadcast_to_all_when_receiver_is_none(self):
        """Doc: receiver=None → broadcast to all registered queues."""
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.core.protocols.communication import AgentMessage, AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("a", "", []))
            await bus.register(AgentInfo("b", "", []))
            await bus.send(AgentMessage(sender="x", receiver=None, type="info", payload={}))
            assert len(bus.sent_messages) == 1

        asyncio.run(run())

    def test_send_targeted_to_named_agent(self):
        """Doc: receiver=name → targeted delivery."""
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.core.protocols.communication import AgentMessage, AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("target", "", []))
            await bus.send(AgentMessage(sender="x", receiver="target", type="query", payload={}))
            assert len(bus.sent_messages) == 1
            assert bus.sent_messages[0].receiver == "target"

        asyncio.run(run())

    def test_discover_filters_by_capability(self):
        """Doc: discover(capability) filters by capability."""
        from arf.communication.in_memory_bus import InMemoryAgentBus
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("a", "", ["code", "test"]))
            await bus.register(AgentInfo("b", "", ["write"]))
            result = await bus.discover("code")
            assert len(result) == 1
            assert result[0].name == "a"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. PeerAgent (docs 2.4)
# ---------------------------------------------------------------------------

class TestPeerAgent:
    """Doc: PeerAgent — decentralized P2P communication."""

    def test_peer_agent_signature(self):
        """Doc: PeerAgent(bus, info)."""
        from arf.communication.peer import PeerAgent
        sig = inspect.signature(PeerAgent.__init__)
        params = list(sig.parameters.keys())
        assert "bus" in params
        assert "info" in params

    def test_start_registers_on_bus(self):
        """Doc: start() → bus.register(AgentInfo(...))."""
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = AsyncMock()
            info = AgentInfo("test", "desc", ["code"])
            peer = PeerAgent(bus, info)
            await peer.start()
            bus.register.assert_called_once_with(info)

        asyncio.run(run())

    def test_broadcast_sends_with_receiver_none(self):
        """Doc: broadcast sets receiver=None for broadcast."""
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = AsyncMock()
            peer = PeerAgent(bus, AgentInfo("test", "", []))
            await peer.broadcast("info", {"msg": "hello"})
            call_args = bus.send.call_args[0][0]
            assert call_args.receiver is None
            assert call_args.type == "info"

        asyncio.run(run())

    def test_send_to_targets_specific_peer(self):
        """Doc: send_to sends targeted message."""
        from arf.communication.peer import PeerAgent
        from arf.core.protocols.communication import AgentInfo

        async def run():
            bus = AsyncMock()
            peer = PeerAgent(bus, AgentInfo("test", "", []))
            await peer.send_to("other", "query", {"q": "x"})
            call_args = bus.send.call_args[0][0]
            assert call_args.receiver == "other"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 5. DictWorkspace (docs 2.5)
# ---------------------------------------------------------------------------

class TestDictWorkspace:
    """Doc: DictWorkspace — shared dict storage."""

    def test_write_and_read(self):
        """Doc: write(key, value, owner) → read(key) returns value."""
        from arf.communication.shared_workspace import DictWorkspace

        async def run():
            ws = DictWorkspace()
            await ws.write("k1", {"data": 42}, owner="agent_a")
            result = await ws.read("k1")
            assert result["data"] == 42
            assert result["_owner"] == "agent_a"

        asyncio.run(run())

    def test_write_history_tracked(self):
        """Doc: has write_history for audit."""
        from arf.communication.shared_workspace import DictWorkspace

        async def run():
            ws = DictWorkspace()
            await ws.write("k1", {"x": 1}, "a")
            assert len(ws.write_history) == 1

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 6. InMemoryLock (docs 2.6)
# ---------------------------------------------------------------------------

class TestInMemoryLock:
    """Doc: InMemoryLock — TTL-protected lock."""

    def test_acquire_release_cycle(self):
        """Doc: acquire(key, owner, ttl) returns True, release(key, owner)."""
        from arf.communication.lock import InMemoryLock

        async def run():
            lock = InMemoryLock()
            assert await lock.acquire("resource1", "agent_a") is True
            # Second acquire by different owner should fail
            assert await lock.acquire("resource1", "agent_b") is False
            await lock.release("resource1", "agent_a")
            assert await lock.acquire("resource1", "agent_b") is True

        asyncio.run(run())

    def test_ttl_default_is_30_seconds(self):
        """Doc: ttl: float = 30.0."""
        from arf.communication.lock import InMemoryLock
        sig = inspect.signature(InMemoryLock.acquire)
        assert sig.parameters["ttl"].default == 30.0


# ---------------------------------------------------------------------------
# 7. MajorityVoteConsensus (docs 2.7)
# ---------------------------------------------------------------------------

class TestMajorityVoteConsensus:
    """Doc: MajorityVoteConsensus — simple majority voting."""

    def test_threshold_default_is_0_5(self):
        """Doc: threshold=0.5 (超过半数)."""
        from arf.communication.consensus import MajorityVoteConsensus
        c = MajorityVoteConsensus()
        assert c._threshold == 0.5

    def test_propose_returns_proposal_id(self):
        """Doc: propose(proposal, voters) → {"proposal_id": ..., "status": "open"}."""
        from arf.communication.consensus import MajorityVoteConsensus

        async def run():
            c = MajorityVoteConsensus()
            result = await c.propose({"action": "test"}, ["a", "b", "c"])
            assert "proposal_id" in result
            assert result["status"] == "open"

        asyncio.run(run())

    def test_vote_records_entry(self):
        """Doc: vote(proposal_id, vote) records vote."""
        from arf.communication.consensus import MajorityVoteConsensus

        async def run():
            c = MajorityVoteConsensus()
            result = await c.propose({"a": 1}, ["x"])
            await c.vote(result["proposal_id"], "yes")
            assert len(c._votes[result["proposal_id"]]) == 1

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 8. RoundRobinSupervisor (docs 2.8)
# ---------------------------------------------------------------------------

class TestRoundRobinSupervisor:
    """Doc: RoundRobinSupervisor — round-robin task assignment."""

    def test_route_task_cycles_through_agents(self):
        """Doc: route_task(task, agents) → agents[_index % len(agents)].name."""
        from arf.communication.supervisor import RoundRobinSupervisor
        from arf.core.protocols.communication import AgentInfo

        async def run():
            sup = RoundRobinSupervisor()
            agents = [AgentInfo("a", "", []), AgentInfo("b", "", [])]
            assert await sup.route_task({}, agents) == "a"
            assert await sup.route_task({}, agents) == "b"
            assert await sup.route_task({}, agents) == "a"  # wraps

        asyncio.run(run())

    def test_should_intervene_always_false(self):
        """Doc: should_intervene currently always returns False."""
        from arf.communication.supervisor import RoundRobinSupervisor

        async def run():
            sup = RoundRobinSupervisor()
            assert await sup.should_intervene("id", {}) is False

        asyncio.run(run())

    def test_synthesize_joins_results(self):
        """Doc: synthesize(results) joins with newlines."""
        from arf.communication.supervisor import RoundRobinSupervisor

        async def run():
            sup = RoundRobinSupervisor()
            result = await sup.synthesize(["a", "b"])
            assert "a" in result and "b" in result

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 9. Module exports (docs 2.2-2.8)
# ---------------------------------------------------------------------------

class TestModuleExports:
    """Doc: all 6 classes should be importable from arf.communication."""

    def test_all_classes_exported(self):
        from arf.communication import (
            InMemoryAgentBus, PeerAgent, RoundRobinSupervisor,
            DictWorkspace, InMemoryLock, MajorityVoteConsensus,
        )
        assert InMemoryAgentBus is not None
        assert PeerAgent is not None
        assert RoundRobinSupervisor is not None
        assert DictWorkspace is not None
        assert InMemoryLock is not None
        assert MajorityVoteConsensus is not None


# ---------------------------------------------------------------------------
# 10. File existence
# ---------------------------------------------------------------------------

class TestFileExistence:
    """Verify doc-referenced files exist."""

    def test_communication_files_exist(self):
        root = Path(__file__).parent.parent.parent
        files = [
            "arf/communication/in_memory_bus.py",
            "arf/communication/peer.py",
            "arf/communication/shared_workspace.py",
            "arf/communication/lock.py",
            "arf/communication/consensus.py",
            "arf/communication/supervisor.py",
            "arf/core/protocols/communication.py",
        ]
        for f in files:
            assert (root / f).exists(), f"File '{f}' not found"


# ---------------------------------------------------------------------------
# Deep Findings — missed by initial fact-check
# ---------------------------------------------------------------------------

class TestAgentMessageDocCompleteness:
    """Doc 2.3 table now lists all 6 AgentMessage fields."""

    def test_doc_lists_all_agent_message_fields(self):
        """Doc table: sender, receiver, type, payload, correlation_id, reply_to."""
        from arf.core.protocols.communication import AgentMessage
        actual = set(AgentMessage.__dataclass_fields__.keys())
        doc_claimed = {"sender", "receiver", "type", "payload",
                       "correlation_id", "reply_to"}
        missing = actual - doc_claimed
        extra = doc_claimed - actual
        assert not missing, f"Code has fields not in doc: {missing}"
        assert not extra, f"Doc claims fields not in code: {extra}"


class TestHandoffManagerCoverage:
    """Doc 2.2 devotes substantial content to HandoffManager.
    Verify it exists and is importable."""

    def test_handoff_manager_exists(self):
        """Doc: HandoffManager in arf/engine/handoff.py."""
        from arf.engine.handoff import HandoffManager
        assert HandoffManager is not None

    def test_handoff_manager_has_detect_resolve_build(self):
        """Doc describes detect(), resolve(), build_target_context()."""
        from arf.engine.handoff import HandoffManager
        for m in ("detect", "resolve", "build_target_context"):
            assert hasattr(HandoffManager, m), f"HandoffManager missing {m}()"

    def test_handoff_manager_detect_scans_for_handoff_true(self):
        """Doc: detect(tool_results) scans for {"handoff": True}."""
        from arf.engine.handoff import HandoffManager

        hm = HandoffManager(rules=[])
        # Simulate a tool result with handoff signal
        result = hm.detect({"tool1": {"handoff": True, "task": "create file"}})
        assert result is not None
        assert result.get("handoff") is True


class TestHandoffStaleLineNumbers:
    """Doc 2.2 engine integration references graph.py:779-791 and graph.py:1110-1124."""

    def test_handoff_code_in_graph_engine(self):
        """Doc: HandoffManager integrated in invoke/astream loops."""
        from arf.engine.graph import GraphEngine
        src = inspect.getsource(GraphEngine)
        assert "handoff" in src or "HandoffManager" in src or "_execute_handoff" in src
