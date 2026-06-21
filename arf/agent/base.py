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
from arf.mcp.client_manager import McpClientManager
from arf.skills.use_skill_tool import execute as use_skill_execute
from arf.skills.ask_user_tool import execute as ask_user_execute
from arf.skills.task_complete_tool import execute as task_complete_execute
from arf.skills.skill_index import SkillIndex
from arf.event_bus import InMemoryEventBus
from arf.core.events import AgentEvent
from arf.engine.checkpoint import InMemoryStateStore, FileStateStore

logger = logging.getLogger(__name__)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Merge override dict into base dict in-place. Lists are replaced, not extended."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


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
                "message_format": md.get("message_format", "openai"),
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
                "message_format": getattr(m, "message_format", "openai"),
                **getattr(m, "kwargs", {}),
            }
            adapters.append(ModelAdapter(cfg))
    else:
        raise ValueError("No model configuration found in agent.yaml")

    degrader = ModelDegrader(adapters)

    async def call_model(messages: list[dict], tools=None) -> ModelResult:
        msg = await degrader.chat_complete(messages, tools=tools)
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
        return degrader.chat_stream_full(messages, tools=tools)

    return call_model, stream_model


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

        # Merge plugins_config overrides (from agent.yaml) into each plugin's config
        for pc in plugin_configs:
            user_override = config.plugins_config.get(pc["name"], {})
            if user_override:
                _deep_merge(pc.setdefault("config", {}), user_override)

        plugins = instantiate_plugins(plugin_configs)

        # Build McpClientManager — unified tool gateway
        tools_dir = Path(config.tools_dir)
        skills_dir = Path(config.skills_dir)
        if not tools_dir.is_absolute():
            tools_dir = Path.cwd() / tools_dir
        if not skills_dir.is_absolute():
            skills_dir = Path.cwd() / skills_dir

        tool_manager = McpClientManager(
            tools_dir=tools_dir,
            skills_dir=skills_dir,
            models_dir=Path("models"),
            plugins_dir=Path(plugins_dir),
            mcp_servers=config.mcp_servers,
            plugin_names=config.plugins if config.plugins else [],
            plugin_configs=config.plugins_config,
        )
        # Register kernel tool executors
        tool_manager.register_kernel_tool("use_skill", use_skill_execute)
        tool_manager.register_kernel_tool("ask_user", ask_user_execute)
        tool_manager.register_kernel_tool("task_complete", task_complete_execute)

        # Initialize skill index for use_skill
        skill_index = SkillIndex(skills_dir)
        skill_index.scan()
        import arf.skills.use_skill_tool as _use_skill_mod
        _use_skill_mod._index = skill_index

        self._skill_index = skill_index
        self._tool_manager = tool_manager

        # Resolve allow_paths — relative paths are relative to app_context.root
        if config.allow_paths and app_context is not None:
            root = app_context.root.resolve()
            config.allow_paths = [
                str(root / p) if not Path(p).is_absolute() else p
                for p in config.allow_paths
            ]

        # Build AgentHarness
        adv = config.effective_advanced()
        self._harness = AgentHarness(
            agent=self._primitive_agent,
            plugins=plugins,
            tool_manager=tool_manager,
            agent_config=config,
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
        await self._tool_manager.start()

    async def stop(self) -> None:
        await self._tool_manager.stop()
        for sid in list(self._active_sessions):
            try:
                state = await self._state_store.get(sid)
                if state:
                    pass
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

        # Reset agent state for the target session
        self._primitive_agent.state.session_id = session_id
        self._primitive_agent.state.messages.clear()
        self._primitive_agent.state.waiting.clear()

        # Restore messages if we have saved state for this session
        existing = await self._state_store.get(session_id)
        if existing and existing.get("messages"):
            from arf.agent.state import Message as _M
            for m in existing["messages"]:
                if isinstance(m, dict):
                    self._primitive_agent.input(role=m.get("role", "user"), content=m.get("content", ""))

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
            yield ctx.emit(event_type="error", data={"detail": str(exc)})

    def reconfigure(self, **overrides) -> None:
        if "advanced" in overrides:
            new_data = self.config.model_dump()
            new_data["advanced"] = overrides["advanced"]
            self.config = AgentConfig(**new_data)
