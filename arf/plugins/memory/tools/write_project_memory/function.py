"""write_project_memory — write project-level memory (shared group or individual)."""
from arf.memory.index import MemoryIndex

_index: MemoryIndex | None = None


async def execute(content: str, **kwargs) -> dict:
    """Persist project memory. Writes to group dir when configured, else individual."""
    global _index
    if _index is None:
        return {"ok": False, "error": "MemoryIndex not wired"}
    if _index._group_dir:
        _index.save_group_project(content)
    else:
        _index.save_project(content)
    return {"ok": True}
