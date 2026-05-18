"""Session manager -- encapsulates all server-side state.

Injectable instance owned by ARFServer for the single-user workspace.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

import yaml

from ..resources.manager import ResourceRegistry
from ..resources.model_adapter import ModelAdapter
from ..agent import ARFAgent
from .sessions import DEFAULT_TITLE
from .fast_model import load_fast_model, is_fast_model_configured
from .hook_runner import HookRunner, generate_default_config
from .trace_collector import TraceCollector

class SessionManager:
    """Encapsulates workspace config, resource registry, agent cache,
    and session conversation history."""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir.resolve()
        self._system_dir: Path | None = None
        self._registry: ResourceRegistry | None = None
        self._agent: ARFAgent | None = None
        self._agent_mtime: float = 0.0

        # Session state
        self.session_history: list[dict] = []
        self.session_start_time: datetime = datetime.now(timezone.utc)
        self.session_title: str = DEFAULT_TITLE
        self.needs_title: bool = True  # cleared after first title generation

        # Graph trace data (populated by LangGraph engine when enabled)
        self.last_traces: list[dict] = []
        self.last_usage: dict | None = None

        # Hook runner (lazy-init)
        self._hook_runner: HookRunner | None = None
        self._session_end_fired = False
        self._trace_collector = TraceCollector()

    @property
    def current_session_id(self) -> str:
        return self.session_start_time.strftime("%Y%m%d_%H%M%S")

    # ---- system dir ---------------------------------------------------

    @property
    def system_dir(self) -> Path:
        if self._system_dir is None:
            import arf.resources.system
            self._system_dir = Path(arf.resources.system.__file__).parent
        return self._system_dir

    # ---- registry -----------------------------------------------------

    def get_registry(self) -> ResourceRegistry:
        if self._registry is None:
            self._registry = ResourceRegistry()
            try:
                self._registry.load(
                    str(self.system_dir),
                    str(self.workspace_dir),
                )
                collector = self.get_trace_collector()
                collector.emit({
                    "event_type": "lifecycle.init",
                    "status": "ok",
                    "metadata": {
                        "stage": "registry_loaded",
                        "counts": {
                            "models": self._registry.count("models"),
                            "tools": self._registry.count("tools"),
                            "skills": self._registry.count("skills"),
                        },
                        "source": "system+user",
                    },
                })
            except Exception as e:
                collector = self.get_trace_collector()
                collector.emit({
                    "event_type": "lifecycle.init",
                    "status": "error",
                    "error_msg": str(e),
                    "metadata": {"stage": "registry_loaded"},
                })
                raise
        return self._registry

    # ---- agent --------------------------------------------------------

    def get_agent(self):
        """Return a Dispatcher wrapping UserAgent + SysAgent, auto-invalidating
        if the underlying model config file changed."""
        # Use first configured model's mtime as cache key
        resolved = self.resolve_model_config(None)
        if not resolved:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=400,
                detail="No model configured. Please configure at least one model.",
            )

        model_name, _model_config = resolved
        config_path = self.workspace_dir / "models" / model_name / "config.yaml"
        current_mtime = config_path.stat().st_mtime if config_path.exists() else 0.0

        if self._agent is not None and current_mtime != self._agent_mtime:
            collector = self.get_trace_collector()
            collector.emit({
                "event_type": "lifecycle.config",
                "status": "ok",
                "metadata": {
                    "action": "agent_rebuilt",
                    "reason": "model_config_changed",
                },
            })
            self.reset_resource_state()
        if self._agent is None:
            from ..agent import UserAgent, SysAgent, generate_default_configs
            from ..engine import Dispatcher

            # Ensure agent configs exist in workspace (copy defaults if missing)
            generate_default_configs(str(self.workspace_dir))

            registry = self.get_registry()
            user_agent = UserAgent.from_config(
                registry, str(self.workspace_dir),
                hook_runner=self.get_hook_runner(),
            )
            sys_agent = SysAgent.from_config(
                registry, str(self.workspace_dir),
                hook_runner=self.get_hook_runner(),
            )

            self._agent = Dispatcher(user_agent, sys_agent)
            self._agent._trace_collector = self.get_trace_collector()
            self._agent_mtime = current_mtime

            collector = self.get_trace_collector()
            collector.emit({
                "event_type": "lifecycle.init",
                "status": "ok",
                "metadata": {
                    "stage": "agent_built",
                    "agent_mode": "dispatcher",
                    "user_model": user_agent.default_model,
                    "sys_model": sys_agent.default_model,
                    "user_max_turns": user_agent.max_turns,
                    "sys_max_turns": sys_agent.max_turns,
                },
            })
        return self._agent

    def reset_resource_state(self):
        """Clear cached registry and agent so they reload on next access."""
        self._registry = None
        self._agent = None
        self._agent_mtime = 0.0

    # ---- hook runner --------------------------------------------------

    def get_hook_runner(self) -> HookRunner:
        if self._hook_runner is None:
            generate_default_config(self.workspace_dir)
            self._hook_runner = HookRunner(self.workspace_dir)
        return self._hook_runner

    def get_trace_collector(self) -> "TraceCollector":
        return self._trace_collector

    # ---- model resolution ---------------------------------------------

    def read_agent_yaml(self) -> dict:
        agent_yaml = self.workspace_dir / "arf_agent.yaml"
        if not agent_yaml.exists():
            return {}
        try:
            return yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    # Priority order for model type resolution (session default -> fallback)
    _MODEL_TYPE_PRIORITY = ("quick_thinking", "deep_thinking", "quick_no_thinking")

    def resolve_model_config(self, preferred_name: str | None = None
                             ) -> tuple[str, dict] | None:
        """Resolve a configured model. Tries preferred_name first, then
        model types in priority order."""
        registry = self.get_registry()
        models = registry._items.get("models", {})

        def _is_configured(cfg: dict) -> bool:
            return bool(cfg.get("base_url") and cfg.get("api_key") and cfg.get("model_name"))

        # 1. Try preferred name
        if preferred_name and preferred_name in models:
            cfg = models[preferred_name].get("config", {})
            if _is_configured(cfg):
                return preferred_name, cfg

        # 2. Try type priority order
        for mt in self._MODEL_TYPE_PRIORITY:
            for name, m in models.items():
                if m.get("model_type") == mt:
                    cfg = m.get("config", {})
                    if _is_configured(cfg):
                        return name, cfg

        # 3. Try any configured model
        for name, m in models.items():
            cfg = m.get("config", {})
            if _is_configured(cfg):
                return name, cfg

        return None

    # ---- session history ----------------------------------------------

    def fire_session_end(self):
        """Fire SessionEnd hooks. Idempotent — only fires once per session."""
        if self._session_end_fired:
            return
        if not self.session_history or len(self.session_history) < 2:
            return
        self._session_end_fired = True

        sid = self.current_session_id
        runner = self.get_hook_runner()

        # Emit session_end trace
        duration = (datetime.now(timezone.utc) - self.session_start_time).total_seconds()
        collector = self.get_trace_collector()
        collector.emit({
            "event_type": "lifecycle.session_end",
            "status": "ok",
            "metadata": {
                "session_id": sid,
                "message_count": len(self.session_history),
                "duration_seconds": round(duration, 1),
                "trigger": "stream_done",
            },
        })

        try:
            runner.run("SessionEnd", {
                "session_id": sid,
                "session_title": self.session_title,
            }, stdin_data={
                "conversation": list(self.session_history),
                "session_start": self.session_start_time.isoformat(),
                "message_count": len(self.session_history),
            })
        except Exception:
            logger.exception("SessionEnd hooks failed on normal completion")

    def reset_session_history(self, title: str = DEFAULT_TITLE) -> None:
        # Flush traces before clearing
        events = self._trace_collector.flush()
        if events and self.session_history and len(self.session_history) >= 2:
            sid = self.current_session_id
            for e in events:
                e["session_id"] = sid
            try:
                from .database import insert_trace_events
                insert_trace_events(events, str(self.workspace_dir))
            except Exception:
                logger.exception("Failed to flush trace events")

        self._session_end_fired = False
        self.session_history = []
        self.session_start_time = datetime.now(timezone.utc)
        self.session_title = title
        self.needs_title = True
        self.last_traces = []
        self.last_usage = None

    def track_session(self, user_msg: str, assistant_response: str = "", reasoning: str = ""):
        self.session_history.append({"role": "user", "content": user_msg})
        if assistant_response or reasoning:
            entry: dict = {"role": "assistant", "content": assistant_response}
            if reasoning:
                entry["reasoning_content"] = reasoning
            self.session_history.append(entry)

    # ---- fast model ---------------------------------------------------

    def load_fast_model(self) -> ModelAdapter | None:
        return load_fast_model(self.get_registry())

    def is_fast_model_configured(self) -> bool:
        return is_fast_model_configured(self.get_registry())

    # ---- language preference -------------------------------------------

    def _load_language(self) -> str:
        """Load user language preference."""
        return "zh"
