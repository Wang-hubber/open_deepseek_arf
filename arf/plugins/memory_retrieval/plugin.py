"""MemoryRetrievalPlugin — retrieve long-term memory at round_start."""
from arf.core.plugin_context import PluginContext


class MemoryRetrievalPlugin:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._max_tokens = cfg.get("max_tokens", 2000)
        self._top_k = cfg.get("top_k", 5)
        self._retriever = None

    def set_retriever(self, retriever) -> None:
        self._retriever = retriever

    @property
    def name(self) -> str:
        return "memory_retrieval"

    @property
    def hooks(self) -> dict[str, str]:
        return {"round_start": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if not self._retriever:
            return
        query = ctx.messages[-1].get("content", "") if ctx.messages else ""
        entries = await self._retriever.retrieve(query, ctx.messages, top_k=self._top_k)
        summary = "\n".join(e.get("content", "") for e in entries)
        ctx.state["context_summary"] = summary
