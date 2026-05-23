"""Lazy persistence -- archive agent state on shutdown, restore on startup.

Supports both sync (CLI save) and async (server shutdown) contexts.
"""
import asyncio
import json
import time
from pathlib import Path

ARCHIVE_PATH = Path("memory/archive.json")


async def save_archive_async(agent) -> None:
    """Async version -- await state_store.get() directly."""
    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        state_store = getattr(agent, "state_store", None)
        if state_store is None:
            print("[persistence] No state_store -- skipping archive")
            return

        state = await state_store.get("default")

        if state is None:
            print("[persistence] No state for 'default' session -- skipping")
            return

        data = {
            "agent_name": agent.config.name,
            "messages": state.get("messages", []),
            "context_summary": state.get("context_summary", ""),
            "current_turn": state.get("current_turn", 0),
            "metadata": state.get("metadata", {}),
            "timestamp": time.time(),
            "arf_version": "1.0",
        }
        ARCHIVE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"[persistence] Archive saved: {len(data['messages'])} messages")
    except Exception as e:
        print(f"[persistence] Archive save failed: {e}")


def save_archive(agent) -> None:
    """Sync version -- create a temp event loop for state_store.get()."""
    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        state_store = getattr(agent, "state_store", None)
        if state_store is None:
            print("[persistence] No state_store -- skipping archive")
            return

        try:
            loop = asyncio.get_running_loop()
            # We are in an async context already -- use the sync path
            state = asyncio.run_coroutine_threadsafe(
                state_store.get("default"), loop
            ).result(timeout=5)
        except RuntimeError:
            # No running loop -- create new one
            state = asyncio.new_event_loop().run_until_complete(
                state_store.get("default")
            )

        if state is None:
            print("[persistence] No state for 'default' session -- skipping")
            return

        data = {
            "agent_name": agent.config.name,
            "messages": state.get("messages", []),
            "context_summary": state.get("context_summary", ""),
            "current_turn": state.get("current_turn", 0),
            "metadata": state.get("metadata", {}),
            "timestamp": time.time(),
            "arf_version": "1.0",
        }
        ARCHIVE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"[persistence] Archive saved: {len(data['messages'])} messages")
    except Exception as e:
        print(f"[persistence] Archive save failed: {e}")


def load_archive() -> dict | None:
    if not ARCHIVE_PATH.exists():
        print("[persistence] No archive -- starting fresh")
        return None
    try:
        data = json.loads(ARCHIVE_PATH.read_text())
        print(f"[persistence] Archive loaded: {len(data.get('messages', []))} messages")
        return data
    except Exception as e:
        print(f"[persistence] Archive load failed: {e}")
        return None
