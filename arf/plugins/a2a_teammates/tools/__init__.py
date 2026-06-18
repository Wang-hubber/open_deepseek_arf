"""A2A Teammates plugin tools."""


class _TeammatesRegistry:
    """Module-level singleton bridging plugin and tool functions."""

    def __init__(self) -> None:
        self.agent_bus: object | None = None


_registry = _TeammatesRegistry()
