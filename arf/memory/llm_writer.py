"""LLMMemoryWriter — use a cheap LLM to extract structured memories from turns."""
from __future__ import annotations

import json
import logging
import time
import uuid

from arf.core.protocols import MemoryEntry, MemoryStore, MemoryWriter

logger = logging.getLogger("arf.memory.llm_writer")


def _parse_json_response(text: str) -> dict:
    """Extract valid JSON from an LLM response, stripping markdown fences and extra text."""
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    if text.startswith("```"):
        nl = text.find("\n")
        if nl > 0:
            text = text[nl + 1:]
        else:
            text = text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    # If still not valid JSON, try to extract the first { ... } block
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: find outermost { ... } in the text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    # Handle double-brace escaping (model copies template literal)
    if text.startswith("{{") and text.endswith("}}"):
        text = text[1:-1]
    return json.loads(text)


_EXTRACTION_PROMPT = """\
You are a memory manager. Extract key information from the conversation below.
For each fact, preference, decision, or important context the user expressed,
decide what to do and return a JSON action list.

Rules:
- "add": new information not yet in existing memories
- "update": modifies or contradicts an existing memory → use "replaces" with the old id
- "delete": existing memory is now obsolete → use "replaces" with the old id
- Categories: "fact" (what is), "preference" (what user likes/wants),
  "decision" (what was agreed), "context" (situational info)
- Content must be concise, factual, self-contained (≤300 chars)
- If nothing new, return {"actions": []}

Existing memories:
{existing_index}

Conversation:
{turn_text}

Return ONLY valid JSON (no markdown, no explanation):
{"actions": [{"action": "add", "entry": {"category": "fact", "content": "..."}},
             {"action": "update", "entry": {...}, "replaces": "old-id"}]}\
"""


class LLMMemoryWriter:
    """LLM-driven memory extraction using a dedicated cheap model.

    After each turn, feeds the conversation to a small/fast LLM (e.g.
    DeepSeek V4 Flash with thinking disabled, temp 0.3) which extracts
    structured memories.  The LLM also handles dedup and merge against
    existing entries.
    """

    def __init__(self, model_call) -> None:
        self._call_model = model_call

    async def extract_and_write(
        self,
        store: MemoryStore,
        turn_messages: list[dict],
        existing_entries: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        # Build existing memory index (id + one-line summary)
        existing_index = "\n".join(
            f"[{e.id}] ({e.category}) {e.content[:120]}"
            for e in existing_entries
        ) or "(no existing memories)"

        # Build turn text from messages
        turn_text = "\n".join(
            f"[{m.get('role', '?')}] {m.get('content', '')[:500]}"
            for m in turn_messages
        )

        prompt = _EXTRACTION_PROMPT.replace("{existing_index}", existing_index).replace("{turn_text}", turn_text)

        logger.info("Memory extraction: calling LLM with %d existing entries", len(existing_entries))

        response = ""
        try:
            response = await self._call_model(prompt)
            result = _parse_json_response(response)
        except json.JSONDecodeError:
            logger.warning("Memory extraction: invalid JSON. Raw: %.300s", response)
            return existing_entries
        except Exception:
            logger.exception("Memory extraction: LLM call failed, skipping turn")
            return existing_entries

        actions = result.get("actions", [])
        if not actions:
            logger.info("Memory extraction: LLM returned empty actions — nothing new")
        current_ids = {e.id for e in existing_entries}
        entries_map = {e.id: e for e in existing_entries}

        for action in actions:
            act = action.get("action", "")
            entry_data = action.get("entry", {})

            if act == "delete":
                eid = action.get("replaces", "")
                if eid in current_ids:
                    await store.delete(eid)
                    entries_map.pop(eid, None)

            elif act in ("add", "update"):
                content = entry_data.get("content", "")[:500]
                if not content:
                    continue
                category = entry_data.get("category", "fact")
                if category not in ("fact", "preference", "decision", "context"):
                    category = "fact"

                replaces = action.get("replaces") if act == "update" else None

                entry = MemoryEntry(
                    id=str(uuid.uuid4()),
                    content=content,
                    category=category,
                    timestamp=time.time(),
                    source_turn=0,
                    replaces=replaces,
                )
                await store.save(entry)
                entries_map[entry.id] = entry

        net_change = len(entries_map) - len(existing_entries)
        if net_change or len(actions) > 0:
            logger.info("LLM memory: %d actions, %d→%d entries (net %+d)", len(actions), len(existing_entries), len(entries_map), net_change)
        return list(entries_map.values())
