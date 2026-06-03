"""MemoryExtractionPlugin — extract and write long-term memory at session_end."""
from arf.core.plugin_context import PluginContext


class MemoryExtractionPlugin:
    def __init__(self, config: dict | None = None):
        self._writer = None

    def set_writer(self, writer) -> None:
        self._writer = writer

    @property
    def name(self) -> str:
        return "memory_extraction"

    @property
    def hooks(self) -> dict[str, str]:
        return {"session_end": "side"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if not self._writer:
            return
        await self._writer.write(ctx.messages, ctx.session_id)
