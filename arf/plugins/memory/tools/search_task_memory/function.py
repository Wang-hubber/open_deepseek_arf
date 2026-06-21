"""search_task_memory — LLM-driven search for relevant task experiences."""
from __future__ import annotations

import logging
from arf.memory.index import MemoryIndex

logger = logging.getLogger("arf.memory.tools.search_task_memory")

_index: MemoryIndex | None = None
_call_model = None


async def execute(query: str, **kwargs) -> dict:
    """Search task memory for experiences relevant to the query."""
    global _index, _call_model
    if _index is None or _call_model is None:
        return {"ok": False, "error": "Task memory not available"}

    content = _index.load_tasks()
    if not content.strip():
        return {"ok": True, "matches": [], "message": "No task memory yet."}

    prompt = _SEARCH_PROMPT.format(tasks=content, query=query)

    try:
        resp = await _call_model(
            [{"role": "user", "content": prompt}],
            model_name="",
        )
        raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
    except Exception:
        logger.warning("Task memory search failed", exc_info=True)
        return {"ok": False, "error": "Search call failed"}

    try:
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        import json as _json
        result = _json.loads(raw)
        return {"ok": True, "matches": result.get("matches", [])}
    except Exception:
        logger.warning("Failed to parse search result: %s", raw[:200])
        return {"ok": False, "error": "Failed to parse search result"}


_SEARCH_PROMPT = """Search the task memory notebook for experiences relevant to the query. Return matching entries with their approach and lessons.

## Task Memory Notebook

{tasks}

## Query

{query}

## Output Format

Output ONLY a valid JSON object:
{{
  "matches": [
    {{
      "category": "refactoring",
      "description": "重构 auth 模块",
      "approach": ["新增 TokenStorage adapter", "逐子类迁移"],
      "lessons": ["不要假设 SessionStore 子类无隐式依赖 (×3)"]
    }}
  ]
}}

If nothing relevant, return {{"matches": []}}."""
