"""BaseAgent — uses AgentHarness internally.

This is a thin compatibility wrapper. The actual execution is delegated
to PrimitiveAgent + AgentHarness.
"""
from __future__ import annotations
import asyncio
import json
import os
import uuid
import logging
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Any

from arf.agent.config import AgentConfig, AdvancedConfig
from arf.agent.app_context import AppContext
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult
from arf.harness.engine import AgentHarness
from arf.harness.context import PluginContext
from arf.harness.plugin_base import Plugin
from arf.harness.loader import discover_plugins, instantiate_plugins
from arf.tooling.registry import ToolRegistry
from arf.tooling.executor import ToolExecutor
from arf.event_bus import InMemoryEventBus
from arf.core.events import AgentEvent
from arf.engine.checkpoint import InMemoryStateStore, FileStateStore

logger = logging.getLogger(__name__)


def _build_call_model(config: AgentConfig):
    """Build call_model and stream_model functions from agent config."""
    from arf.core.model_adapter import ModelAdapter
    from arf.core.model_degrader import ModelDegrader

    adapters = []
    if config.model_defs:
        for md in config.model_defs:
            api_key = os.environ.get(md.get("api_key_env", ""), "placeholder")
            cfg = {
                "base_url": md.get("api_base", "https://api.deepseek.com/v1"),
                "api_key": api_key,
                "model_name": md.get("model", "deepseek-chat"),
                "context_window": md.get("context_window", 131072),
                **md.get("kwargs", {}),
            }
            adapters.append(ModelAdapter(cfg))
    elif config.models:
        for m in config.models:
            api_key = os.environ.get(m.api_key_env, "placeholder")
            cfg = {
                "base_url": m.api_base,
                "api_key": api_key,
                "model_name": m.model,
                "context_window": m.context_window,
                **getattr(m, "kwargs", {}),
            }
            adapters.append(ModelAdapter(cfg))
    else:
        raise ValueError("No model configuration found in agent.yaml")

    degrader = ModelDegrader(adapters)

    async def call_model(messages: list[dict], tools=None) -> ModelResult:
        msg = await degrader.chat_complete(messages, tools=_to_openai_tools(tools))
        tool_calls = []
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    params = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    params = {}
                tool_calls.append({"id": tc.id, "name": tc.function.name, "params": params})
        usage = {}
        if hasattr(msg, "usage") and msg.usage:
            usage = dict(msg.usage)
        return ModelResult(
            content=msg.content or "",
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=getattr(msg, "finish_reason", "stop"),
        )

    def stream_model(messages: list[dict], tools=None):
        return degrader.chat_stream_full(messages, tools=_to_openai_tools(tools))

    return call_model, stream_model


def _to_openai_tools(tools):
    if not tools:
        return None
    result = []
    for t in tools:
        if isinstance(t, dict):
            params = t.get("parameters", {})
            result.append({
                "type": "function",
                "function": {"name": t.get("name", ""), "description": t.get("description", ""),
                             "parameters": params if params else {"type": "object", "properties": {}}},
            })
    return result if result else None


def _resolve_data_dir(config: AgentConfig, ctx: AppContext | None) -> str:
    if config.data_path and config.data_path != ".":
        return str(Path(config.data_path).resolve())
    if ctx:
        return str(ctx.root.resolve() / "data")
    return str(Path(".").resolve() / "data")


def _resolve_plugin_dir(override: str | None = None) -> str:
    if override:
        return override
    import arf as _arf_pkg
    return str(Path(_arf_pkg.__file__).parent / "plugins")


