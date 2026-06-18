"""A2A plugin tools — registry singleton bridges plugin instance to tool functions."""
import asyncio

from arf.communication.queued_delegator import QueuedTaskDelegator


class _A2ARegistry:
    """Module-level singleton so tool functions can access the delegator.

    Set by A2APlugin.__init__, read by tool function.py modules.

    ``running_sub_agents`` and ``runtime_task_ids`` are stored here (not as
    module-level vars in function.py) to prevent the editable-install finder
    from creating duplicate module instances and thus separate dicts.
    """

    def __init__(self) -> None:
        self.delegator: QueuedTaskDelegator | None = None
        self.max_task_timeout: float = 600.0
        self.engine: object | None = None  # ControlPlane ref for sub-agent astream
        self.running_sub_agents: dict[str, dict] = {}
        self.runtime_task_ids: dict[str, str] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.child_resume: str = "auto"


_registry = _A2ARegistry()
