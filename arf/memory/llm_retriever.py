"""LLMMemoryRetriever — use a cheap LLM to select relevant memories for a query."""
from __future__ import annotations

import json
import logging

from arf.core.protocols import MemoryEntry, MemoryStore, MemoryRetriever

logger = logging.getLogger("arf.memory.llm_retriever")


def _parse_json_response(text: str) -> dict:
    """Extract valid JSON from an LLM response, stripping markdown fences and extra text."""
    text = text.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        if nl > 0:
            text = text[nl + 1:]
        else:
            text = text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    if text.startswith("{{") and text.endswith("}}"):
        text = text[1:-1]
    return json.loads(text)

_RETRIEVAL_PROMPT = """\
Select the most relevant memories for the user's query. Return ONLY the IDs.

Available memories:
{memory_index}

User query: {query}

Rules:
- Return at most {top_k} IDs
- Only return IDs that are clearly relevant to the query
- If nothing is relevant, return an empty list

Return ONLY valid JSON (no markdown, no explanation):
{"relevant_ids": ["id1", "id2"]}\
"""


class LLMMemoryRetriever:
    """LLM-driven memory retrieval using a dedicated cheap model.

    Feeds the user query + a compact memory index to a small/fast LLM
    which selects the most relevant entries.  Full entries are then loaded
    from the store and returned ranked by relevance.
    """

    def __init__(self, model_call) -> None:
        self._call_model = model_call

    async def retrieve(
        self,
        store: MemoryStore,
        query_context: str,
        session_id: str,
        max_tokens: int = 2000,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        entries = await store.load(session_id)
        if not entries:
            return []

        # Build compact memory index
        index_lines = [
            f"[{e.id}] ({e.category}) {e.content[:120]}"
            for e in entries
        ]
        memory_index = "\n".join(index_lines)

        prompt = _RETRIEVAL_PROMPT.replace("{memory_index}", memory_index).replace("{query}", query_context[:500]).replace("{top_k}", str(min(top_k, len(entries))))

        response = ""
        try:
            response = await self._call_model(prompt)
            result = _parse_json_response(response)
        except json.JSONDecodeError:
            logger.warning("Memory retrieval: invalid JSON. Raw: %.200s", response)
            entries.sort(key=lambda e: e.timestamp, reverse=True)
            return entries[:top_k]
        except Exception:
            logger.exception("Memory retrieval: LLM call failed, falling back to recent-first")
            entries.sort(key=lambda e: e.timestamp, reverse=True)
            return entries[:top_k]

        relevant_ids = set(result.get("relevant_ids", []))

        # Return relevant entries in index order, with relevance boost
        selected = [e for e in entries if e.id in relevant_ids]
        for e in selected:
            e.relevance_score = 1.0

        # Trim by max_tokens (approximate: chars/3 ≈ tokens)
        total_chars = sum(len(e.content) for e in selected)
        while total_chars > max_tokens * 3 and len(selected) > 1:
            removed = selected.pop()
            total_chars -= len(removed.content)

        return selected
