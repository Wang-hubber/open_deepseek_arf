"""Shared state — accessed by all routers, set by server.py lifespan."""
import asyncio

_agent = None
_active_cancel_events: dict[str, asyncio.Event] = {}
