"""SysAgent — system engineer persona, configured via arf_sys_agent.yaml."""

from .base import BaseAgent


class SysAgent(BaseAgent):
    _config_filename = "arf_sys_agent.yaml"
