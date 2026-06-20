"""AgentHarnessFactory — assemble PrimitiveAgent + AgentHarness from config.

Replaces BaseAgent.__init__ DI assembly with a streamlined factory.
"""
from __future__ import annotations
import os
import json
import logging
from pathlib import Path
from typing import Any

from arf.agent.primitive import PrimitiveAgent
from arf.agent.config import AgentConfig
from arf.agent.state import ModelResult
from arf.harness.engine import AgentHarness
from arf.harness.config import HarnessConfig
from arf.harness.loader import discover_plugins, instantiate_plugins
from arf.tooling.registry import ToolRegistry
from arf.tooling.executor import ToolExecutor

logger = logging.getLogger("arf.harness.factory")


def _build_call_model(model_defs: list[dict], models: list) -> Any:
    """Build a call_model function from model definitions using ModelAdapter."""
    from arf.core.model_adapter import ModelAdapter
    from arf.core.model_degrader import ModelDegrader

    adapters = []
    if model_defs:
        for md in model_defs:
            api_key = os.environ.get(md.get("api_key_env", ""), "")
            cfg = {
                "base_url": md.get("api_base", "https://api.deepseek.com/v1"),
                "api_key": api_key,
                "model_name": md.get("model", "deepseek-chat"),
                "context_window": md.get("context_window", 131072),
                **md.get("kwargs", {}),
            }
            adapters.append(ModelAdapter(cfg))
    elif models:
        for m in models:
            api_key = os.environ.get(m.api_key_env, "")
            cfg = {
                "base_url": m.api_base,
                "api_key": api_key,
                "model_name": m.model,
                "context_window": m.context_window,
                **getattr(m, "kwargs", {}),
            }
            adapters.append(ModelAdapter(cfg))

    if not adapters:
        raise ValueError("No model configuration found. Add model_defs or models to agent.yaml.")

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

    return call_model


def _to_openai_tools(tools):
    """Convert framework ToolDefinition list to OpenAI tool format."""
    if not tools:
        return None
    result = []
    for t in tools:
        if isinstance(t, dict):
            params = t.get("parameters", {})
            result.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": params if params else {"type": "object", "properties": {}},
                },
            })
        else:
            params = getattr(t, "parameters", {})
            result.append({
                "type": "function",
                "function": {
                    "name": getattr(t, "name", ""),
                    "description": getattr(t, "description", ""),
                    "parameters": params if params else {"type": "object", "properties": {}},
                },
            })
    return result


def _build_tool_registry(kernel_tools: dict[str, Any] | None = None) -> ToolRegistry:
    """Build ToolRegistry with kernel tools (use_skill, ask_user, etc.)."""
    registry = ToolRegistry()
    default_kernel = {
        "use_skill": None,
        "ask_user": None,
        "task_complete": None,
    }
    for name in (kernel_tools or default_kernel):
        registry.register(name, {"name": name, "description": f"Built-in: {name}"}, None)
    return registry


async def create_harness(
    agent_config_path: str,
    harness_config_path: str | None = None,
    plugin_dir: str | None = None,
    event_bus=None,
    data_dir: str = "./data",
) -> AgentHarness:
    """Create a fully configured AgentHarness from agent.yaml and harness.yaml.

    Args:
        agent_config_path: Path to agent.yaml
        harness_config_path: Path to harness.yaml (optional, defaults next to agent.yaml)
        plugin_dir: Path to plugins directory (optional, defaults to arf/plugins/)
        event_bus: Event bus instance (optional, creates InMemoryEventBus)
        data_dir: Root data directory for traces, state, etc.

    Returns:
        Configured AgentHarness ready for run()
    """
    # 1. Load agent config
    agent_cfg = AgentConfig.from_yaml(agent_config_path)

    # 2. Build call_model
    call_model = _build_call_model(agent_cfg.model_defs, agent_cfg.models)

    # 3. Create PrimitiveAgent
    # Extract model config for agent state persistence
    model_config = {}
    if agent_cfg.model_defs:
        md = agent_cfg.model_defs[0]
        model_config = {
            "api_base": md.get("api_base", ""),
            "api_key_env": md.get("api_key_env", ""),
            "model_name": md.get("model", ""),
            "context_window": md.get("context_window", 131072),
        }
    elif agent_cfg.models:
        m = agent_cfg.models[0]
        model_config = {
            "api_base": m.api_base,
            "api_key_env": m.api_key_env,
            "model_name": m.model,
            "context_window": m.context_window,
        }

    agent = PrimitiveAgent(
        agent_id=agent_cfg.name,
        model_config=model_config,
        call_model=call_model,
    )

    # 4. Load harness config
    if harness_config_path is None:
        # Default to harness.yaml next to agent.yaml
        agent_dir = Path(agent_config_path).parent
        harness_config_path = str(agent_dir / "harness.yaml")

    try:
        harness_cfg = HarnessConfig.from_yaml(harness_config_path)
    except FileNotFoundError:
        logger.info("No harness.yaml found at %s, using defaults", harness_config_path)
        harness_cfg = HarnessConfig()

    # 5. Resolve plugin directory
    if plugin_dir is None:
        import arf as _arf_pkg
        plugin_dir = str(Path(_arf_pkg.__file__).parent / "plugins")

    # 6. Discover and instantiate plugins
    plugin_configs = discover_plugins(plugin_dir, harness_cfg.plugins)
    plugins = instantiate_plugins(plugin_configs)

    # 7. Build tool registry and executor
    registry = _build_tool_registry()
    tool_executor = ToolExecutor(registry, timeout=harness_cfg.tool_timeout) if harness_cfg.plugins else None

    # 8. Create event bus if not provided
    if event_bus is None:
        from arf.event_bus import InMemoryEventBus
        event_bus = InMemoryEventBus()

    # 9. Create AgentHarness
    harness = AgentHarness(
        agent=agent,
        plugins=plugins,
        tool_executor=tool_executor,
        event_bus=event_bus,
        max_turns=harness_cfg.max_turns,
        data_dir=data_dir,
    )

    return harness
