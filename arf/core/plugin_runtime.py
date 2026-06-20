"""PluginRuntime — DEPRECATED: plugin config now lives in plugin.yaml."""
import warnings
warnings.warn("PluginRuntime is deprecated. Use plugin.yaml + PluginContext.", DeprecationWarning, stacklevel=2)

import os
import sys
from dataclasses import dataclass, field


@dataclass
class PluginRuntime:
    """Framework runtime context for plugins. Read-only for plugin code."""

    python_executable: str = field(default_factory=lambda: sys.executable)
    env_vars: dict[str, str] = field(default_factory=lambda: dict(os.environ))

    memory_dir: str = "./data/memory"
    workspace_dir: str = "."
    data_dir: str = "./data"
    state_dir: str = "./data/state"
    trace_dir: str = "./data/traces"
    files_dir: str = "./data/files"

    session_id: str = "default"
    interaction_round: int = 0

    system_model: str = "quick"
    model_configs: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "python_executable": self.python_executable,
            "env_vars": self.env_vars,
            "memory_dir": self.memory_dir,
            "workspace_dir": self.workspace_dir,
            "data_dir": self.data_dir,
            "state_dir": self.state_dir,
            "trace_dir": self.trace_dir,
            "files_dir": self.files_dir,
            "session_id": self.session_id,
            "interaction_round": self.interaction_round,
            "system_model": self.system_model,
            "model_configs": self.model_configs,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PluginRuntime":
        return cls(
            python_executable=d.get("python_executable", sys.executable),
            env_vars=d.get("env_vars", {}),
            memory_dir=d.get("memory_dir", "./data/memory"),
            workspace_dir=d.get("workspace_dir", "."),
            data_dir=d.get("data_dir", "./data"),
            state_dir=d.get("state_dir", "./data/state"),
            trace_dir=d.get("trace_dir", "./data/traces"),
            files_dir=d.get("files_dir", "./data/files"),
            session_id=d.get("session_id", "default"),
            interaction_round=d.get("interaction_round", 0),
            system_model=d.get("system_model", "quick"),
            model_configs=d.get("model_configs", {}),
        )
