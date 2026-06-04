"""FunctionBackend — call Python functions directly with optional rollback."""
import time
from collections.abc import Callable
from arf.core.config_base import ToolConfig
from arf.core.results import ToolResult


class FunctionBackend:
    def execute(self, tool_config: ToolConfig, params: dict) -> ToolResult:
        return ToolResult(
            tool_name=tool_config.name, success=False,
            error=f"No function bound for '{tool_config.name}'"
        )

    async def execute_with_fn(
        self,
        tool_config: ToolConfig,
        fn: Callable,
        params: dict,
        rollback_fn: Callable | None = None,
    ) -> ToolResult:
        start = time.time()
        try:
            if params:
                import inspect
                try:
                    sig = inspect.signature(fn)
                except (ValueError, TypeError):
                    sig = None
                if sig:
                    for reserved in ("_agent_mode", "_engine", "_state_store",
                                     "_workspace", "session_id"):
                        if reserved not in sig.parameters:
                            params.pop(reserved, None)
                result = fn(**params)
            else:
                result = fn()
            if hasattr(result, "__await__"):
                result = await result
            return ToolResult(
                tool_name=tool_config.name, success=True, data={"result": result},
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as exc:
            tr = ToolResult(
                tool_name=tool_config.name, success=False, error=str(exc),
                duration_ms=(time.time() - start) * 1000,
            )
            if rollback_fn:
                try:
                    rb_result = rollback_fn(**params) if params else rollback_fn()
                    if hasattr(rb_result, "__await__"):
                        rb_result = await rb_result
                    tr.rolled_back = True
                    if isinstance(rb_result, dict) and not rb_result.get("ok", True):
                        tr.rollback_error = rb_result.get("error", "rollback returned ok=False")
                except Exception as rb_exc:
                    tr.rolled_back = True
                    tr.rollback_error = str(rb_exc)
                    tr.data["rollback_exception"] = type(rb_exc).__name__
            return tr
