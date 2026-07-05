"""App-side approval registry — framework does not participate.

This registry is intentionally framework-agnostic: ARF's engine pool
runs the agent loop, but when an `ask`-mode tool requires human
confirmation the app records a pending request here and blocks the
tool call until a `/approve` (or `/reject`) HTTP call decides on it.

Future framework support:
    - When ARF adds a first-class `ApprovalHook` Protocol (planned for
      a later release), this class will be re-expressed as a thin
      adapter around that Protocol. Until then the dict-of-pending-
      requests in-memory model is the only contract.
"""

from typing import Callable, Optional


class ApprovalRegistry:
    """In-memory approval store for ask-mode tools.

    Lifecycle:
        1. Engine enters ask-mode and calls `request(tool, params, id)`.
        2. Server exposes `id` to the user (e.g. via SSE event or REST GET).
        3. User calls `decide(id, approved=True/False)`.
        4. Engine resumes with the decision.

    This class is deliberately NOT thread-safe — the engine runs on a
    single asyncio loop and HTTP handlers run on FastAPI's loop, so
    callers should access it from the same loop. If the deployment
    ever uses multiple workers, swap this for a Redis-backed registry.
    """

    def __init__(self) -> None:
        # request_id -> {"tool": str, "params": dict}
        self._pending: dict[str, dict] = {}
        # reserved for future per-id handler wiring (e.g. asyncio.Event)
        self._handlers: dict[str, Callable] = {}

    def request(self, tool_name: str, params: dict, request_id: str) -> None:
        """Record a pending approval request.

        Idempotent for the same `request_id`: a second call with the
        same id overwrites (it shouldn't happen in practice, but it
        keeps the registry from crashing on duplicate ids).
        """
        self._pending[request_id] = {"tool": tool_name, "params": params}

    def decide(self, request_id: str, approved: bool) -> Optional[dict]:
        """Resolve a pending request.

        Returns:
            - the stored `{"tool", "params"}` dict if `approved=True`
              (also removes it from pending);
            - `None` if `approved=False` (also removes from pending);
            - `None` if `request_id` is unknown.
        """
        entry = self._pending.pop(request_id, None)
        if entry is None:
            return None
        if approved:
            return entry
        return None

    def peek(self, request_id: str) -> Optional[dict]:
        """Inspect a pending request without consuming it.

        Useful for `/approvals/{id}` GET endpoints in follow-ups.
        """
        return self._pending.get(request_id)

    def pending_ids(self) -> list[str]:
        """List all pending request IDs (newest first is not guaranteed)."""
        return list(self._pending.keys())


# Module-level singleton — the FastAPI app imports this directly.
approvals = ApprovalRegistry()