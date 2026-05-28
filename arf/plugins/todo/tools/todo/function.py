"""todo — workspace task list management."""
from pathlib import Path

WORKSPACE = Path("workspaces/default")
TODO_FILE = WORKSPACE / "todo.md"


async def execute(action: str, items: list[str] | None = None) -> dict:
    """Manage a task list file in the workspace.

    Actions:
      list  — return current todo items
      add   — append items to the list
      check — mark items as complete (strikethrough)
      clear — remove all items
    """
    try:
        TODO_FILE.parent.mkdir(parents=True, exist_ok=True)

        if action == "list":
            if not TODO_FILE.exists():
                return {"ok": True, "items": [], "count": 0}
            lines = TODO_FILE.read_text(encoding="utf-8").strip().split("\n")
            items_list = []
            for line in lines:
                if not line.strip():
                    continue
                stripped = line.strip("- [] ").strip("- [x] ")
                is_done = "~~" in line or "[x]" in line
                items_list.append({"done": is_done, "text": stripped})
            return {"ok": True, "items": items_list, "count": len(items_list)}

        elif action == "add":
            if not items:
                return {"error": "items required for 'add' action"}
            new_lines = [f"- [ ] {item}" for item in items if item.strip()]
            if TODO_FILE.exists():
                existing = TODO_FILE.read_text(encoding="utf-8")
                TODO_FILE.write_text(existing.rstrip() + "\n" + "\n".join(new_lines) + "\n", encoding="utf-8")
            else:
                TODO_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            return {"ok": True, "added": len(new_lines)}

        elif action == "check":
            if not items or not TODO_FILE.exists():
                return {"ok": False, "error": "No items to check or todo file missing"}
            content = TODO_FILE.read_text(encoding="utf-8")
            for item in items:
                content = content.replace(f"- [ ] {item}", f"~~- [x] {item}~~")
            TODO_FILE.write_text(content, encoding="utf-8")
            return {"ok": True, "checked": len(items)}

        elif action == "clear":
            TODO_FILE.write_text("", encoding="utf-8")
            return {"ok": True}

        else:
            return {"error": f"Unknown action: {action}"}
    except Exception as e:
        return {"error": str(e)}
