from arf.core.results import GuardResult
from arf.guardrails.none_guard import NoneInputGuard
from arf.guardrails.regex_clean import RegexOutputGuard
from arf.guardrails.path_check import PathCheckToolGuard


class DefaultGuardRunner:
    def __init__(self, input_guard=None, output_guard=None, tool_guard=None) -> None:
        self._input = input_guard or NoneInputGuard()
        self._output = output_guard or RegexOutputGuard()
        self._tool = tool_guard or PathCheckToolGuard()

    async def check_input(self, message: str, context: dict) -> GuardResult:
        return await self._input.check(message, context)

    async def check_output(self, message: str, context: dict) -> GuardResult:
        return await self._output.check(message, context)

    async def check_tool_params(self, tool_name: str, params: dict) -> GuardResult:
        return await self._tool.check(tool_name, params)
