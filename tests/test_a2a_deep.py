"""Deep dual-agent A2A tests — expose design flaws before fixing them.

These tests simulate real two-agent scenarios and reveal:
1. bus.send() — queue full → silent block, no timeout
2. bus.receive() — blocking wait, no notification
3. InMemoryLock — no acquire timeout
4. PeerAgent.handoff() — hangs when peer is dead
5. MajorityVoteConsensus — no result tallying
"""

import asyncio

import pytest

from arf.core.protocols.communication import AgentMessage, AgentInfo


# ===================================================================
# FLAW 1: bus.send() blocks forever when queue is full
# ===================================================================

class TestBusSendBackpressure:
    """InMemoryAgentBus.send() silently blocks when queue reaches maxsize=100.
    There is no timeout, no QueueFull exception, no put_nowait option."""

    def test_send_blocks_when_queue_full(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus

        async def run():
            bus = InMemoryAgentBus()
            # Register receiver — creates queue with maxsize=100
            await bus.register(AgentInfo("receiver", "", []))
            # Fill the queue to capacity
            for i in range(100):
                await bus.send(AgentMessage(
                    sender="sender", receiver="receiver",
                    type="info", payload={"i": i},
                ))
            assert len(bus.sent_messages) == 100

            # 101st send() will BLOCK forever — queue is full,
            # no one is consuming. Prove it by using a timeout wrapper.
            async def blocked_send():
                await bus.send(AgentMessage(
                    sender="sender", receiver="receiver",
                    type="info", payload={"i": 101},
                ))

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(blocked_send(), timeout=0.5)

        asyncio.run(run())

    def test_send_must_have_timeout_mechanism(self):
        """Doc says 'queue满时put()阻塞，自然施加背压'. But if the
        consumer is dead or slow, the sender hangs with no recourse.
        A timeout or put_nowait option is needed."""
        from arf.communication.in_memory_bus import InMemoryAgentBus

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("worker", "", []))
            # Fill queue completely
            for i in range(100):
                await bus.send(AgentMessage(
                    sender="main", receiver="worker",
                    type="task_delegate", payload={"task": f"t{i}"},
                ))

            # Sender is now stuck — queue full, no consumer, no timeout
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    bus.send(AgentMessage(
                        sender="main", receiver="worker",
                        type="task_delegate", payload={"task": "critical"},
                    )),
                    timeout=0.5,
                )

        asyncio.run(run())


# ===================================================================
# FLAW 2: bus.receive() — no way to know if messages are waiting
# ===================================================================

class TestBusReceiveNotification:
    """receive() is a blocking async generator. No poll, no peek, no callback.
    Agent must dedicate a coroutine to listening — can't interleave with
    other work."""

    def test_receive_is_blocking_generator(self):
        from arf.communication.in_memory_bus import InMemoryAgentBus

        async def run():
            bus = InMemoryAgentBus()
            await bus.register(AgentInfo("agent_x", "", []))
            gen = bus.receive("agent_x")

            # Send a message
            await bus.send(AgentMessage(
                sender="y", receiver="agent_x", type="info", payload={},
            ))

            # Message is in queue — receive().asend() would get it.
            # But there's no way to check "is there a message?" without
            # blocking on the generator.
            assert len(bus.sent_messages) == 1

            # The only way to know is to await the generator
            msg = await gen.__anext__()
            assert msg.sender == "y"

        asyncio.run(run())

    def test_no_poll_or_peek_mechanism(self):
        """InMemoryAgentBus has no qsize(), empty(), or peek() method.
        Agents can't check if messages are waiting without blocking."""
        from arf.communication.in_memory_bus import InMemoryAgentBus

        bus = InMemoryAgentBus()
        assert not hasattr(bus, "qsize")
        assert not hasattr(bus, "pending_count")
        assert not hasattr(bus, "has_messages")


# ===================================================================
# FLAW 3: InMemoryLock.acquire() — no timeout on acquire
# ===================================================================

