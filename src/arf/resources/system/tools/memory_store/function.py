"""Memory store tool -- long-term memory with single-file storage and backup rotation.

Storage layout:
    memory/long_term.md              -- currently active (<= 1 MB)
    memory/long_term_{ts}_bak.md     -- backup before last mutation (keeps only latest)
"""

import re
from datetime import datetime, timezone
from pathlib import Path

MAX_TOTAL_SIZE = 1 * 1024 * 1024       # 1 MB hard limit
COMPRESSION_THRESHOLD = 716_800        # 70% -- triggers advisory flag
MEMORY_FILE = "memory/long_term.md"


def execute(
    action: str,
    content: str = "",
    model: str = "",
    _workspace_dir: str = "",
) -> dict:
    base_dir = _resolve_workspace(_workspace_dir)

    handlers = {
        "read":     _handle_read,
        "write":    _handle_write,
        "stats":    _handle_stats,
        "compress": _handle_compress,
    }
    handler = handlers.get(action)
    if not handler:
        return {"error": f"Unknown action: {action}"}
    return handler(base_dir, content, model)


def _resolve_workspace(workspace_dir: str) -> Path:
    if workspace_dir:
        return Path(workspace_dir)
    return Path.cwd()


def _mem_path(base: Path) -> Path:
    return base / MEMORY_FILE


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _rotate_backup(mem_file: Path):
    """Rename current long_term.md -> long_term_{ts}_bak.md, keeping only
    the latest backup (older backups are removed)."""
    if not mem_file.exists():
        return
    # Remove any existing backup(s)
    parent = mem_file.parent
    for old in sorted(parent.glob("long_term_*_bak.md")):
        old.unlink()
    bak_name = f"long_term_{_timestamp()}_bak.md"
    mem_file.rename(parent / bak_name)


def _handle_read(base: Path, _content: str = "", _model: str = "") -> dict:
    mem_file = _mem_path(base)
    if not mem_file.exists():
        return {"ok": True, "content": "", "exists": False}
    text = mem_file.read_text(encoding="utf-8")
    return {"ok": True, "content": text, "size": len(text.encode("utf-8")), "exists": True}


def _handle_write(base: Path, content: str = "", _model: str = "") -> dict:
    if not content:
        return {"error": "content is required for write action"}
    content_bytes = len(content.encode("utf-8"))
    if content_bytes > MAX_TOTAL_SIZE:
        return {
            "error": f"Content exceeds 1 MB limit ({content_bytes} bytes). "
                     "Run memory_compress to reduce size before writing.",
            "compression_needed": True,
        }

    mem_file = _mem_path(base)
    mem_file.parent.mkdir(parents=True, exist_ok=True)
    _rotate_backup(mem_file)
    mem_file.write_text(content, encoding="utf-8")

    return {
        "ok": True,
        "size": content_bytes,
        "usage_percent": round(content_bytes / MAX_TOTAL_SIZE * 100, 1),
        "compression_needed": content_bytes > COMPRESSION_THRESHOLD,
    }


def _handle_stats(base: Path, _content: str = "", _model: str = "") -> dict:
    mem_file = _mem_path(base)
    if not mem_file.exists():
        return {
            "ok": True,
            "total_size_bytes": 0,
            "total_size_human": "0 B",
            "max_size_bytes": MAX_TOTAL_SIZE,
            "usage_percent": 0.0,
            "compression_needed": False,
        }

    size = mem_file.stat().st_size
    return {
        "ok": True,
        "total_size_bytes": size,
        "total_size_human": _format_size(size),
        "max_size_bytes": MAX_TOTAL_SIZE,
        "usage_percent": round(size / MAX_TOTAL_SIZE * 100, 1),
        "compression_needed": size > COMPRESSION_THRESHOLD,
        "compression_threshold_bytes": COMPRESSION_THRESHOLD,
    }


def _handle_compress(base: Path, content: str = "", model: str = "") -> dict:
    """Compress long-term memory, optionally using a specified model.

    If `model` is provided, the tool loads that model's config, creates a
    ModelAdapter, and sends a compression prompt. The compressed result is
    then written with backup rotation.

    If `model` is empty, falls back to writing content directly (caller is
    responsible for producing the compressed text).
    """
    if not content and not model:
        return {"error": "content is required for compress action"}

    if model and not content:
        # Use the specified model to generate compressed content
        mem_file = _mem_path(base)
        original = mem_file.read_text(encoding="utf-8") if mem_file.exists() else ""
        if not original:
            return {"error": "No existing memory to compress"}

        result = _call_model_for_compress(base, model, original)
        if "error" in result:
            return result
        content = result["content"]

    if not content:
        return {"error": "content is required for compress action"}

    return _handle_write(base, content)


def _call_model_for_compress(base: Path, model_name: str, original: str) -> dict:
    """Load a model config and use it to compress memory content."""
    import yaml
    from arf.resources.model_adapter import ModelAdapter

    config_path = base / "models" / model_name / "config.yaml"
    if not config_path.exists():
        return {"error": f"Model '{model_name}' config not found at {config_path}"}

    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return {"error": f"Failed to read model config: {e}"}

    model_cfg = cfg.get("config", {})
    if not model_cfg.get("base_url") or not model_cfg.get("api_key"):
        return {"error": f"Model '{model_name}' has incomplete config"}

    prompt = _compression_prompt(original)

    try:
        adapter = ModelAdapter(model_cfg)
        compressed = adapter.chat([{"role": "user", "content": prompt}])
        return {"content": compressed}
    except Exception as e:
        return {"error": f"Compression model call failed: {e}"}


def _compression_prompt(original: str) -> str:
    return (
        "You are a memory compression assistant. Below is the user's long-term "
        "memory file. Compress it to remove redundancy, merge related information, "
        "and preserve all unique facts and preferences. Follow these rules:\n\n"
        "1. PRESERVE all unique facts, preferences, and decisions\n"
        "2. REMOVE duplicates, redundant phrasing, outdated values\n"
        "3. MERGE related facts into coherent paragraphs\n"
        "4. KEEP the same markdown structure with sections:\n"
        "   # Long-Term Memory\n"
        "   ## User Profile\n"
        "   ## Preferences\n"
        "   ## Important Facts\n"
        "   ## Decisions\n"
        "5. Add a footer: 'Last updated: <current datetime>' and 'Total entries: <count>'\n\n"
        "Original memory:\n\n"
        f"{original}"
    )


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
