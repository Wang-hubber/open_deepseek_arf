from pathlib import Path

USER_RESTRICTED_PREFIXES = ("tools/", "skills/", "models/")


def execute(path: str, content: str, _agent_mode: str = "sys") -> dict:
    if _agent_mode == "user":
        for prefix in USER_RESTRICTED_PREFIXES:
            if path.lstrip("/").startswith(prefix):
                return {
                    "error": (
                        f"User Agent 无法写入 {path}。"
                        f"tools/, skills/, models/ 路径下的文件操作需要 Sys Agent。"
                        f"请调用 handoff_to_sys 转交任务。"
                    )
                }

    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

        preview = content[:600]
        if len(content) > 600:
            preview += f"\n... ({len(content) - 600} more chars)"

        return {
            "ok": True,
            "path": str(p),
            "filename": p.name,
            "bytes": len(content),
            "preview": preview,
        }
    except Exception as e:
        return {"error": str(e)}
