"""ValidateMessagesPlugin — check message contract before call_model dispatch."""
from arf.core.plugin_context import PluginContext
from arf.engine.control_plane import MessageContractError


class ValidateMessagesPlugin:
    def __init__(self, config: dict | None = None):
        pass

    @property
    def name(self) -> str:
        return "validate_messages"

    @property
    def hooks(self) -> dict[str, str]:
        return {"pre_dispatch": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if ctx.current_step != "call_model":
            return
        msgs = ctx.messages
        if not msgs:
            return
        if msgs[0].get("role") != "user":
            raise MessageContractError("Messages must start with user role")
        for i, m in enumerate(msgs):
            if not isinstance(m, dict):
                raise MessageContractError(f"Message {i} is not a dict")
            role = m.get("role", "")
            if role not in ("user", "assistant", "tool"):
                raise MessageContractError(f"Message {i} has invalid role: {role}")
