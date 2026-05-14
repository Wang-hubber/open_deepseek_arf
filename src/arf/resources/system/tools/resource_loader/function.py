"""Resource loader tool -- activate/deactivate tools on demand.

This tool is injected with agent state at runtime (_active_tools, _kernel_tools,
_all_tool_names, _registry) so it can mutate the active set directly and check
dependencies. Changes take effect on the next engine loop iteration (same user
turn) because call_model rebuilds tools dynamically via _build_openai_tools().
"""


def execute(
    action: str,
    tools: list[str] | None = None,
    _active_tools: set | None = None,
    _kernel_tools: frozenset | None = None,
    _all_tool_names: set | None = None,
    _registry=None,
) -> dict:
    tools = tools or []
    active = _active_tools if _active_tools is not None else set()
    kernel = _kernel_tools if _kernel_tools is not None else frozenset()
    all_names = _all_tool_names if _all_tool_names is not None else set()

    handlers = {
        "activate":    _handle_activate,
        "deactivate":  _handle_deactivate,
        "list_active": _handle_list_active,
    }
    handler = handlers.get(action)
    if not handler:
        return {"error": f"Unknown action: {action}"}
    return handler(active, kernel, all_names, tools, _registry)


def _handle_activate(active: set, kernel: frozenset, all_names: set,
                     tools: list, _registry=None) -> dict:
    if not tools:
        return {"error": "tools list is required for activate"}

    # Check dependencies for each tool before activation
    deps_missing: dict[str, list] = {}
    if _registry:
        for name in tools:
            dep_result = _registry.check_deps("tools", name)
            if not dep_result.get("ok"):
                missing = dep_result.get("missing", [])
                if missing:
                    deps_missing[name] = missing

    if deps_missing:
        details = []
        for tool_name, missing in deps_missing.items():
            for m in missing:
                details.append(f"{m['type']}/{m['name']}")
        return {
            "ok": False,
            "error": "Cannot activate: dependencies not configured",
            "deps_missing": deps_missing,
            "hint": f"Use resource_registrar to configure: {', '.join(details)}",
        }

    activated = []
    unknown = []
    for name in tools:
        if name not in all_names:
            unknown.append(name)
            continue
        active.add(name)
        activated.append(name)

    result = {"ok": True, "activated": activated, "active_count": len(active)}
    if unknown:
        result["unknown_tools"] = unknown
        result["hint"] = "Unknown tools do not exist in the registry. Check tool names via skills."
    return result


def _handle_deactivate(active: set, kernel: frozenset, all_names: set,
                       tools: list, _registry=None) -> dict:
    if not tools:
        return {"error": "tools list is required for deactivate"}

    deactivated = []
    blocked = []
    for name in tools:
        if name in kernel:
            blocked.append(name)
            continue
        if name in active:
            active.discard(name)
            deactivated.append(name)

    result = {"ok": True, "deactivated": deactivated, "active_count": len(active)}
    if blocked:
        result["blocked"] = blocked
        result["hint"] = "Kernel tools cannot be deactivated."
    return result


def _handle_list_active(active: set, kernel: frozenset, all_names: set,
                        _tools: list, _registry=None) -> dict:
    discoverable = sorted(all_names - active)
    active_list = sorted(active)
    return {
        "ok": True,
        "active": active_list,
        "active_count": len(active),
        "kernel": sorted(kernel),
        "discoverable": discoverable,
        "discoverable_count": len(discoverable),
    }
