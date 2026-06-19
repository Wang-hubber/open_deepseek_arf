"""A2A Teammates plugin tools."""
import logging

logger = logging.getLogger("arf.plugins.a2a_teammates.tools")


class _TeammatesRegistry:
    """Module-level singleton bridging plugin and tool functions."""

    def __init__(self) -> None:
        self.agent_bus: object | None = None
        self.agents: dict[str, object] = {}  # role_name → agent instance
        self.park_coordinator = None


_registry = _TeammatesRegistry()
