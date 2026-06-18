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
        h: dict[str, str] = {"round_end": "side", "task_completed": "side"}
        if self._extract_on_session_end:
            h["session_end"] = "side"
        return h

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name == "task_completed":
            await self._on_task_completed(ctx)
            return

        if hook_name not in ("round_end", "session_end"):
            return

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

    async def _on_task_completed(self, ctx: PluginContext) -> None:
        """Extract task experience from completed task conversation."""
        if self._call_model is None or self._mem_index is None:
            return

        messages = ctx.state.get("messages", [])
        if not messages:
            return

        task_result = ctx.hook_data.get("task_result", "")
        notes = ctx.hook_data.get("notes", "")
        confidence = ctx.hook_data.get("confidence", 1.0)

        # Step 1: Extract from messages (in-session, cache-friendly)
        entry = await self._extract_task_experience(
            ctx, messages, task_result, notes, confidence)
        if entry is None:
            return  # should_write was False

        # Step 2: Merge into existing tasks.md (independent async call)
        import asyncio
        asyncio.create_task(self._merge_and_save(ctx, entry))

    async def _extract_task_experience(
        self, ctx: PluginContext, messages: list[dict],
        task_result: str, notes: str, confidence: float,
    ) -> dict | None:
        """LLM extracts approach + lessons from full conversation.

        Reuses ctx.state["messages"] directly as the prefix — appending
        only the extraction instruction as a new user message. This
        guarantees byte-identical prefix with the main session for
        maximum prompt cache hit rate.
        """
        instruction = _TASK_EXTRACTION_PROMPT.format(
            task_result=task_result,
            notes=notes,
            confidence=confidence,
        )

        # Build call messages: full conversation + extraction instruction
        call_messages = list(messages) + [
            {"role": "user", "content": instruction},
        ]

        try:
            resp = await self._call_model(
                call_messages,
                model_name="",
            )
            content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        except Exception:
            logger.warning("Task memory extraction failed", exc_info=True)
            return None

        try:
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            import json as _json
            entry = _json.loads(content)
        except Exception:
            logger.warning("Failed to parse extraction result: %s", content[:200])
            return None

        if not entry.get("should_write", True):
            return None
        return entry

    async def _merge_and_save(self, ctx: PluginContext, entry: dict) -> None:
        """Merge new entry into tasks.md and save to personal + group."""
        existing = self._mem_index.load_tasks()

        prompt = _TASK_MERGE_PROMPT.format(
            existing=existing or "(no existing task memory)",
            new_entry=_json_dumps(entry),
        )

        try:
            resp = await self._call_model(
                [{"role": "user", "content": prompt}],
                model_name="",
            )
            merged = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        except Exception:
            logger.warning("Task memory merge failed", exc_info=True)
            return

        merged = merged.strip()
        if not merged:
            return

        if self._mem_index._group_dir:
            self._mem_index.save_group_tasks(merged)
        self._mem_index.save_tasks(merged)
        logger.info("Task memory updated (%d chars)", len(merged))


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


import json as _json_mod


def _json_dumps(obj: dict) -> str:
    return _json_mod.dumps(obj, ensure_ascii=False, indent=2)


_TASK_EXTRACTION_PROMPT = """Based on the conversation above, extract reusable task experience. Focus on WHAT was done and WHAT PITFALLS to avoid.

**Task result:** {task_result}
**Notes:** {notes}
**Confidence:** {confidence}

## Extraction Rules

**Extract ONLY if there is non-trivial experience:**

- **category**: one of — refactoring, bugfix, feature, config, investigation, deployment, testing, docs
- **description**: one sentence (max 40 chars), what the task was about
- **approach**: 2-5 bullets of what was done, key decisions, order of operations
- **lessons**: 1-5 bullets of pitfalls, surprises, wrong turns, what to avoid next time. Be specific — name functions, files, error types.
- **should_write**: false if the task is trivial (simple Q&A, one-line fix with no learning). Default true.

**Skip:**
- Sensitive data (passwords, tokens, PII)
- Task-internal progress updates
- Model self-praise or generic conclusions ("task was completed successfully")

## Output Format

Output ONLY a valid JSON object, no markdown fences:
{{
  "category": "bugfix",
  "description": "fix login timeout",
  "approach": ["located redis connection timeout", "increased pool timeout to 30s"],
  "lessons": ["never assume redis connection pool auto-recovers after fork"],
  "should_write": true
}}"""


_TASK_MERGE_PROMPT = """Merge new task experience into the existing task memory notebook. Follow these rules:

## Merging Rules

1. **Same category**: Append the new entry under the same category section.
2. **Similar lessons**: If a lesson in the new entry is semantically similar to an existing lesson, merge them — keep the clearer version, increment the count suffix (×N) on the merged line.
3. **New lessons**: Add as new bullet under **教训：**.
4. **Size limit**: If adding this entry would make the output exceed ~50KB (approx 12000 words), trim the OLDEST entries first (remove entire <!-- TASK --> blocks from the top).
5. **Preserve structure**: Every entry must keep the `<!-- TASK category | agent: name -->` header, `### description`, `**方案：**`, `**教训：**` sections, and `<!-- /TASK -->` footer.
6. **Agent attribution**: Use the agent name from the new entry's header for the new/merged entry.

## Existing Task Memory

{existing}

## New Entry to Merge

{new_entry}

## Output

Output the COMPLETE merged tasks.md content, preserving all existing entries that were not affected. No markdown fences, no commentary — just the raw tasks.md content."""
