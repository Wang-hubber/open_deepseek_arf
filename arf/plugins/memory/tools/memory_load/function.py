"""Load resident memory from memory.md for agent context recall."""
from pathlib import Path


async def execute(memory_dir: str = "") -> dict:
    memory_path = Path(memory_dir or "./data/memory") / "memory.md"
    if not memory_path.exists():
        return {"ok": True, "content": "", "message": "No resident memory yet"}

    content = memory_path.read_text(encoding="utf-8")
    return {"ok": True, "content": content, "size": len(content)}
