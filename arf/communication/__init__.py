"""ARF Communication — DEPRECATED.

A2A communication is deferred. Focus on agent+subagent first.
Will be redesigned when multi-agent scheduling is needed.
"""
import warnings
warnings.warn(
    "arf.communication is deprecated. A2A communication is deferred. "
    "Use subagent plugin for agent delegation.",
    DeprecationWarning, stacklevel=2,
)

from arf.communication.in_memory_bus import InMemoryAgentBus
from arf.communication.peer import PeerAgent
from arf.communication.supervisor import RoundRobinSupervisor
from arf.communication.shared_workspace import DictWorkspace
from arf.communication.lock import InMemoryLock
from arf.communication.consensus import MajorityVoteConsensus

__all__ = ["InMemoryAgentBus", "PeerAgent", "RoundRobinSupervisor", "DictWorkspace", "InMemoryLock", "MajorityVoteConsensus"]
