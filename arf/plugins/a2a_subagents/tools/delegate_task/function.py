"""delegate_task — spawn a sub-agent via QueuedTaskDelegator.

Two modes:
- agent="" (inherit): lightweight sub-harness with parent's model/tools/plugins
- agent="name" (YAML): full create_harness() from agent config file

Both modes run via AgentHarness.run() and complete through the delegator.
"""
import asyncio
import hashlib
import logging
import os
import uuid
from pathlib import Path

from arf.plugins.a2a_subagents.tools import _registry

logger = logging.getLogger("arf.plugins.a2a_subagents.delegate_task")

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


def _resolve_agent_config(agent_name: str) -> Path | None:
    """Look up an agent YAML config by name. Returns Path or None."""
    ws_root = Path(os.environ.get("A4A_WORKSPACE", "."))
    candidates = [
        ws_root / "builtin" / f"{agent_name}.yaml",
        ws_root / "agents" / agent_name / "agent.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


async def _add_child_task(
    parent_sid: str, task_id: str, child_sid: str,
    agent_name: str, data_dir: str, status: str = "running",
) -> None:
    """Add a child_tasks entry to the parent's state store."""
    from arf.engine.checkpoint import FileStateStore
    import time

    try:
        store = FileStateStore(data_dir)
        parent_state = await store.get(parent_sid)
        if parent_state is None:
            parent_state = {"session_id": parent_sid, "messages": []}
        parent_state.setdefault("child_tasks", []).append({
            "task_id": task_id,
            "child_session_id": child_sid,
            "agent_name": agent_name,
            "status": status,
            "created_at": time.time(),
        })
        await store.put(parent_sid, parent_state)
    except Exception:
        logger.exception("Failed to add child_tasks entry for %s", task_id)


async def execute(
    task: str,
    agent: str = "",
    timeout: int = 0,
    context: dict | None = None,
    session_id: str = "",
) -> dict:
    """Spawn a sub-agent to handle *task*.

    If *agent* names a configured agent (YAML found), creates a separate
    AgentHarness with its own config/tools/model via create_harness().
    Otherwise inherits the parent's model/tools/plugins for a lightweight
    sub-agent with the same capabilities.
    """
    registry = _registry
    if registry.delegator is None:
        return {"ok": False, "error": "A2A plugin not initialized — delegator is None"}

    parent_sid = session_id or registry.current_session_id
    if not parent_sid:
        return {"ok": False, "error": "delegate_task: no session_id available"}

    effective_timeout = (
        min(timeout, registry.max_task_timeout) if timeout
        else registry.max_task_timeout
    )

    # Resolve agent config for YAML mode
    config_path = _resolve_agent_config(agent) if agent else None

    # If agent name given but no config found, fail early
    if agent and config_path is None:
        return {
            "ok": False,
            "error": (
                f"Agent '{agent}' not found. "
                f"Check builtin/{agent}.yaml or agents/{agent}/agent.yaml exists."
            ),
        }

    task_obj = {
        "agent": agent,
        "task": task,
        "context": context or {},
    }

    # Pre-generate child session id
    child_sid = f"{parent_sid}--{uuid.uuid4().hex[:8]}"
    task_obj["_child_sid"] = child_sid

    if config_path is not None:
        # === YAML mode: full create_harness() ===
        runner = _make_yaml_runner(
            config_path, agent, parent_sid, child_sid,
            effective_timeout, registry,
        )
    else:
        # === Inherit mode: lightweight sub-harness ===
        parent_cfg = registry.parent_config
        if parent_cfg is None:
            return {"ok": False, "error": "delegate_task: parent config not captured. Ensure a2a plugin is enabled."}
        runner = _make_inherit_runner(
            parent_cfg, parent_sid, child_sid,
            effective_timeout, registry,
        )

    result = await registry.delegator.dispatch(parent_sid, task_obj, runner)

    # Update child_tasks task_id after dispatch returns it
    if result.get("task_id"):
        await _add_child_task(
            parent_sid=parent_sid,
            task_id=result["task_id"],
            child_sid=child_sid,
            agent_name=agent or "inline",
            data_dir=registry.data_dir,
        )

    return {
        "ok": True,
        **result,
        "session_id": parent_sid,
    }


def _make_yaml_runner(
    config_path, agent_name, parent_sid, child_sid, hard_timeout, registry,
):
    """Build a runner for YAML-configured sub-agent."""
    import time as _time
    import yaml as _yaml

    async def runner(t: dict) -> dict:
        from arf.harness.factory import create_harness

        task_id = t.get("_delegator_task_id", "")
        ws_root = Path(os.environ.get("A4A_WORKSPACE", "."))
        ws_dir = str(ws_root)

        # Create sub-agent harness
        try:
            harness = await create_harness(
                agent_config_path=str(config_path),
                data_dir=registry.data_dir,
            )
        except Exception as exc:
            logger.exception("Failed to create harness for %s", agent_name)
            return {"ok": False, "error": f"harness_create: {exc}"}

        # Register for frontend SSE
        rid = f"sub_{agent_name}_{uuid.uuid4().hex[:8]}"
        event_queue: asyncio.Queue = asyncio.Queue()
        registry.running_sub_agents[rid] = {
            "agent_name": agent_name,
            "session_id": child_sid,
            "parent_session_id": parent_sid,
            "status": "running",
            "task": t.get("task", ""),
            "_event_queue": event_queue,
        }

        # Cancel event for cascade
        cancel_evt = asyncio.Event()
        registry.cancel_events[child_sid] = cancel_evt

        pre_snapshot = _snapshot_workspace(ws_dir)
        tool_calls: list[dict] = []
        final_result = ""
        final_status = "completed"

        try:
            deadline = _time.time() + hard_timeout
            async for event in harness.run(
                user_message=t.get("task", ""),
                session_id=child_sid,
            ):
                await event_queue.put(event)

                if event.type == "tool_call_end":
                    tool_calls.append({
                        "tool_name": event.data.get("name", ""),
                        "success": event.data.get("success", False),
                        "duration_ms": event.data.get("duration_ms", 0),
                        "error": event.data.get("error", ""),
                    })

                if event.type == "model_call_end":
                    content = (event.data or {}).get("content", "")
                    if content:
                        final_result = content

                if event.type == "round_end":
                    # Harness finished
                    pass

                if event.type == "error":
                    final_status = "error"
                    break

                # Timeout check
                if _time.time() > deadline:
                    final_status = "error"
                    break

        except Exception as exc:
            logger.exception("Sub-agent %s failed", child_sid)
            final_status = "error"
            final_result = str(exc)
        finally:
            await event_queue.put(None)
            # Delayed cleanup
            async def _cleanup():
                await asyncio.sleep(30)
                registry.running_sub_agents.pop(rid, None)
            asyncio.ensure_future(_cleanup())

        # File change detection
        post_snapshot = _snapshot_workspace(ws_dir)
        added = [p for p in post_snapshot if p not in pre_snapshot]
        modified = [
            p for p in post_snapshot
            if p in pre_snapshot and post_snapshot[p] != pre_snapshot[p]
        ]
        deleted = [p for p in pre_snapshot if p not in post_snapshot]
        file_changes = {}
        if added or modified or deleted:
            file_changes = {
                "added": sorted(added),
                "modified": sorted(modified),
                "deleted": sorted(deleted),
            }

        # Complete via delegator
        complete_result = {
            "ok": final_status == "completed",
            "content": final_result,
            "turn_count": len(tool_calls),
            "gate_exceeded": final_status == "error",
        }
        if tool_calls:
            complete_result["tool_calls_summary"] = tool_calls
        if file_changes:
            complete_result["file_changes"] = file_changes
        if final_status == "error":
            complete_result["error"] = final_result

        await registry.delegator.complete(parent_sid, task_id, complete_result)

        if final_status == "error":
            return {"ok": False, "error": final_result}
        return {
            "ok": True,
            "result": final_result,
            "session_id": child_sid,
            "agent_name": agent_name,
            "tool_calls_summary": tool_calls,
            "file_changes": file_changes,
        }

    return runner


def _make_inherit_runner(
    parent_cfg, parent_sid, child_sid, hard_timeout, registry,
):
    """Build a runner for parent-inheriting sub-agent (replaces old Inline)."""
    import time as _time

    async def runner(t: dict) -> dict:
        from arf.agent.primitive import PrimitiveAgent
        from arf.harness.engine import AgentHarness
        from arf.agent.config import AgentConfig
        task_id = t.get("_delegator_task_id", "")
        ws_root = Path(os.environ.get("A4A_WORKSPACE", "."))
        ws_dir = str(ws_root)

        # Build PrimitiveAgent with parent's model
        agent = PrimitiveAgent(
            agent_id=f"sub_{uuid.uuid4().hex[:8]}",
            model_config=parent_cfg["model_config"],
            call_model=parent_cfg["call_model"],
            stream_model=parent_cfg["stream_model"],
        )

        # Filter plugins: exclude a2a_subagents (prevent recursion)
        parent_plugins = parent_cfg.get("plugins", [])
        filtered_plugins = [
            p for p in parent_plugins
            if getattr(p, "name", "") != "a2a_subagents"
        ]

        # Build a minimal agent config for filtering delegate_task
        parent_agent_cfg = parent_cfg.get("agent_config")
        sub_agent_cfg = None
        if parent_agent_cfg is not None:
            # Clone without a2a_subagents plugin
            sub_plugins = [
                p for p in (parent_agent_cfg.plugins or [])
                if p != "a2a_subagents"
            ]
            sub_agent_cfg = AgentConfig(
                name="inline_sub",
                model_defs=parent_agent_cfg.model_defs,
                models=parent_agent_cfg.models,
                plugins=sub_plugins,
                tools=parent_agent_cfg.tools or [],
                system_prompt=parent_agent_cfg.system_prompt,
                session_mode="auto",
            )

        # Create sub-harness with shared tool_manager
        harness = AgentHarness(
            agent=agent,
            plugins=filtered_plugins,
            tool_manager=parent_cfg.get("tool_manager"),
            agent_config=sub_agent_cfg,
            event_bus=parent_cfg.get("event_bus"),
            max_turns=parent_cfg.get("max_turns", 50),
            data_dir=parent_cfg.get("data_dir", "./data"),
        )

        # Cancel event for cascade
        cancel_evt = asyncio.Event()
        registry.cancel_events[child_sid] = cancel_evt

        pre_snapshot = _snapshot_workspace(ws_dir)
        tool_calls: list[dict] = []
        final_result = ""
        final_status = "completed"

        try:
            deadline = _time.time() + hard_timeout
            async for event in harness.run(
                user_message=t.get("task", ""),
                session_id=child_sid,
            ):
                if event.type == "tool_call_end":
                    tool_calls.append({
                        "tool_name": event.data.get("name", ""),
                        "success": event.data.get("success", False),
                        "duration_ms": event.data.get("duration_ms", 0),
                        "error": event.data.get("error", ""),
                    })

                if event.type == "model_call_end":
                    content = (event.data or {}).get("content", "")
                    if content:
                        final_result = content

                if event.type == "error":
                    final_status = "error"
                    break

                if _time.time() > deadline:
                    final_status = "error"
                    break

        except Exception as exc:
            logger.exception("Sub-agent %s failed", child_sid)
            final_status = "error"
            final_result = str(exc)

        # File change detection
        post_snapshot = _snapshot_workspace(ws_dir)
        added = [p for p in post_snapshot if p not in pre_snapshot]
        modified = [
            p for p in post_snapshot
            if p in pre_snapshot and post_snapshot[p] != pre_snapshot[p]
        ]
        deleted = [p for p in pre_snapshot if p not in post_snapshot]
        file_changes = {}
        if added or modified or deleted:
            file_changes = {
                "added": sorted(added),
                "modified": sorted(modified),
                "deleted": sorted(deleted),
            }

        # Complete via delegator
        complete_result = {
            "ok": final_status == "completed",
            "content": final_result,
            "turn_count": len(tool_calls),
            "gate_exceeded": final_status == "error",
        }
        if tool_calls:
            complete_result["tool_calls_summary"] = tool_calls
        if file_changes:
            complete_result["file_changes"] = file_changes
        if final_status == "error":
            complete_result["error"] = final_result

        await registry.delegator.complete(parent_sid, task_id, complete_result)

        if final_status == "error":
            return {"ok": False, "error": final_result}
        return {
            "ok": True,
            "result": final_result,
            "session_id": child_sid,
            "agent_name": "inline",
            "tool_calls_summary": tool_calls,
            "file_changes": file_changes,
        }

    return runner
