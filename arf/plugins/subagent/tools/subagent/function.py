"""subagent tool — spawn isolated engine, run subtask, return summary."""
import time
import yaml
from pathlib import Path

from arf.engine.control_plane import ControlPlane
from arf.engine.checkpoint import InMemoryStateStore
from arf.event_bus import InMemoryEventBus
from arf.resources.resolver import ResourceResolver
from arf.engine.tool_executor import ConcurrentToolExecutor


# Load allowed_tools from sibling tool.yaml at module level
_tool_dir = Path(__file__).parent
_raw = yaml.safe_load((_tool_dir / "tool.yaml").read_text())
_DEFAULT_ALLOWED = set(
    _raw.get("execution", {}).get("allowed_tools", ["read", "grep", "glob", "bash"])
)


class FilteredToolProvider:
    """Wraps a parent ToolProvider, exposing only allowed tools."""

    def __init__(self, parent_provider, allowed: set[str]):
        self._parent = parent_provider
        self._allowed = allowed

    def list(self):
        return [t for t in self._parent.list() if t.name in self._allowed]

    async def list_tools(self):
        return [t for t in await self._parent.list_tools() if t.name in self._allowed]

    async def execute(self, name: str, params: dict):
        if name not in self._allowed:
            raise ValueError(f"Tool '{name}' not allowed for subagent")
        return await self._parent.execute(name, params)


async def execute(
    prompt: str,
    model: str = "",
    description: str = "",
    _engine=None,
    _state_store=None,
    _workspace: str = "",
) -> dict:
    if not prompt or not prompt.strip():
        return {"content": "Error: prompt is required", "error": True}

    # Determine model
    model_name = model or getattr(_engine, "_system_model_name", "") or "quick"

    # Build filtered tool infrastructure (handle both DefaultToolResolver and ResourceResolver)
    resolver = getattr(_engine, 'tool_resolver', None)
    tool_guard = None
    tool_boundaries = {}
    default_boundary = None
    # Inherit tool guard + boundaries from parent engine's executor
    parent_executor = getattr(_engine, 'tool_executor', None)
    if parent_executor is not None:
        tool_guard = getattr(parent_executor, '_tool_guard', None)
        tool_boundaries = getattr(parent_executor, '_tool_boundaries', {})
        default_boundary = getattr(parent_executor, '_default_boundary', None)
    if resolver is not None:
        # Legacy GraphEngine path — wrap tool provider with filter
        if hasattr(resolver, '_inner'):
            parent_tool_provider = resolver._inner._tool_provider
        else:
            parent_tool_provider = resolver._tool_provider
        filtered_provider = FilteredToolProvider(parent_tool_provider, _DEFAULT_ALLOWED)
        sub_tool_resolver = ResourceResolver(tool_provider=filtered_provider)
    else:
        sub_tool_resolver = None
    sub_tool_executor = ConcurrentToolExecutor(
        tool_resolver=sub_tool_resolver,
        tool_guard=tool_guard,
        tool_boundaries=tool_boundaries,
        default_boundary=default_boundary,
    )

    # Build isolated sub-engine
    session_id = f"sub_{description or 'task'}_{int(time.time() * 1000)}"
    sub_state_store = InMemoryStateStore()
    sub_event_bus = InMemoryEventBus()

    async def _sub_mcp_resolver(state):
        """Resolve tools from filtered resolver for ControlPlane dispatch."""
        if sub_tool_resolver:
            try:
                tools = await sub_tool_resolver.get_tool_definitions()
                return [
                    {
                        "name": t.name if hasattr(t, "name") else t.get("name", ""),
                        "description": t.description if hasattr(t, "description") else t.get("description", ""),
                        "parameters": t.parameters if hasattr(t, "parameters") else t.get("parameters", {}),
                    }
                    for t in (tools or [])
                ]
            except Exception:
                pass
        return []

    sub_engine = ControlPlane(
        max_turns=10,
        state_store=sub_state_store,
        tool_executor=sub_tool_executor,
        event_bus=sub_event_bus,
        call_model=_engine._call_model,
        workspace_dir=_workspace,
        mcp_tool_resolver=_sub_mcp_resolver,
    )

    state = {
        "session_id": session_id,
        "agent_name": "subagent",
        "messages": [{"role": "user", "content": prompt.strip()}],
        "current_model": model_name,
        "current_turn": 0,
        "interaction_round": 0,
        "context_summary": "",
        "tool_results": {},
        "plan": None,
        "metadata": {},
        "session_active": True,
    }

    try:
        result = await sub_engine.invoke(state)
    except Exception as exc:
        return {"content": f"Subagent error: {exc}", "error": True, "model": model_name}

    # Extract summary from last assistant message
    for m in reversed(result.get("messages", [])):
        if m.get("role") == "assistant" and m.get("content", "").strip():
            return {"content": m["content"].strip(), "model": model_name}

    return {"content": "(no output)", "model": model_name}
