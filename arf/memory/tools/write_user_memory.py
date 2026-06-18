"""write_user_memory — write user-level memory (shared group or individual)."""
from arf.memory.index import MemoryIndex

_index: MemoryIndex | None = None


async def execute(content: str, **kwargs) -> dict:
    """Persist user memory. Writes to group dir when configured, else individual."""
    global _index
    if _index is None:
        return {"ok": False, "error": "MemoryIndex not wired"}
    # Prefer group memory when configured, fall back to individual
    if _index._group_dir:
        _index.save_group_user(content)
    else:
        _index.save_user(content)
    return {"ok": True}
