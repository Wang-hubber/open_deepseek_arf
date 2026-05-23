"""memory_store -- write to memory/ directory with structured markdown."""
from pathlib import Path
from datetime import datetime

MEMORY_DIR = Path("memory")


async def execute(content: str, category: str = "fact") -> dict:
    try:
        mem_path = MEMORY_DIR / f"{category}.md"
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n## {category.capitalize()} ({timestamp})\n{content}\n"

        if mem_path.exists():
            existing = mem_path.read_text(encoding="utf-8")
            existing += entry
            mem_path.write_text(existing, encoding="utf-8")
        else:
            mem_path.write_text(
                f"# {category.capitalize()} Memory\n"
                f"Auto-stored memories for category: {category}\n{entry}",
                encoding="utf-8",
            )

        return {
            "ok": True,
            "category": category,
            "path": str(mem_path),
            "size": len(content.encode("utf-8")),
        }
    except Exception as e:
        return {"error": str(e)}
