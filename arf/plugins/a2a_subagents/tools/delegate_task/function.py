"""delegate_task — spawn a sub-agent via QueuedTaskDelegator.

Two modes:
- Inline (agent=""): sub-state on parent's engine, homogeneous (same tools/model)
- External (agent="name"): separate BaseAgent from YAML, heterogeneous (own config)

External mode supports: event queue for frontend SSE, HITL via ask_user,
deadline-based timeout (refreshed per human interaction).
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

# Sub-agent registry and task-id mapping are stored on _registry (module-level
# singleton in tools/__init__.py) so that api/chat.py and function.py always
# share the same dicts, even if the editable-install finder creates separate
# module instances.


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
    import yaml
    ws_root = Path(os.environ.get("A4A_WORKSPACE", "."))
    candidates = [
        ws_root / "builtin" / f"{agent_name}.yaml",
        ws_root / "agents" / agent_name / "agent.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


async def _add_child_task(engine, parent_sid: str, task_id: str,
                           child_sid: str, agent_name: str,
                           status: str = "running") -> None:
    """Add a child_tasks entry to the parent's state store."""
    import time
    try:
        parent_state = await engine.state_store.get(parent_sid)
        if parent_state is None:
            parent_state = {"session_id": parent_sid, "messages": []}
        parent_state.setdefault("child_tasks", []).append({
            "task_id": task_id,
            "child_session_id": child_sid,
            "agent_name": agent_name,
            "status": status,
            "created_at": time.time(),
        })
        await engine.state_store.put(parent_sid, parent_state)
        logger.debug("Added child_tasks entry: %s -> %s", task_id, child_sid)
    except Exception:
        logger.exception("Failed to add child_tasks entry for %s", task_id)


