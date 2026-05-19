from pathlib import Path

USER_RESTRICTED_PREFIXES = ("/tools/", "/skills/", "/models/")


def execute(path: str, _agent_mode: str = "sys") -> dict:
    if _agent_mode == "user":
        for prefix in USER_RESTRICTED_PREFIXES:
            if prefix in path or path.lstrip("/").startswith(prefix.strip("/") + "/"):
                return {
                    "error": (
                        f"User Agent 无法删除 {path}。"
                        f"tools/, skills/, models/ 路径下的文件操作需要 Sys Agent。"
                        f"请调用 handoff_to_sys 转交任务。"
                    )
                }

    p = Path(path)
    try:
        if not p.exists():
            return {"error": f"File not found: {path}"}
        if p.is_dir():
            return {"error": f"Cannot delete directories: {path}"}
        deleted_path = p.with_name(p.name + "_deleted")
        p.rename(deleted_path)
        return {"ok": True, "path": str(p), "deleted_as": str(deleted_path)}
    except Exception as e:
        return {"error": str(e)}