class TestLockAcquireTimeout:
    """InMemoryLock.acquire(key, owner, ttl=30.0) returns False if held.
    But there's no way to say 'wait up to N seconds for this lock'.
    Caller must busy-loop."""

    def test_acquire_no_wait_timeout(self):
        from arf.communication.lock import InMemoryLock

        async def run():
            lock = InMemoryLock()
            # Agent A holds the lock
            assert await lock.acquire("critical_resource", "agent_a") is True

            # Agent B tries to acquire — immediately gets False
            # There's no way to say "wait 5 seconds for this lock"
            assert await lock.acquire("critical_resource", "agent_b") is False

            # B must busy-loop:
            got_lock = False
            for _ in range(10):
                if await lock.acquire("critical_resource", "agent_b"):
                    got_lock = True
                    break
                await asyncio.sleep(0.1)
            assert got_lock is False  # A never released

        asyncio.run(run())


# ===================================================================
# FLAW 4: PeerAgent.handoff() — hangs without timeout when peer dead
# ===================================================================

class TestPeerAgentHandoffTimeout:
    """PeerAgent.handoff() sends a handoff request and waits for a response
    via async for on receive(). If the target peer never responds, this
    hangs forever — the timeout parameter exists in negotiate() but not
    handoff()."""

    def test_handoff_timeout_parameter_unused(self):
        """handoff() accepts timeout=60s but the inner receive loop
        does not enforce it — it waits for matching sender+type forever."""
        from arf.communication.peer import PeerAgent
        from arf.communication.in_memory_bus import InMemoryAgentBus

        async def run():
            bus = InMemoryAgentBus()
            # Register both agents
            await bus.register(AgentInfo("agent_a", "", ["chat"]))
            await bus.register(AgentInfo("agent_b", "", ["resource_creation"]))

            peer_a = PeerAgent(bus, AgentInfo("agent_a", "", ["chat"]))
            await peer_a.start()

            # Peer B is registered but never listens.
            # A sends handoff → message goes into B's queue → B never reads.
            # A waits forever in the async for loop inside handoff().

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    peer_a.handoff(
                        task="create tool",
                        target_capability="resource_creation",
                        timeout=0.5,  # passed to find_peer, NOT enforced in receive
                    ),
                    timeout=1.0,
                )

        asyncio.run(run())


# ===================================================================
# FLAW 5: MajorityVoteConsensus — no result tallying
# ===================================================================

class TestConsensusNoTally:
    """MajorityVoteConsensus has propose() and vote() but no way to
    check if consensus was reached. The threshold field exists but is
    never used after __init__."""

    def test_verdict_method_added(self):
        """After fix: MajorityVoteConsensus.verdict() tallies votes and
        reports whether consensus was reached (> threshold)."""
        from arf.communication.consensus import MajorityVoteConsensus
        assert hasattr(MajorityVoteConsensus, "verdict")

    def test_threshold_never_used_after_init(self):
        """threshold is stored but never referenced in propose() or vote().
        There's no logic to determine if votes > threshold."""
        import inspect
        from arf.communication.consensus import MajorityVoteConsensus
        src_propose = inspect.getsource(MajorityVoteConsensus.propose)
        src_vote = inspect.getsource(MajorityVoteConsensus.vote)
        assert "_threshold" not in src_propose
        assert "_threshold" not in src_vote


# ===================================================================
# FLAW 6: RoundRobinSupervisor — no agent health check
# ===================================================================

class TestSupervisorNoHealthCheck:
    """RoundRobinSupervisor.route_task() cycles through agents blindly.
    It doesn't check if the selected agent is alive before assigning."""

    def test_route_task_returns_empty_for_zero_agents(self):
        from arf.communication.supervisor import RoundRobinSupervisor

        async def run():
            sup = RoundRobinSupervisor()
            result = await sup.route_task({"task": "test"}, [])
            assert result == ""

        asyncio.run(run())

    def test_route_task_only_uses_modulo(self):
        """route_task() logic: agents[index % len(agents)].name.
        No health check, no status inquiry, just round-robin."""
        import inspect
        from arf.communication.supervisor import RoundRobinSupervisor
        src = inspect.getsource(RoundRobinSupervisor.route_task)
        assert "index" in src
        assert "%" in src
        assert "health" not in src
        assert "alive" not in src
        assert "status" not in src
