"""ToolGuardPlugin — merged permission policy + parameter security check."""
from arf.core.plugin_context import PluginContext
from arf.session import PermissionLists, PermissionRegistry
from arf.sandbox.path_sandbox import PathSandbox


class ToolGuardError(Exception):
    """Base exception for ToolGuard plugin."""


class PermissionDenied(ToolGuardError):
    """Raised when a tool call is denied by permission policy."""


class SandboxViolation(ToolGuardError):
    """Raised when a tool call violates sandbox security rules."""


class ToolGuardPlugin:
    """Unified tool permission + security check plugin.

    Two-layer enforcement:
      Layer 1 — Permission policy (deny/ask/allow lists)
      Layer 2 — Sandbox security (path traversal, suspicious parameters)
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._lists = PermissionLists(
            deny=cfg.get("deny_list", []),
            ask=cfg.get("ask_list", []),
            allow=cfg.get("allow_list", []),
        )
        self._registry = PermissionRegistry()
        self._sandbox = PathSandbox() if cfg.get("sandbox_check", True) else None

    @property
    def name(self) -> str:
        return "tool_guard"

    @property
    def hooks(self) -> dict[str, str]:
        return {"pre_dispatch": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if ctx.current_step != "execute_tools":
            return

        tool_calls = ctx.state.get("_pending_tool_calls", [])
        for tc in tool_calls:
            name = tc.get("name", "")
            params = tc.get("params", {})

            # Layer 1: Permission policy (deny/ask/allow)
            result = self._registry.evaluate(name, params, self._lists)
            if result.action == "deny":
                raise PermissionDenied(f"Tool '{name}' denied: {result.reason}")

            # Layer 2: Security check (path traversal, injection)
            if self._sandbox:
                for key, value in params.items():
                    if isinstance(value, str) and (".." in value or value.startswith("/")):
                        raise SandboxViolation(
                            f"Tool '{name}' param '{key}' contains suspicious path: {value}"
                        )
