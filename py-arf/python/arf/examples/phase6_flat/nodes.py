"""MemoryNode and CompactionNode Python mock implementations.

These nodes connect to the Bus, subscribe to memory_op / compact_op,
process messages on receipt, and send results back. They don't know
Engine exists — they only subscribe by msg_type and reply by `from` field.

Verification points:
  - Node independence (§10 boundary 2): Node subscribes msg_types,
    never assumes sender is Engine
  - Flat mode: all Nodes on the same Bus, filter ensures no crosstalk
"""

import asyncio
from arf import NodeId, NodeInfo, MessageFilter, ToMatch


class BusNode:
    """Base class for Bus-connected nodes.

    When the Rust `Node` trait (§1.5.2) is implemented, this base class
    is no longer needed — each Node implements `on_message()` directly.
    """

    def __init__(self, node_id: str, node_type: str, capabilities: dict):
        self.node_id = NodeId(node_id)
        self.info = NodeInfo(node_id, node_type, capabilities)
        self._handle = None
        self._task: asyncio.Task | None = None

    async def connect(self, bus, filter_types: list[str] | None = None):
        self._handle = await bus.connect(
            self.info,
            MessageFilter(
                types=filter_types,
                to_match=ToMatch.DirectedToMe,
            ),
        )
        self._task = asyncio.create_task(self._run())

    async def disconnect(self):
        if self._task:
            self._task.cancel()
            self._task = None
        if self._handle:
            await self._handle.disconnect()
            self._handle = None

    async def _run(self):
        """Subclass overrides _handle_msg(msg) -> payload | None."""
        while True:
            msg = await self._handle.recv()
            result = await self._handle_msg(msg)
            if result is not None:
                await self._handle.send(
                    msg_type=f"{msg.msg_type}_result",
                    to=[msg.sender],
                    payload=result,
                )

    async def _handle_msg(self, msg) -> dict | None:
        raise NotImplementedError


class MemoryNode(BusNode):
    """Subscribes to memory_op. On extract, mock-returns memory entries."""

    def __init__(self):
        super().__init__(
            node_id="memory/l1",
            node_type="memory",
            capabilities={"kind": "memory", "backend": "file", "tier": "l1"},
        )

    async def _handle_msg(self, msg) -> dict | None:
        action = msg.payload.get("action")
        if action == "extract":
            messages = msg.payload.get("messages", [])
            user_msgs = [m for m in messages if m.get("role") == "user"]
            return {
                "memories": [
                    {"content": m.get("content", "")[:80], "source": "user"}
                    for m in user_msgs[-3:]
                ],
                "count": min(len(user_msgs), 3),
            }
        if action == "retrieve":
            query = msg.payload.get("query", "")
            return {"memories": [], "query": query}
        return None


class CompactionNode(BusNode):
    """Subscribes to compact_op. Mock-returns compacted message list."""

    def __init__(self):
        super().__init__(
            node_id="compactor/default",
            node_type="compactor",
            capabilities={"kind": "compactor", "backend": "sliding_window"},
        )

    async def _handle_msg(self, msg) -> dict | None:
        messages = msg.payload.get("messages", [])
        keep = messages[-10:] if len(messages) > 10 else messages
        return {
            "compacted_messages": keep,
            "summary": f"Compacted {max(0, len(messages) - 10)} messages",
            "original_count": len(messages),
            "compacted_count": len(keep),
        }
