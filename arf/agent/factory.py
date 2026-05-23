"""create_agent — the single public entry point."""
from pathlib import Path
from arf.agent.config import AgentConfig as AgentConfigModel
from arf.agent.base import BaseAgent


def create_agent(*, config: AgentConfigModel | None = None, agent_dir: str | None = None) -> BaseAgent:
    if agent_dir:
        cfg = AgentConfigModel.from_yaml(agent_dir)
        return BaseAgent(cfg)
    if config is not None:
        return BaseAgent(config)
    raise ValueError("Either 'config' or 'agent_dir' must be provided")
