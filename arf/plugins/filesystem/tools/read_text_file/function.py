"""read_text_file tool — read file content as text."""
import os


async def execute(path: str, head: int | None = None, tail: int | None = None,
                  encoding: str = "utf-8", **kwargs) -> dict:
    if head is not None and tail is not None:
        return {"ok": False, "error": "Cannot specify both head and tail parameters simultaneously"}

    if not os.path.isfile(path):
        return {"ok": False, "error": f"Not a file: {path}"}

    try:
        with open(path, "r", encoding=encoding) as f:
            lines = f.readlines()
    except (UnicodeDecodeError, LookupError) as e:
        return {"ok": False, "error": f"Cannot read {path} as {encoding} text — {e}"}
    except OSError as e:
        return {"ok": False, "error": str(e)}

    total_lines = len(lines)

    if tail is not None:
        lines = lines[-tail:]
    elif head is not None:
        lines = lines[:head]

    numbered = []
    for i, line in enumerate(lines):
        line_num = 1 + i
        if tail is not None:
            line_num = total_lines - len(lines) + 1 + i
        elif head is not None:
            line_num = 1 + i
        else:
            line_num = 1 + i
        numbered.append(f"{line_num}\t{line.rstrip()}")

    return {
        "ok": True,
        "content": "\n".join(numbered),
        "lines_read": len(lines),
        "total_lines": total_lines,
    }
