"""A2A plugin tools — registry singleton bridges plugin instance to tool functions."""
from arf.communication.queued_delegator import QueuedTaskDelegator


class _A2ARegistry:
    """Module-level singleton so tool functions can access the delegator.

    Set by A2APlugin.__init__, read by tool function.py modules.
    """

    def __init__(self) -> None:
        self.delegator: QueuedTaskDelegator | None = None
        self.max_task_timeout: float = 600.0
        self.engine: object | None = None  # ControlPlane ref for sub-agent astream


_registry = _A2ARegistry()
