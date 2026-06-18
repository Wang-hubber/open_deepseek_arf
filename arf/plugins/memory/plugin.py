"""MemoryPlugin — user memory extraction on round_end via LLM."""
import logging

from arf.core.plugin_context import PluginContext

logger = logging.getLogger("arf.plugins.memory")


class MemoryPlugin:
    """Extracts user-specific memory from conversation messages every N rounds.

    On each triggered round_end, builds an extraction prompt from recent
    messages, calls the LLM, and writes the result to user.md via MemoryIndex.
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._interval: int = cfg.get("interval", 5)
        self._extract_on_session_end: bool = cfg.get("extract_on_session_end", False)
        self._mem_index = None  # set by BaseAgent after construction
        self._call_model = None  # set by BaseAgent after construction

    def set_memory_index(self, mem_index) -> None:
        self._mem_index = mem_index

    def set_call_model(self, call_model) -> None:
        self._call_model = call_model

    @property
    def name(self) -> str:
        return "memory"

    @property
    def hooks(self) -> dict[str, str]:
        h: dict[str, str] = {"round_end": "side"}
        if self._extract_on_session_end:
            h["session_end"] = "side"
        return h

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name not in ("round_end", "session_end"):
            return

        # session_end always extracts; round_end is gated by interval
        if hook_name == "round_end":
            current_round = ctx.interaction_round
            if current_round <= 0 or current_round % self._interval != 0:
                return

        messages = ctx.state.get("messages", [])
        if not messages:
            return

        if self._mem_index is not None:
            import asyncio
            asyncio.create_task(self._rolling_update(ctx, messages))

    async def _rolling_update(self, ctx, messages: list[dict]) -> None:
        """Extract user-specific facts from this round into user.md."""
        if self._call_model is None or self._mem_index is None:
            return

        existing = self._mem_index.load_user()
        recent = messages[-20:]
        msgs_text = "\n".join(
            f"[{m.get('role', '?')}] {str(m.get('content', ''))[:800]}"
            for m in recent
        )

        prompt = _USER_EXTRACTION_PROMPT.format(
            existing=existing or "(no existing user memory)",
            messages=msgs_text,
        )

        try:
            resp = await self._call_model(
                [{"role": "user", "content": prompt}],
                model_name="",
            )
            content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        except Exception:
            logger.warning("User memory extraction failed", exc_info=True)
            return

        output = content.strip()
        if not output or output == "NO_NEW_MEMORY":
            return

        # Write to group when shared memory dir is configured, else individual
        if self._mem_index._group_dir:
            self._mem_index.save_group_user(output)
        else:
            self._mem_index.save_user(output)
        logger.info("User memory updated (%d chars)", len(output))


_USER_EXTRACTION_PROMPT = """Extract user-specific facts from the conversation below. Output raw Markdown — only categories with facts.

## Existing User Memory

{existing}

## Extraction Rules

Extract ONLY facts about the USER (not the project, not the AI, not task progress):

**Extract:**
- Who the user is (role, skills, background, responsibilities)
- How they like to work (language, style, tools, workflows, communication preferences)
- What they decided and WHY (architecture choices, naming, tech stack, rejected alternatives)
- What they know / believe that persists across sessions (domain knowledge, constraints, contacts)

**Skip:**
- Task progress or to-do items
- Code or tool outputs
- Debug traces or error stacks
- Casual chat or one-off questions
- Project structure (that's project.md's job)

## Output Format

```
## <Category>

- Fact (one sentence, include WHY when known)
```

Use these categories (pick applicable ones, create new ones if needed):
- ## User Identity
- ## Preferences
- ## Decisions
- ## Knowledge

Keep each bullet to one sentence. Be specific. If a new fact updates or contradicts old memory, reflect the change directly. Preserve existing memories that are still accurate — only modify what changed.

If nothing worth extracting: output "NO_NEW_MEMORY" and stop.

## Recent Conversation

{messages}"""
