"""subagent tool — spawn isolated engine, run subtask, return summary."""
import time
import yaml
from pathlib import Path

from arf.engine.graph import GraphEngine
from arf.engine.checkpoint import InMemoryStateStore
from arf.event_bus import InMemoryEventBus
from arf.resources.resolver import ResourceResolver
from arf.engine.tool_executor import ConcurrentToolExecutor
from arf.errors.retry import DefaultErrorPolicy
from arf.engine.loop_strategies.react import ReActStrategy


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

    def list_kernel(self):
        return [t for t in self._parent.list_kernel() if t.name in self._allowed]

    def list_dynamic(self):
        return [t for t in self._parent.list_dynamic() if t.name in self._allowed]

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
    resolver = _engine.tool_resolver
    if hasattr(resolver, '_inner'):
        parent_tool_provider = resolver._inner._tool_provider
    else:
        parent_tool_provider = resolver._tool_provider
    filtered_provider = FilteredToolProvider(parent_tool_provider, _DEFAULT_ALLOWED)
    sub_tool_resolver = ResourceResolver(tool_provider=filtered_provider)
    sub_tool_executor = ConcurrentToolExecutor(tool_resolver=sub_tool_resolver)

    # Build isolated sub-engine
    session_id = f"sub_{description or 'task'}_{int(time.time() * 1000)}"
    sub_state_store = InMemoryStateStore()
    sub_event_bus = InMemoryEventBus()

    sub_engine = GraphEngine(
        loop_strategy=ReActStrategy(max_turns=10),
        state_store=sub_state_store,
        tool_executor=sub_tool_executor,
        tool_resolver=sub_tool_resolver,
        event_bus=sub_event_bus,
        error_policy=DefaultErrorPolicy(tool_retry=0),
        call_model=_engine._call_model,
        approval_enabled=False,
        workspace_dir=_workspace,
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
