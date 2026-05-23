"""FunctionBackend — call Python functions directly."""
import time
from arf.core.config_base import ToolConfig
from arf.core.results import ToolResult


class FunctionBackend:
    def execute(self, tool_config: ToolConfig, params: dict) -> ToolResult:
        return ToolResult(
            tool_name=tool_config.name, success=False,
            error=f"No function bound for '{tool_config.name}'"
        )

    async def execute_with_fn(self, tool_config: ToolConfig, fn, params: dict) -> ToolResult:
        start = time.time()
        try:
            result = fn(**params) if params else fn()
            if hasattr(result, "__await__"):
                result = await result
            return ToolResult(
                tool_name=tool_config.name, success=True, data={"result": result},
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return ToolResult(
                tool_name=tool_config.name, success=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )
