"""A2AHITL — delegates to DefaultHITL for sub-agent HITL events."""
from arf.core.protocols.hitl import DefaultHITL


class A2AHITL(DefaultHITL):
    """A2A sub-agent HITL. The delegate_task runner handles HITL loop
    blocking; this class ensures EventBus events are emitted correctly."""
    pass
