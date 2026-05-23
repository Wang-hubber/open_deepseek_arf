"""file_reader -- async read files or list directories."""
from pathlib import Path

WORKSPACE = Path("workspaces/default")


async def execute(operation: str, path: str) -> dict:
    p = WORKSPACE / path
    try:
        if operation == "read":
            if not p.exists():
                return {"error": f"File not found: {path}"}
            if p.is_dir():
                return {"error": f"Path is a directory, use list instead: {path}"}
            text = p.read_text(encoding="utf-8")
            return {"content": text, "size": len(text)}
        elif operation == "list":
            if not p.exists():
                return {"error": f"Directory not found: {path}"}
            if not p.is_dir():
                return {"error": f"Not a directory: {path}"}
            items = []
            for child in sorted(p.iterdir()):
                items.append({
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else 0,
                })
            return {"items": items, "count": len(items)}
        else:
            return {"error": f"Unknown operation: {operation}"}
    except Exception as e:
        return {"error": str(e)}
