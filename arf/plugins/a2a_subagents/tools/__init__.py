"""A2A plugin tools — registry singleton bridges plugin instance to tool functions."""
import asyncio

from arf.communication.queued_delegator import QueuedTaskDelegator


class _A2ARegistry:
    """Module-level singleton so tool functions can access the delegator.

    Set by Plugin.__init__, read by tool function.py modules.

    ``running_sub_agents`` is stored here (not as module-level vars in
    function.py) to prevent the editable-install finder from creating
    duplicate module instances and thus separate dicts.
    """

    def __init__(self) -> None:
        self.delegator: QueuedTaskDelegator | None = None
        self.max_task_timeout: float = 600.0
        self.running_sub_agents: dict[str, dict] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.child_resume: str = "auto"

        # Session context — updated by Plugin at each checkpoint
        self.current_session_id: str = ""
        self.data_dir: str = "./data"

        # Parent harness config for inline-like mode (agent="")
        # Captured by Plugin at session_start
        self.parent_config: dict | None = None


_registry = _A2ARegistry()
