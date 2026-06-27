"""
[G] Shutdown — Bus closing behavior.

Test angles: [方法] [边界]
"""
import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


# ── G1 ──────────────────────────────────────────────────────────────────

async def test_shutdown_recv_send_error(bus):
    """[方法] After shutdown, recv/send both raise Exception."""
    h = await bus.connect(NodeInfo("engine/x", "engine", {}), MessageFilter())
    await bus.shutdown()

    with pytest.raises(Exception):
        await h.recv()

    with pytest.raises(Exception):
        await h.send("action", [], {})


# ── G2 ──────────────────────────────────────────────────────────────────

async def test_shutdown_with_online_nodes_no_hang(bus):
    """[边界] Shutdown with online nodes completes without hanging."""
    h1 = await bus.connect(NodeInfo("engine/a", "engine", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("engine/b", "engine", {}), MessageFilter())
    h3 = await bus.connect(NodeInfo("trace/obs", "trace", {}), MessageFilter())

    await h1.send("msg", [], {"n": 1})
    await h2.send("msg", [], {"n": 2})

    await bus.shutdown()

    # After shutdown, recv returns buffered messages first, then raises Closed.
    # Drain all buffered messages, then verify recv raises.
    for h in [h1, h2, h3]:
        # Drain buffered messages
        while True:
            try:
                m = h.try_recv()
                if m is None:
                    break
            except Exception:
                break  # closed
        # Now recv should raise (buffer empty, channel closed)
        with pytest.raises(Exception):
            await h.recv()
