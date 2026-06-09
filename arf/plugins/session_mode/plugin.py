"""SessionModePlugin — resolve AUTO/PLAN/ASK permission mode."""
from arf.session import SessionMode
from arf.core.plugin_context import PluginContext


class SessionModePlugin:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        mode_str = cfg.get("default_mode", "ask")
        self._session_mode = SessionMode(mode_str)

    @property
    def name(self) -> str:
        return "session_mode"

    @property
    def hooks(self) -> dict[str, str]:
        return {"pre_action": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if ctx.current_step != "execute_tools":
            return
        ctx.hook_data["effective_mode"] = self._session_mode
