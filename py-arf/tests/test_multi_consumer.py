"""
[D] Multi-consumer — load balancing foundation.

Business scenarios:
- Multiple same-type+same-filter worker nodes all receive broadcast messages.
- Application decides which worker handles it (e.g., session affinity).
- Bus guarantees message delivery, no exclusive consumption.
- When one worker goes down, standby worker connects and resumes consumption.

Test angles: [多节点] [并发]
"""
import asyncio
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


def drain_all(*handles):
    """Drain all pending node_online messages from handles using try_recv."""
    for h in handles:
        while h.try_recv() is not None:
            pass


# ── D1: broadcast received by all peers ───────────────────────────────

async def test_broadcast_received_by_all_peers(bus):
    """[多节点] One broadcast received by 3 same-type+same-filter nodes.

    Foundation guarantee: messages are not exclusive, all workers get a copy.
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    sender = await bus.connect(NodeInfo("engine/sender", "engine", {}), f)
    r1 = await bus.connect(NodeInfo("worker/1", "worker", {}), f)
    r2 = await bus.connect(NodeInfo("worker/2", "worker", {}), f)
    r3 = await bus.connect(NodeInfo("worker/3", "worker", {}), f)

    drain_all(r1, r2, r3)

    await sender.send("job", [], {"task": "compress"})

    for handle in [r1, r2, r3]:
        msg = await handle.recv()
        assert msg.msg_type == "job"
        assert msg.payload == {"task": "compress"}


# ── D2: same-type multi-worker all receive ─────────────────────────────

async def test_same_type_multi_worker_all_receive(bus):
    """[多节点] 4 worker nodes same type="model" all receive infer broadcast.

    Business: 4 model inference workers, dispatcher broadcasts prompt.
    Application-layer decides which one handles it.
    """
    f = MessageFilter(types=["infer"], to_match=ToMatch.BroadcastAndDirectedToMe)
    dispatcher = await bus.connect(NodeInfo("engine/dispatcher", "engine", {}), f)
    workers = []
    for i in range(4):
        w = await bus.connect(
            NodeInfo(f"model/worker{i}", "model", {"gpu": i}), f
        )
        workers.append(w)

    await dispatcher.send("infer", [], {"prompt": "hello world"})

    count = 0
    for w in workers:
        msg = await w.recv()
        assert msg.msg_type == "infer"
        assert msg.payload == {"prompt": "hello world"}
        count += 1
    assert count == 4


# ── D3: directed to specific worker, others don't get it ────────────────

async def test_directed_to_one_worker_ignored_by_others(bus):
    """[多节点] Directed to worker/1 → worker/2 and worker/3 don't receive.

    Business: session affinity — same-session messages directed to fixed worker.
    """
    f = MessageFilter(types=None, to_match=ToMatch.DirectedToMe)
    w1 = await bus.connect(NodeInfo("worker/1", "worker", {}), f)
    w2 = await bus.connect(NodeInfo("worker/2", "worker", {}), f)
    w3 = await bus.connect(NodeInfo("worker/3", "worker", {}), f)
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    target = NodeId("worker/1")
    await engine.send("session_msg", [target], {"session": "sid-42", "data": "hello"})

    msg1 = await w1.recv()
    assert msg1.msg_type == "session_msg"
    assert msg1.is_for(target) is True

    assert w2.try_recv() is None
    assert w3.try_recv() is None


# ── D4: concurrent recv no cross-interference ──────────────────────────

async def test_concurrent_recv_no_cross_interference(bus):
    """[并发] 3 nodes recv() concurrently — independent, no cross-talk.

    Each node has its own broadcast::Receiver. One node's recv
    does not consume messages for another node.
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), f)
    r1 = await bus.connect(NodeInfo("worker/1", "worker", {}), f)
    r2 = await bus.connect(NodeInfo("worker/2", "worker", {}), f)
    r3 = await bus.connect(NodeInfo("worker/3", "worker", {}), f)

    drain_all(r1, r2, r3)

    await sender.send("job", [], {"id": 1})

    async def recv_one(handle):
        msg = await handle.recv()
        return msg.payload["id"]

    results = await asyncio.gather(
        recv_one(r1), recv_one(r2), recv_one(r3),
    )
    assert results == [1, 1, 1]


# ── D5: broadcast semantics — not consumed after one recv ───────────────

async def test_message_not_consumed_after_one_recv(bus):
    """[多节点] After A recv() consumes, B can still recv() the same message.

    Broadcast semantics: not a queue, no exclusive consumption.
    """
    f = MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe)
    a = await bus.connect(NodeInfo("node/a", "node", {}), f)
    b = await bus.connect(NodeInfo("node/b", "node", {}), f)
    sender = await bus.connect(NodeInfo("engine/s", "engine", {}), f)

    drain_all(a, b)

    await sender.send("announce", [], {"msg": "hello all"})

    msg_a = await a.recv()
    assert msg_a.payload == {"msg": "hello all"}

    msg_b = await b.recv()
    assert msg_b.payload == {"msg": "hello all"}
    assert msg_b.id == msg_a.id  # same message


# ── D+: standby worker activates and consumes ──────────────────────────

async def test_standby_worker_activates_and_consumes(bus):
    """[多节点] Primary disconnect → standby connect → consumes subsequent messages.

    Business: primary worker is busy/crashed, standby activates and takes over.
    Bus doesn't perceive role change; application handles activation logic.
    """
    f = MessageFilter(types=["job"], to_match=ToMatch.BroadcastAndDirectedToMe)
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    # Primary worker online
    primary = await bus.connect(NodeInfo("worker/primary", "worker", {}), f)

    # Send job → primary receives
    await engine.send("job", [], {"task": "t1"})
    msg = await primary.recv()
    assert msg.payload == {"task": "t1"}

    # Primary "crashes" — disconnect
    await primary.disconnect()

    # Standby activates
    standby = await bus.connect(NodeInfo("worker/standby", "worker", {}), f)

    # Standby receives subsequent jobs
    await engine.send("job", [], {"task": "t2"})
    msg2 = await standby.recv()
    assert msg2.payload == {"task": "t2"}

    # Standby does NOT get historical message t1 (late joiner semantics)
    assert standby.try_recv() is None


async def test_primary_reconnect_after_standby_takes_over(bus):
    """[多节点] Primary disconnect → standby activates → primary reconnects → both online.

    After primary recovers, both workers are online and receive subsequent broadcasts.
    Application may use DirectedToMe for session affinity as an optimization.
    """
    f = MessageFilter(types=["job"], to_match=ToMatch.BroadcastAndDirectedToMe)
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    primary = await bus.connect(NodeInfo("worker/main", "worker", {}), f)
    await engine.send("job", [], {"task": "t1"})
    msg = await primary.recv()
    assert msg.payload == {"task": "t1"}
    await primary.disconnect()

    standby = await bus.connect(NodeInfo("worker/standby", "worker", {}), f)
    await engine.send("job", [], {"task": "t2"})
    msg2 = await standby.recv()
    assert msg2.payload == {"task": "t2"}

    # Primary recovers → reconnects
    primary2 = await bus.connect(NodeInfo("worker/main", "worker", {}), f)

    # Both receive subsequent broadcast
    await engine.send("job", [], {"task": "t3"})
    msg_p = await primary2.recv()
    assert msg_p.payload == {"task": "t3"}
    msg_s = await standby.recv()
    assert msg_s.payload == {"task": "t3"}
