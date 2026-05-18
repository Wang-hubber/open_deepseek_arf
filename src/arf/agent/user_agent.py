"""UserAgent — personal assistant persona, configured via arf_user_agent.yaml."""

from .base import BaseAgent


class UserAgent(BaseAgent):
    _config_filename = "arf_user_agent.yaml"