class BaseAgent:
    """Compatibility wrapper — uses PrimitiveAgent + AgentHarness internally.

    All execution delegates to the new architecture. Session state is managed
    via FileStateStore for multi-round persistence.
    """

    def __init__(self, config: AgentConfig, app_context: AppContext | None = None, **override_protocols) -> None:
        self.config = config
        self._override = override_protocols

        data_dir = _resolve_data_dir(config, app_context)
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        for sub in ("memory", "state", "traces"):
            (Path(data_dir) / sub).mkdir(parents=True, exist_ok=True)

        # Infrastructure
        self._event_bus = override_protocols.pop("event_bus", InMemoryEventBus())
        self._state_store = override_protocols.pop("state_store", FileStateStore(data_dir))

        # Build call_model and stream_model
        try:
            call_model, stream_model = _build_call_model(config)
        except ValueError:
            # No model config — create a placeholder (tests use override_protocols)
            async def _noop(messages, tools=None):
                return ModelResult(content="")

            async def _noop_stream(messages, tools=None):
                if False:
                    yield
            call_model, stream_model = _noop, _noop_stream

        # Create PrimitiveAgent
        model_cfg = {"api_base": "", "api_key_env": "", "model_name": "", "context_window": 131072}
        if config.model_defs:
            md = config.model_defs[0]
            model_cfg = {"api_base": md.get("api_base", ""), "api_key_env": md.get("api_key_env", ""),
                         "model_name": md.get("model", ""), "context_window": md.get("context_window", 131072)}
        elif config.models:
            m = config.models[0]
            model_cfg = {"api_base": m.api_base, "api_key_env": m.api_key_env,
                         "model_name": m.model, "context_window": m.context_window}

        self._primitive_agent = PrimitiveAgent(
            agent_id=config.name,
            model_config=model_cfg,
            call_model=call_model,
            stream_model=stream_model,
        )

        # Resolve plugin directory
        plugins_dir = _resolve_plugin_dir(override_protocols.pop("plugins_dir", None))

        # Load plugins
        plugin_names = config.plugins if config.plugins else []
        plugin_configs = discover_plugins(plugins_dir, plugin_names)
        plugins = instantiate_plugins(plugin_configs)

        # Tool registry (minimal — full integration via MCP handled separately)
        registry = ToolRegistry()

        # Build AgentHarness
        adv = config.effective_advanced()
        self._harness = AgentHarness(
            agent=self._primitive_agent,
            plugins=plugins,
            tool_executor=ToolExecutor(registry) if plugins else None,
            event_bus=self._event_bus,
            max_turns=adv.max_turns if adv else 50,
            data_dir=data_dir,
        )

        self._active_sessions: set[str] = set()
        logger.info("BaseAgent initialized with AgentHarness: %s", config.name)

    # ── Public API ──────────────────────────────────────

    @property
    def state_store(self):
        return self._state_store

    @property
    def event_bus(self):
        return self._event_bus

    @property
    def engine(self):
        """Returns the internal AgentHarness (formerly ControlPlane)."""
        return self._harness

    async def start(self) -> None:
        pass  # MCP/file watcher start handled externally

    async def stop(self) -> None:
        for sid in list(self._active_sessions):
            try:
                state = await self._state_store.get(sid)
                if state:
                    pass  # Session close handled by caller
            except Exception:
                pass
        self._active_sessions.clear()

    def approve(self, decision_id: str, approved: bool = True) -> bool:
        """Delegate to ApprovalPlugin."""
        for p in self._harness._plugins:
            if p.name == "approval" and hasattr(p, "approve"):
                return p.approve(decision_id, approved)
        return False

    async def run(self, user_message: str, session_id: str = "default") -> str:
        """Convenience: run a single round, return final text."""
        from arf.engine.compat import collect_response
        return await collect_response(self.astream(user_message, session_id))

    async def astream(
        self, user_message: str, session_id: str = "default",
        stop_on_text: bool = False,
    ) -> AsyncIterator[AgentEvent]:
        """Main execution — delegates to AgentHarness.run()."""
        # Resolve session
        if not session_id or session_id.strip() == "":
            session_id = str(uuid.uuid4())

        # If session has state, load it into the agent
        existing = await self._state_store.get(session_id)
        if existing and existing.get("messages"):
            self._primitive_agent.state.session_id = session_id
            # Restore messages from saved state
            from arf.agent.state import Message as _M
            for m in existing["messages"]:
                if isinstance(m, dict):
                    self._primitive_agent.input(m.get("role", "user"), m.get("content", ""))

        self._active_sessions.add(session_id)

        try:
            # Delegate to AgentHarness
            async for event in self._harness.run(user_message, session_id=session_id):
                yield event

            # Save state after round
            msgs_dict = [
                {"role": m.role, "content": m.content}
                for m in self._primitive_agent.state.messages
            ]
            await self._state_store.put(session_id, {
                "session_id": session_id,
                "messages": msgs_dict,
                "session_active": True,
            })
        except Exception as exc:
            logger.exception("astream session=%s failed", session_id)
            # Emit error event
            ctx = PluginContext(agent=self._primitive_agent, session_id=session_id,
                               event_bus=self._event_bus)
            yield ctx.emit("error", {"detail": str(exc)})

    def reconfigure(self, **overrides) -> None:
        if "advanced" in overrides:
            new_data = self.config.model_dump()
            new_data["advanced"] = overrides["advanced"]
            self.config = AgentConfig(**new_data)
