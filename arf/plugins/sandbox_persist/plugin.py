"""SandboxPersistPlugin — persist workspace after tool execution."""
from arf.core.plugin_context import PluginContext


class SandboxPersistPlugin:
    def __init__(self, config: dict | None = None):
        pass

    @property
    def name(self) -> str:
        return "sandbox_persist"

    @property
    def hooks(self) -> dict[str, str]:
        return {"post_dispatch": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if ctx.current_step != "execute_tools":
            return
        # Persist workspace changes after tool execution
        # Sandbox diff/persist logic from SandboxManager
