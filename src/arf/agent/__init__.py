"""ARF Agent -- dual-agent architecture (UserAgent + SysAgent + Dispatcher)."""

from .base import BaseAgent, generate_default_configs  # noqa: F401
from .user_agent import UserAgent  # noqa: F401
from .sys_agent import SysAgent  # noqa: F401