async def execute(
    task: str,
    agent: str = "",
    timeout: int = 0,
    context: dict | None = None,
    _engine=None,
    session_id: str = "",
) -> dict:
    """Spawn a sub-agent to handle *task*.

    If *agent* names a configured agent (YAML found), creates a separate
    BaseAgent with its own config/tools/model. Otherwise runs inline on
    the parent's engine.
    """
    registry = _registry
    if registry.delegator is None:
        return {"ok": False, "error": "A2A plugin not initialized — delegator is None"}

    parent_sid = session_id
    if not parent_sid and _engine is not None:
        parent_sid = getattr(_engine, "_current_session_id", "default")

    if _engine is None:
        return {"ok": False, "error": "delegate_task: _engine not injected by tool executor. Ensure the a2a plugin is enabled and ConcurrentToolExecutor is passing DI params correctly."}

    effective_timeout = (
        min(timeout, registry.max_task_timeout) if timeout
        else registry.max_task_timeout
    )

    # Resolve agent config for external mode
    config_path = _resolve_agent_config(agent) if agent else None

    # If agent name given but no config found, fail clearly
    if agent and config_path is None:
        return {
            "ok": False,
            "error": f"Agent '{agent}' not found. Check builtin/{agent}.yaml or agents/{agent}/agent.yaml exists.",
        }

    task_obj = {
        "agent": agent,
        "task": task,
        "context": context or {},
    }

    if config_path is not None:
        # === External mode: separate BaseAgent ===
        runtime_id = f"sub_{agent}_{uuid.uuid4().hex[:8]}"
        task_obj["runtime_id"] = runtime_id
        # Pre-generate child session id so we can record it in child_tasks
        resume_session = task_obj.get("resume_session", "") or (context or {}).get("resume_session", "")
        child_sid = resume_session or f"a2a_{agent}_{uuid.uuid4().hex[:8]}"
        task_obj["_child_sid"] = child_sid

        # Write child_tasks entry BEFORE dispatch to prevent race with round_end
        import time as _time
        _parent_state = await _engine.state_store.get(parent_sid)
        if _parent_state is None:
            _parent_state = {"session_id": parent_sid, "messages": []}
        _parent_state.setdefault("child_tasks", []).append({
            "task_id": "",  # filled after dispatch returns
            "child_session_id": child_sid,
            "agent_name": agent,
            "status": "pending",
            "created_at": _time.time(),
        })
        await _engine.state_store.put(parent_sid, _parent_state)

        result = await _dispatch_external(
            task_obj, agent, config_path, parent_sid, effective_timeout, runtime_id)
        # Update task_id after dispatch
        if result.get("task_id"):
            _updated = await _engine.state_store.get(parent_sid)
            if _updated:
                for ct in _updated.get("child_tasks", []):
                    if ct.get("child_session_id") == child_sid:
                        ct["task_id"] = result["task_id"]
                        break
                await _engine.state_store.put(parent_sid, _updated)
        return {
            "ok": True,
            **result,
            "runtime_id": runtime_id,
            "agent_name": agent,
            "task": task,
            "session_id": parent_sid,
        }

    # === Inline mode: sub-state on parent engine ===
    async def runner(t: dict) -> dict:
        from arf.plugins.a2a_subagents.state import build_sub_state

        sub_state = build_sub_state(
            parent_session_id=parent_sid,
            task_id="",
            task=t.get("task", task),
            system_prompt="",
            model="",
            parent_state={},
        )
        child_sid = f"{parent_sid}--{t.get('task_id', 'unknown')}"
        sub_state["session_id"] = child_sid
        sub_state["_tool_blacklist"] = ["delegate_task"]
        ws_dir = getattr(_engine, '_workspace_dir', '') or '.'
        pre_snapshot = _snapshot_workspace(ws_dir)
        t["_pre_snapshot"] = pre_snapshot
        sub_state["_workspace_snapshot"] = t["_pre_snapshot"]

        # Create shared tool_calls ref for A2ATaskLifecycle
        tool_calls_ref: list[dict] = []
        t["_tool_calls_ref"] = tool_calls_ref

        # Write child_tasks entry BEFORE async work — prevents race with round_end
        await _add_child_task(_engine, parent_sid, t.get("task_id", ""), child_sid, agent or "inline")

        # Inject A2ATaskLifecycle so inline sub-agent results are recorded
        _original_lifecycle = _engine._task_lifecycle
        from arf.plugins.a2a_subagents.task_lifecycle import A2ATaskLifecycle
        _engine._task_lifecycle = A2ATaskLifecycle(
            _engine._task_lifecycle._event_bus, registry.delegator,
            parent_sid=parent_sid, child_sid=child_sid, task_id=t.get("task_id", ""),
            pre_snapshot=t.get("_pre_snapshot"),
            tool_calls_ref=t.get("_tool_calls_ref"),
        )
        try:
            tool_calls = await asyncio.wait_for(
                _drain_stream(_engine, sub_state, tool_calls_ref=tool_calls_ref),
                timeout=effective_timeout,
            )
        finally:
            _engine._task_lifecycle = _original_lifecycle

        sub_state["_tool_calls_summary"] = tool_calls

        # Fallback: ensure delegator task is completed even without kernel__task_complete
        task_id_inline = t.get("_delegator_task_id", "")
        if task_id_inline:
            try:
                await registry.delegator.complete(
                    parent_sid, task_id_inline,
                    {
                        "ok": True,
                        "content": sub_state.get("messages", [])[-1].get("content", "")
                        if sub_state.get("messages") else "",
                        "turn_count": sub_state.get("current_turn", 0),
                        "gate_exceeded": False,
                    },
                )
            except Exception:
                pass  # delegator.complete is idempotent — ignore race

        return {"ok": True, "final_state": sub_state}

    result = await registry.delegator.dispatch(parent_sid, task_obj, runner)
    return result


