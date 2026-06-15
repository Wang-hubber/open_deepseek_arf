"""ToolGuardPlugin — mode-aware permission policy + parameter security check."""
import logging

from arf.core.plugin_context import PluginContext
from arf.session import PermissionLists, PermissionRegistry
from arf.sandbox.path_sandbox import PathSandbox

logger = logging.getLogger("arf.plugins.tool_guard")


class ToolGuardError(Exception):
    """Base exception for ToolGuard plugin."""


class PermissionDenied(ToolGuardError):
    """Raised when a tool call is denied by permission policy."""


class SandboxViolation(ToolGuardError):
    """Raised when a tool call violates sandbox security rules."""


class ToolGuardPlugin:
    """Mode-aware tool permission + security check plugin.

    Three-layer enforcement gated by effective_mode:
      auto  — all tools allowed (skip checks)
      plan  — read-only tools allowed (via readOnlyHint annotation);
              side-effect tools denied
      ask   — standard deny/ask/allow list matching
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._lists = PermissionLists(
            deny=cfg.get("deny", []),
            ask=cfg.get("ask", []),
            allow=cfg.get("allow", []),
            deny_patterns=cfg.get("deny_patterns", []),
        )
        self._registry = PermissionRegistry()
        self._sandbox = PathSandbox() if cfg.get("sandbox_check", True) else None
        self._name_resolver = None  # injected by base.py after plugin construction

    @property
    def name(self) -> str:
        return "tool_guard"

    @property
    def hooks(self) -> dict[str, str]:
        return {"pre_action": "blocking"}

    def set_name_resolver(self, resolver) -> None:
        """Inject tool name → namespaced name resolver (called by base.py).

        Resolves both the callback AND the list entries so that bare names
        in plugins_config are converted to namespaced names before matching.
        """
        self._name_resolver = resolver
        self._lists.deny = {resolver(t) for t in self._lists.deny}
        self._lists.ask = {resolver(t) for t in self._lists.ask}
        self._lists.allow = {resolver(t) for t in self._lists.allow}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if ctx.current_step != "execute_tools":
            return

        # Read effective session mode (set by ControlPlane before pre_action)
        effective_mode = ctx.hook_data.get("effective_mode", "ask")

        # Build lookup: tool_name → annotations dict from MCP tool definitions
        tool_annotations: dict[str, dict] = {}
        for td in ctx.tool_definitions:
            tname = td.get("name", "")
            tool_annotations[tname] = td.get("annotations", {})

        tool_calls = ctx.state.get("_pending_tool_calls", [])
        for tc in tool_calls:
            name = tc.get("name", "")
            params = tc.get("params", {})

            # Resolve bare names → namespaced names at check time
            resolved_name = self._name_resolver(name) if self._name_resolver else name

            # --- auto mode: all tools allowed, skip checks ---
            if effective_mode == "auto":
                if resolved_name in self._lists.ask:
                    logger.warning(
                        "Auto mode: tool '%s' is in the ask list but "
                        "session_mode=auto requires zero user interaction. "
                        "The tool will be auto-allowed. Either move it to "
                        "the allow list or switch to ask/plan mode if "
                        "confirmation is required.",
                        name,
                    )
                continue

            # --- plan mode: read-only tools only ---
            if effective_mode == "plan":
                ann = tool_annotations.get(name, {})
                hint = ann.get("readOnlyHint")
                if hint is None:
                    # Tool didn't declare — assume side effect (safe default)
                    logger.warning(
                        "Tool '%s' has no readOnlyHint annotation — "
                        "denied in plan mode. Add 'annotations: {readOnlyHint: true/false}' "
                        "to its tool.yaml.", name)
                    self._block_all(tool_calls, ctx,
                                    f"PLAN mode: tool '{name}' has no readOnlyHint annotation "
                                    f"(assumed side effect)")
                    raise PermissionDenied(
                        f"Tool '{name}' denied in plan mode: missing readOnlyHint annotation")
                if hint is not True:
                    self._block_all(tool_calls, ctx,
                                    f"PLAN mode: tool '{name}' has side effects")
                    raise PermissionDenied(f"Tool '{name}' denied in plan mode")
                continue

            # --- ask mode: standard list-based permission checks ---
            result = self._registry.evaluate(resolved_name, params, self._lists)
            if result.action == "deny":
                self._block_all(tool_calls, ctx, result.reason)
                raise PermissionDenied(f"Tool '{name}' denied")

            # Layer 2: Security check (path traversal, injection)
            # Only check path-annotated params — content-type params legitimately
            # contain ".." (e.g. relative paths in config files, Python code).
            if self._sandbox:
                ann = tool_annotations.get(resolved_name, {})
                param_props = ann.get("parameters", {}).get("properties", {})
                for key, value in params.items():
                    is_path = param_props.get(key, {}).get("format") == "path"
                    if is_path and isinstance(value, str) and ".." in value:
                        ctx.emit("guard_block", {
                            "tool_name": name,
                            "reason": f"sandbox: {key} contains suspicious path",
                        })
                        msgs = ctx.state.setdefault("messages", [])
                        for tc_cleanup in tool_calls:
                            msgs.append({
                                "role": "tool",
                                "tool_call_id": tc_cleanup.get("id", ""),
                                "content": "[blocked] sandbox violation",
                            })
                        ctx.state["_pending_tool_calls"] = []
                        raise SandboxViolation(f"Tool '{name}': sandbox violation in param '{key}'")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _block_all(self, tool_calls: list, ctx: PluginContext, reason: str) -> None:
        """Inject blocked events + messages for all pending tool calls."""
        for tc in tool_calls:
            event_data = {
                "tool_name": tc.get("name", ""),
                "id": tc.get("id", ""),
            }
            ctx.emit("tool_call_start", {
                **event_data,
                "arguments": tc.get("params", {}),
            })
            ctx.inject_engine_event("tool_call_start", {
                **event_data,
                "arguments": tc.get("params", {}),
            })
            ctx.emit("tool_call_end", {
                **event_data,
                "success": False,
                "blocked": True,
                "result": f"Blocked: {reason}",
                "error": f"Blocked: {reason}",
            })
            ctx.inject_engine_event("tool_call_end", {
                **event_data,
                "success": False,
                "blocked": True,
                "result": f"Blocked: {reason}",
                "error": f"Blocked: {reason}",
            })
        msgs = ctx.state.setdefault("messages", [])
        for tc in tool_calls:
            msgs.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": f"[blocked] {reason}",
            })
        ctx.state["_pending_tool_calls"] = []
