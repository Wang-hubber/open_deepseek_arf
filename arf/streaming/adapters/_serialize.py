"""Shared helpers for streaming adapters."""
from dataclasses import asdict

from arf.core.events import AgentEvent


def event_to_dict(event: AgentEvent) -> dict:
    """Serialize an AgentEvent to a JSON-safe dict, dropping None values."""
    d = asdict(event)
    return {k: v for k, v in d.items() if v is not None}
