"""MemoryPlugin — LLM-driven memory extraction + injection.

Self-contained: owns MemoryIndex, SecretsStore, and a dedicated extraction model.
Tools are loaded by PluginProvider as memory__* namespace; the plugin wires their
module-level globals (_index, _store, _call_model) after lazy init.
"""
from __future__ import annotations
import asyncio
import json as _json_mod
import logging
import os
from pathlib import Path

from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.memory.config import MemoryConfig
from arf.memory.index import MemoryIndex
from arf.memory.secrets_store import SecretsStore

logger = logging.getLogger("arf.plugins.memory")


class MemoryPlugin(Plugin):
    """Extracts user/task memory via LLM, writes to memory.md files.

    Injects existing memory as system messages on session_start.
    Extracts user facts every N rounds, extracts task experience on task_completed.
    """

    def __init__(self, name="memory", events=None, config=None):
        events = events or [
            {"hook_name": "session_start", "event_name": "session_start", "mode": "blocking"},
            {"hook_name": "after_round", "event_name": "round_end", "mode": "side"},
            {"hook_name": "after_round", "event_name": "task_completed", "mode": "side"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._interval: int = self.config.get("interval", 5)
        self._max_memory_size: int = self.config.get("max_memory_size", 300)
        self._round_count: dict[str, int] = {}
        self._inited: bool = False

        # Lazy-initialized components
        self._index: MemoryIndex | None = None
        self._secrets: SecretsStore | None = None
        self._call_model = None
        self._data_dir: str = ""

    # ── lifecycle ────────────────────────────────────────

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        self._ensure_init(ctx)

        if event_name == "session_start":
            await self._on_session_start(ctx)
        elif event_name == "round_end":
            await self._on_round_end(ctx)
        elif event_name == "task_completed":
            await self._on_task_completed(ctx)

    def _ensure_init(self, ctx: PluginContext) -> None:
        if self._inited:
            return
        self._inited = True

        data_dir = ctx.data_dir
        self._data_dir = data_dir

        # SecretsStore must come first — MemoryIndex validates it when secrets enabled
        mem_cfg = MemoryConfig()
        master_key = os.environ.get(mem_cfg.secrets.master_key_env, "")
        self._secrets = SecretsStore(data_dir=data_dir, master_key=master_key)
        self._index = MemoryIndex(data_dir, mem_cfg, secrets_store=self._secrets)

        # Wire tool globals
        self._wire_tool("write_user_memory", _index=self._index)
        self._wire_tool("write_project_memory", _index=self._index)
        self._wire_tool("search_task_memory", _index=self._index)
        self._wire_tool("list_secrets", _store=self._secrets)
        self._wire_tool("read_secret", _store=self._secrets)
        self._wire_tool("write_secret", _store=self._secrets)

        # Extraction model
        model_cfg = self.config.get("model")
        if model_cfg:
            self._call_model = self._build_call_model(model_cfg)
            # Also wire for search_task_memory tool
            self._wire_tool("search_task_memory", _call_model=self._call_model)

        logger.info("MemoryPlugin initialized (data_dir=%s)", data_dir)

    @staticmethod
    def _wire_tool(name: str, **kwargs) -> None:
        """Inject globals into a plugin tool module."""
        import importlib as _il
        try:
            mod = _il.import_module(f"arf.plugins.memory.tools.{name}.function")
            for attr, val in kwargs.items():
                setattr(mod, attr, val)
        except ImportError:
            logger.warning("Memory tool '%s' not found for wiring", name)

    def _build_call_model(self, model_cfg: dict):
        """Build an async call_model function from plugin model config."""
        from arf.core.model_adapter import ModelAdapter
        api_key = os.environ.get(model_cfg.get("api_key_env", ""), "placeholder")
        adapter = ModelAdapter({
            "base_url": model_cfg.get("api_base", "https://api.deepseek.com/v1"),
            "api_key": api_key,
            "model_name": model_cfg.get("model", "deepseek-chat"),
            "context_window": model_cfg.get("context_window", 131072),
        })

        async def _call(messages: list[dict], model_name: str = "") -> dict:
            msg = await adapter.chat_complete(messages, tools=None)
            content = msg.content if hasattr(msg, "content") else str(msg)
            return {"content": content}

        return _call

    # ── session_start — inject memory ─────────────────────

    async def _on_session_start(self, ctx: PluginContext) -> None:
        """Inject existing memory as system messages."""
        if self._index is None:
            return
        msgs = self._index.build_injected_messages()
        for msg in msgs:
            ctx.agent.input(role="system", content=msg["content"])

    # ── round_end — rolling user memory extraction ────────

    async def _on_round_end(self, ctx: PluginContext) -> None:
        sid = ctx.session_id
        self._round_count.setdefault(sid, 0)
        self._round_count[sid] += 1

        if self._round_count[sid] % self._interval != 0:
            return

        # Trim old messages
        messages = ctx.agent.state.messages
        if len(messages) > self._max_memory_size:
            ctx.agent.state.messages = messages[-self._max_memory_size:]

        if self._call_model is not None and self._index is not None:
            asyncio.create_task(self._rolling_update(ctx))

    async def _rolling_update(self, ctx: PluginContext) -> None:
        """LLM extracts user-specific facts from recent conversation."""
        messages = ctx.agent.state.messages
        if not messages or self._index is None or self._call_model is None:
            return

        existing = self._index.load_user()
        recent = [{"role": m.role, "content": m.content} for m in messages[-20:]]

        instruction = _USER_EXTRACTION_PROMPT.format(
            existing=existing or "(no existing user memory)",
        )
        call_messages = recent + [{"role": "user", "content": instruction}]

        try:
            resp = await self._call_model(call_messages, model_name="")
            content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        except Exception:
            logger.warning("User memory extraction failed", exc_info=True)
            return

        output = content.strip()
        if not output or output == "NO_NEW_MEMORY":
            return

        if self._index._group_dir:
            self._index.save_group_user(output)
        else:
            self._index.save_user(output)
        logger.info("User memory updated (%d chars)", len(output))

    # ── task_completed — task experience extraction ───────

    async def _on_task_completed(self, ctx: PluginContext) -> None:
        if self._call_model is None or self._index is None:
            return

        messages = ctx.agent.state.messages
        if not messages:
            return

        task_result = ctx.hook_data.get("task_result", "")
        notes = ctx.hook_data.get("notes", "")
        confidence = ctx.hook_data.get("confidence", 1.0)

        entry = await self._extract_task_experience(messages, task_result, notes, confidence)
        if entry is None:
            return

        entry["agent_name"] = ctx.agent.state.agent_id
        asyncio.create_task(self._merge_and_save(entry))

    async def _extract_task_experience(
        self, messages, task_result: str, notes: str, confidence: float,
    ) -> dict | None:
        instruction = _TASK_EXTRACTION_PROMPT.format(
            task_result=task_result,
            notes=notes,
            confidence=confidence,
        )
        raw_messages = [{"role": m.role, "content": m.content} for m in messages]
        call_messages = raw_messages + [{"role": "user", "content": instruction}]

        try:
            resp = await self._call_model(call_messages, model_name="")
            content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        except Exception:
            logger.warning("Task memory extraction failed", exc_info=True)
            return None

        try:
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            entry = _json_mod.loads(content)
        except Exception:
            logger.warning("Failed to parse extraction result: %s", content[:200])
            return None

        if not entry.get("should_write", True):
            return None
        return entry

    async def _merge_and_save(self, entry: dict) -> None:
        existing = self._index.load_tasks() if self._index else ""

        prompt = _TASK_MERGE_PROMPT.format(
            existing=existing or "(no existing task memory)",
            new_entry=_json_mod.dumps(entry, ensure_ascii=False, indent=2),
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
        if not merged or self._index is None:
            return

        if self._index._group_dir:
            self._index.save_group_tasks(merged)
        self._index.save_tasks(merged)
        logger.info("Task memory updated (%d chars)", len(merged))


# ── Prompt templates ──────────────────────────────────────────────────

_USER_EXTRACTION_PROMPT = """Based on the conversation above, extract user-specific facts. Output raw Markdown — only categories with facts.

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

If nothing worth extracting: output "NO_NEW_MEMORY" and stop."""


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


Plugin = MemoryPlugin
