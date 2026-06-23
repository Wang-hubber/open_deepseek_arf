"""A2A Subagents Plugin — task delegation for agent-to-agent communication.

Re-exports Plugin from plugin.py (the deep-port implementation).
"""
from arf.plugins.a2a_subagents.plugin import Plugin  # noqa: F401

__all__ = ["Plugin"]
