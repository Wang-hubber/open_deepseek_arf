"""delegate_task — spawn a sub-agent via QueuedTaskDelegator."""
import asyncio
import hashlib
import logging
from pathlib import Path

from arf.plugins.a2a.tools import _registry

logger = logging.getLogger("arf.plugins.a2a.delegate_task")

_IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "data", ".claude"}


def _snapshot_workspace(workspace_dir: str) -> dict[str, str]:
    """Scan workspace -> {relative_path: sha256_hex}."""
    snapshot = {}
    ws = Path(workspace_dir)
    if not ws.exists():
        return snapshot
    for f in ws.rglob("*"):
        if not f.is_file():
            continue
        if any(part in _IGNORED_DIRS for part in f.parts):
            continue
        try:
            rel = str(f.relative_to(ws))
            snapshot[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        except (OSError, PermissionError):
            pass
    return snapshot


async def execute(
    task: str,
    agent: str = "",
    timeout: int = 0,
    context: dict | None = None,
    _engine=None,
    session_id: str = "",
) -> dict:
    """Spawn a sub-agent to handle *task*. Uses QueuedTaskDelegator for slot scheduling.

    *_engine* is injected by ConcurrentToolExecutor — always set in production.
    """
    registry = _registry
    if registry.delegator is None:
        return {"ok": False, "error": "A2A plugin not initialized — delegator is None"}

    parent_sid = session_id
    if not parent_sid and _engine is not None:
        parent_sid = getattr(_engine, "_current_session_id", "default")

    if _engine is None:
        return {"ok": False, "error": "No engine available for sub-agent execution"}

    effective_timeout = (
        min(timeout, registry.max_task_timeout) if timeout
        else registry.max_task_timeout
    )

    task_obj = {
        "agent": agent,
        "task": task,
        "context": context or {},
    }

    async def runner(t: dict) -> dict:
        """Runner callback — executed by QueuedTaskDelegator when slot is available.

        Does NOT catch exceptions — they propagate to _run_wrapped which
        calls complete() on failure. Successful completion is handled by
        the round_end hook.
        """
        from arf.plugins.a2a.state import build_sub_state

        sub_state = build_sub_state(
            parent_session_id=parent_sid,
            task_id="",
            task=t.get("task", task),
            system_prompt="",
            model="",
            parent_state={},
        )
        sub_state["session_id"] = f"{parent_sid}--{t.get('task_id', 'unknown')}"

        # Depth limit: sub-agents cannot spawn further sub-agents
        sub_state["_tool_blacklist"] = ["delegate_task"]

        # Workspace snapshot for conflict detection
        ws_dir = getattr(engine, '_workspace_dir', '') or '.'
        sub_state["_workspace_snapshot"] = _snapshot_workspace(ws_dir)

        await asyncio.wait_for(
            _drain_stream(engine, sub_state),
            timeout=effective_timeout,
        )
        return {"ok": True, "final_state": sub_state}

    result = await registry.delegator.dispatch(parent_sid, task_obj, runner)
    return result


async def _drain_stream(engine, sub_state: dict) -> None:
    """Drain astream events — results collected by round_end hook."""
    async for _event in engine.astream(sub_state, stop_on_text=True):
        pass
