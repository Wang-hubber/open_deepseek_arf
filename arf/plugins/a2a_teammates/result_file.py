"""Peer result file writer — same format as a2a_subagents result files."""
from __future__ import annotations

from pathlib import Path


def write_peer_result(
    *,
    data_dir: str,
    group_id: str,
    correlation_id: str,
    agent_role: str,
    task_description: str,
    full_result: str,
    tool_calls: list[dict] | None = None,
    file_changes: dict[str, list[str]] | None = None,
    turn_count: int = 0,
) -> str:
    """Write peer task result to persistent file.

    Returns relative path from data_dir root.
    """
    import time as _time

    result_dir = Path(data_dir) / group_id / "peer_results"
    result_dir.mkdir(parents=True, exist_ok=True)

    safe_id = correlation_id.replace("/", "_").replace(" ", "_")
    result_path = result_dir / f"{safe_id}.md"

    parts = [
        "# Peer Task Result",
        "",
        f"**Correlation ID:** `{correlation_id}`",
        f"**Agent:** {agent_role}",
        f"**Completed at:** {_time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Turns:** {turn_count}",
        "",
        "## Task",
        "",
        task_description,
        "",
        "## Result",
        "",
        full_result or "(no output)",
    ]

    if tool_calls:
        success_count = sum(1 for tc in tool_calls if tc.get("success"))
        parts.append("")
        parts.append(f"## Tool Calls ({len(tool_calls)} total, {success_count} ok)")
        parts.append("")
        parts.append("| # | Tool | Success | Duration | Error |")
        parts.append("|---|------|---------|----------|-------|")
        for i, tc in enumerate(tool_calls):
            err = tc.get("error", "") or ""
            dur = f"{tc.get('duration_ms', 0)}ms"
            ok = "yes" if tc.get("success") else "no"
            parts.append(
                f"| {i+1} | `{tc.get('tool_name', '?')}` | {ok} | {dur} | {err} |"
            )

    if file_changes:
        parts.append("")
        parts.append("## File Changes")
        parts.append("")
        for category in ("added", "modified", "deleted"):
            paths = file_changes.get(category, [])
            if paths:
                prefix = {"added": "+", "modified": "~", "deleted": "-"}[category]
                for p in paths:
                    parts.append(f"- {prefix} `{p}`")

    result_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return str(result_path.relative_to(data_dir))
