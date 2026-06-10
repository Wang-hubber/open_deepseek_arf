"""Dependency graph validation for plan steps."""


def validate_steps(steps: list[dict]) -> dict:
    """Validate plan steps: indices, symmetry, no cycles.

    Returns {"ok": True} or {"ok": False, "error": "...", "suggestion": "..."}.
    """
    if not steps:
        return {"ok": False, "error": "steps list is empty", "suggestion": "Provide at least one step"}

    indices = {s["index"] for s in steps}
    if len(indices) != len(steps):
        return {"ok": False, "error": "duplicate step indices detected", "suggestion": "Ensure each step has a unique index"}

    for s in steps:
        idx = s["index"]

        # Self-dependency
        if idx in s.get("depends_on", []) or idx in s.get("blocks", []):
            return {"ok": False, "error": f"step {idx} references itself", "suggestion": "Remove self-referencing dependency"}

        # Invalid depends_on references
        for dep in s.get("depends_on", []):
            if dep not in indices:
                return {"ok": False, "error": f"step {idx} depends_on invalid step {dep}", "suggestion": f"Step {dep} does not exist"}

        # Invalid blocks references
        for blk in s.get("blocks", []):
            if blk not in indices:
                return {"ok": False, "error": f"step {idx} blocks invalid step {blk}", "suggestion": f"Step {blk} does not exist"}

    # Symmetry check: A blocks B  B depends_on A
    for a in steps:
        for blk in a.get("blocks", []):
            b = _find_step(steps, blk)
            if a["index"] not in b.get("depends_on", []):
                return {
                    "ok": False,
                    "error": f"asymmetric dependency: step {a['index']} blocks step {blk} but step {blk} doesn't depend_on it",
                    "suggestion": f"Either remove the blocks relationship or add step {a['index']} to step {blk}'s depends_on",
                }

    # Cycle detection via topological sort (Kahn's algorithm)
    in_degree = {s["index"]: len(s.get("depends_on", [])) for s in steps}
    edge_map: dict[int, list[int]] = {s["index"]: list(s.get("blocks", [])) for s in steps}

    queue = [idx for idx, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for neighbor in edge_map.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(steps):
        return {"ok": False, "error": "circular dependency detected", "suggestion": "Remove one of the dependencies in the cycle"}

    return {"ok": True}


def _find_step(steps: list[dict], index: int) -> dict:
    for s in steps:
        if s["index"] == index:
            return s
    return {}
