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
from arf.mcp.client_manager import McpClientManager
from arf.skills.use_skill_tool import execute as use_skill_execute
from arf.skills.ask_user_tool import execute as ask_user_execute
from arf.skills.task_complete_tool import execute as task_complete_execute
from arf.skills.skill_index import SkillIndex

logger = logging.getLogger("arf.harness.factory")


def _build_call_model(model_defs: list[dict], models: list) -> Any:
    """Build call_model and stream_model functions from model definitions."""
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
                "message_format": md.get("message_format", "openai"),
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
                "message_format": getattr(m, "message_format", "openai"),
                **getattr(m, "kwargs", {}),
            }
            adapters.append(ModelAdapter(cfg))

    if not adapters:
        raise ValueError("No model configuration found. Add model_defs or models to agent.yaml.")

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

    # 2. Build call_model and stream_model
    call_model, stream_model = _build_call_model(agent_cfg.model_defs, agent_cfg.models)

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
        stream_model=stream_model,
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

    # 7. Build McpClientManager — unified tool gateway
    agent_dir = Path(agent_config_path).parent
    tools_dir = agent_dir / agent_cfg.tools_dir
    skills_dir = agent_dir / agent_cfg.skills_dir

    tool_manager = McpClientManager(
        tools_dir=tools_dir,
        skills_dir=skills_dir,
        models_dir=agent_dir / "models",
        plugins_dir=Path(plugin_dir) if plugin_dir else Path(agent_dir / "plugins"),
        mcp_servers=agent_cfg.mcp_servers,
        plugin_names=agent_cfg.plugins if agent_cfg.plugins else [],
        plugin_configs=agent_cfg.plugins_config,
    )
    tool_manager.register_kernel_tool("use_skill", use_skill_execute)
    tool_manager.register_kernel_tool("ask_user", ask_user_execute)
    tool_manager.register_kernel_tool("task_complete", task_complete_execute)

    await tool_manager.start()

    # Initialize skill index for use_skill
    skill_index = SkillIndex(skills_dir)
    skill_index.scan()
    import arf.skills.use_skill_tool as _use_skill_mod
    _use_skill_mod._index = skill_index

    # 8. Create event bus if not provided
    if event_bus is None:
        from arf.event_bus import InMemoryEventBus
        event_bus = InMemoryEventBus()

    # 9. Create AgentHarness
    harness = AgentHarness(
        agent=agent,
        plugins=plugins,
        tool_manager=tool_manager,
        agent_config=agent_cfg,
        event_bus=event_bus,
        max_turns=harness_cfg.max_turns,
        data_dir=data_dir,
    )

    return harness