async def _dispatch_external(
    task_obj: dict,
    agent_name: str,
    config_path: Path,
    parent_sid: str,
    hard_timeout: float,
    runtime_id: str,
) -> dict:
    """Dispatch to a separate BaseAgent with its own config."""
    import time as _time
    import yaml as _yaml

    task = task_obj["task"]
    resume_session = task_obj.get("resume_session", "") or (task_obj.get("context") or {}).get("resume_session", "")

    async def runner(t: dict) -> dict:
        from arf.agent.base import BaseAgent
        from arf.agent.config import AgentConfig
        from arf.agent.app_context import AppContext

        rid = t.get("runtime_id", runtime_id)
        sid = t.get("_child_sid") or resume_session or f"a2a_{agent_name}_{uuid.uuid4().hex[:8]}"

        # Create queues and register entry BEFORE any file I/O or agent init.
        # If runner crashes during init, the entry still exists so the
        # frontend can connect and see the error instead of a "not found" flash.
        event_queue: asyncio.Queue = asyncio.Queue()
        answer_queue: asyncio.Queue = asyncio.Queue()
        consumer_ready: asyncio.Event = asyncio.Event()

        _registry.running_sub_agents[rid] = {
            "agent": None,
            "agent_name": agent_name,
            "session_id": sid,
            "parent_session_id": parent_sid,
            "status": "idle",
            "task": task,
            "result": "",
            "error": "",
            "_event_queue": event_queue,
            "_answer_queue": answer_queue,
            "_consumer_ready": consumer_ready,
        }

        ws_root = Path(os.environ.get("A4A_WORKSPACE", Path(__file__).parent.parent.parent.parent))

        try:
            with open(config_path, encoding="utf-8") as f:
                data = _yaml.safe_load(f)
        except Exception as exc:
            _registry.running_sub_agents[rid]["status"] = "error"
            _registry.running_sub_agents[rid]["error"] = f"Config read failed: {exc}"
            await event_queue.put({
                "type": "error",
                "data": {"detail": f"加载 {agent_name} 配置失败: {exc}"},
            })
            await event_queue.put(None)
            return {"ok": False, "error": f"config_read: {exc}"}

        try:
            prompt_dir = config_path.parent / "system_prompt" / agent_name
            shared_dir = config_path.parent / "system_prompt" / "_shared"

            data.setdefault("system_prompt", {}).setdefault("prefix", {})
            data["system_prompt"]["prefix"]["role"] = (
                prompt_dir / "role.md"
            ).read_text(encoding="utf-8")

            rules = ""
            if shared_dir.exists():
                shared_files = sorted(
                    f for f in shared_dir.iterdir()
                    if f.suffix == ".md" and not f.name.endswith("_en.md")
                )
                parts = []
                for sf in shared_files:
                    content = sf.read_text(encoding="utf-8").strip()
                    if content:
                        parts.append(content)
                if parts:
                    rules = "\n\n".join(parts) + "\n\n"
            rules += (prompt_dir / "critical_rules.md").read_text(encoding="utf-8")
            data["system_prompt"]["prefix"]["critical_rules"] = rules

            config = AgentConfig(**data)
            ctx = AppContext(root=ws_root)
            sub_agent = BaseAgent(config, app_context=ctx)
            _registry.running_sub_agents[rid]["agent"] = sub_agent

            # Inject A2A-specific HITL and TaskLifecycle protocols
            from arf.plugins.a2a_subagents.hitl import A2AHITL
            from arf.plugins.a2a_subagents.task_lifecycle import A2ATaskLifecycle

            _delegator_task_id = t.get("_delegator_task_id", "")
            sub_agent._engine._hitl = A2AHITL(
                sub_agent._engine.event_bus, sub_agent.state_store)
            sub_agent._engine._task_lifecycle = A2ATaskLifecycle(
                sub_agent._engine.event_bus, _registry.delegator,
                parent_sid=parent_sid, child_sid=sid, task_id=_delegator_task_id,
                pre_snapshot=t.get("_pre_snapshot"),
                tool_calls_ref=t.get("_tool_calls_ref"),
            )
            # Inject cancel_event for cascade cancel
            import asyncio as _asyncio
            cancel_evt = _asyncio.Event()
            _registry.cancel_events[sid] = cancel_evt
            sub_agent._engine.set_cancel_event(cancel_evt)
        except Exception as exc:
            _registry.running_sub_agents[rid]["status"] = "error"
            _registry.running_sub_agents[rid]["error"] = f"Agent init failed: {exc}"
            await event_queue.put({
                "type": "error",
                "data": {"detail": f"初始化 {agent_name} 失败: {exc}"},
            })
            await event_queue.put(None)
            return {"ok": False, "error": f"agent_init: {exc}"}

        sub_agent = _registry.running_sub_agents[rid]["agent"]

        # Pre-execution workspace snapshot for file change detection
        ws_dir = getattr(sub_agent._engine, '_workspace_dir', '') or '.'
        pre_snapshot = _snapshot_workspace(ws_dir) if ws_dir != '.' else {}
        tool_calls: list[dict] = []
        t["_pre_snapshot"] = pre_snapshot
        t["_tool_calls_ref"] = tool_calls

        final_result = ""
        final_status = "completed"
        _deadline = _time.time() + hard_timeout

        try:
            await sub_agent.start()

            # For auto-mode agents, skip consumer wait — they run autonomously.
            # For ask-mode agents, wait for frontend SSE consumer to connect.
            _session_mode = data.get("session_mode", "auto")
            if _session_mode == "auto":
                consumer_ready.set()  # auto-proceed, no frontend needed
            else:
                try:
                    await asyncio.wait_for(consumer_ready.wait(), timeout=30)
                except asyncio.TimeoutError:
                    _registry.running_sub_agents[rid]["status"] = "error"
                    _registry.running_sub_agents[rid]["error"] = "前端连接超时"
                    return {"ok": False, "error": "consumer_connect_timeout"}
            _registry.running_sub_agents[rid]["status"] = "running"
            # Update child_tasks from pending to running
            try:
                _ps = await sub_agent.state_store.get(parent_sid)
                if _ps:
                    for _ct in _ps.get("child_tasks", []):
                        if _ct.get("child_session_id") == sid:
                            _ct["status"] = "running"
                            break
                    await sub_agent.state_store.put(parent_sid, _ps)
            except Exception:
                logger.exception("Failed to update child_tasks status to running")

            message = task
            while True:
                _remaining = _deadline - _time.time()
                if _remaining <= 0:
                    final_status = "error"
                    _registry.running_sub_agents[rid]["error"] = "任务执行超时"
                    await event_queue.put({
                        "type": "error",
                        "data": {"detail": "任务执行超时，已取消"},
                    })
                    break

                round_content = ""
                try:
                    async for event in sub_agent.astream(message, session_id=sid, stop_on_text=True):
                        await event_queue.put(event)
                        if event.type == "tool_call_end":
                            tool_calls.append({
                                "tool_name": event.data.get("tool_name", ""),
                                "success": event.data.get("success", False),
                                "duration_ms": event.data.get("duration_ms", 0),
                                "error": event.data.get("error", ""),
                            })
                        if event.type == "model_call_end":
                            content = (getattr(event, "data", None) or {}).get("content", "")
                            if content:
                                round_content = content
                                final_result = content
                except Exception as exc:
                    final_status = "error"
                    _registry.running_sub_agents[rid]["error"] = str(exc)
                    await event_queue.put({
                        "type": "error",
                        "data": {"detail": str(exc)},
                    })
                    break

                try:
                    state = await sub_agent.state_store.get(sid)
                except Exception:
                    state = None

                pending_decision = state.get("_pending_human_decision") if state else None
                if not pending_decision:
                    break

                # HITL
                _registry.running_sub_agents[rid]["status"] = "waiting_human"
                _registry.running_sub_agents[rid]["_pending_decision"] = pending_decision
                await event_queue.put({
                    "type": "need_human_input",
                    "data": {
                        "runtime_id": rid,
                        "agent_name": agent_name,
                        "session_id": sid,
                        "question": pending_decision.get("question", ""),
                        "options": pending_decision.get("options", []),
                        "context": pending_decision.get("context", ""),
                        "task_id": pending_decision.get("task_id", ""),
                    },
                })

                _remaining = _deadline - _time.time()
                if _remaining <= 0:
                    _registry.running_sub_agents[rid]["status"] = "error"
                    _registry.running_sub_agents[rid]["error"] = "任务执行超时"
                    await event_queue.put({
                        "type": "error", "data": {"detail": "任务执行超时，已取消"},
                    })
                    await event_queue.put(None)
                    task_id = t.get("_delegator_task_id", "")
                    if task_id:
                        await _registry.delegator.complete(parent_sid, task_id, {
                            "ok": False, "error": "timeout", "agent_name": agent_name,
                        })
                    return {"ok": False, "error": "timeout"}

                try:
                    message = await asyncio.wait_for(answer_queue.get(), timeout=_remaining)
                except asyncio.TimeoutError:
                    _registry.running_sub_agents[rid]["status"] = "error"
                    _registry.running_sub_agents[rid]["error"] = "任务执行超时"
                    await event_queue.put({"type": "error", "data": {"detail": "任务执行超时，已取消"}})
                    await event_queue.put(None)
                    task_id = t.get("_delegator_task_id", "")
                    if task_id:
                        await _registry.delegator.complete(parent_sid, task_id, {"ok": False, "error": "timeout", "agent_name": agent_name})
                    return {"ok": False, "error": "timeout"}

                if message == "__stop__":
                    _registry.running_sub_agents[rid]["status"] = "error"
                    _registry.running_sub_agents[rid]["error"] = "用户停止了会话"
                    await event_queue.put({"type": "error", "data": {"detail": "用户停止了会话"}})
                    await event_queue.put(None)
                    task_id = t.get("_delegator_task_id", "")
                    if task_id:
                        await _registry.delegator.complete(parent_sid, task_id, {"ok": False, "error": "stopped_by_user", "agent_name": agent_name})
                    return {"ok": False, "error": "stopped_by_user"}

                _deadline = _time.time() + hard_timeout
                _registry.running_sub_agents[rid]["status"] = "running"
                await event_queue.put({"type": "human_input_provided", "data": {"runtime_id": rid}})

            # Post-execution file change detection
            post_snapshot = _snapshot_workspace(ws_dir) if ws_dir != '.' else {}
            added = [p for p in post_snapshot if p not in pre_snapshot]
            modified = [p for p in post_snapshot if p in pre_snapshot and post_snapshot[p] != pre_snapshot[p]]
            deleted = [p for p in pre_snapshot if p not in post_snapshot]
            file_changes = {}
            if added or modified or deleted:
                file_changes = {
                    "added": sorted(added),
                    "modified": sorted(modified),
                    "deleted": sorted(deleted),
                }

            task_id = t.get("_delegator_task_id", "")
            _registry.running_sub_agents[rid]["status"] = final_status
            _registry.running_sub_agents[rid]["result"] = final_result

            if task_id:
                complete_result = {
                    "ok": final_status == "completed",
                    "result": final_result,
                    "content": final_result,
                    "agent_name": agent_name,
                    "error": _registry.running_sub_agents[rid].get("error", ""),
                }
                if tool_calls:
                    complete_result["tool_calls_summary"] = tool_calls
                if file_changes:
                    complete_result["file_changes"] = file_changes
                await _registry.delegator.complete(parent_sid, task_id, complete_result)

            await event_queue.put(None)

            if final_status == "error":
                return {"ok": False, "error": _registry.running_sub_agents[rid].get("error", "")}
            return {
                "ok": True, "result": final_result, "session_id": sid, "agent_name": agent_name,
                "tool_calls_summary": tool_calls,
                "file_changes": file_changes,
            }
        finally:
            await sub_agent.stop()
            async def _delayed_cleanup():
                await asyncio.sleep(30)
                _registry.running_sub_agents.pop(rid, None)
                _registry.runtime_task_ids.pop(rid, None)
            asyncio.ensure_future(_delayed_cleanup())

    result = await _registry.delegator.dispatch(parent_sid, task_obj, runner)
    return {
        "ok": True,
        **result,
        "runtime_id": runtime_id,
        "agent_name": agent_name,
        "task": task,
        "session_id": parent_sid,
    }


async def _drain_stream(engine, sub_state: dict,
                         tool_calls_ref: list | None = None) -> list[dict]:
    """Drain astream events, collect tool_call_end events, return tool_calls_summary.

    The round_end hook (in plugin.py) handles completion via _collect_result.
    This function supplements that result with tool_call metadata.

    If *tool_calls_ref* is provided, it is used as the collector instead of
    creating a new list, allowing A2ATaskLifecycle to share the same reference.
    """
    tool_calls = tool_calls_ref if tool_calls_ref is not None else []
    async for event in engine.astream(sub_state, stop_on_text=True):
        if event.type == "tool_call_end":
            tool_calls.append({
                "tool_name": event.data.get("tool_name", ""),
                "success": event.data.get("success", False),
                "duration_ms": event.data.get("duration_ms", 0),
                "error": event.data.get("error", ""),
            })
    return tool_calls
