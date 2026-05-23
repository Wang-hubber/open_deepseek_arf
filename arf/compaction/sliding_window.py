"""SlidingWindowCompactor — summarise old turns, keep recent ones."""
from arf.core.state import AgentState


class SlidingWindowCompactor:
    def __init__(self, threshold: float = 0.75, summarizer: callable | None = None) -> None:
        self._threshold = threshold
        self._summarize = summarizer

    def should_compact(self, state: AgentState, threshold: float | None = None) -> bool:
        t = threshold or self._threshold
        chars = sum(len(m.get("content", "")) for m in state.get("messages", []))
        return chars > t * 1_000_000 * 3

    async def compact(self, state: AgentState) -> AgentState:
        msgs = state.get("messages", [])
        if len(msgs) <= 4:
            return state
        old_msgs = msgs[:-4]
        recent = msgs[-4:]
        summary = state.get("context_summary", "")
        if self._summarize and old_msgs:
            new_summary = await self._summarize(old_msgs)
            prefix = "[Earlier]" if not summary else ""
            summary = f"{summary}\n{prefix}: {new_summary}" if summary else f"[Earlier]: {new_summary}"
        return {**state, "messages": recent, "context_summary": summary}
