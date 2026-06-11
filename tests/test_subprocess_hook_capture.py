"""Tests for subprocess hook output capture."""
import asyncio
from arf.hooks.runner import SubprocessHookRunner
from arf.core.plugin_context import PluginContext
from arf.core.config_base import HookDefinition


class TestSubprocessHookCapture:
    def test_stdout_captured_in_hook_data(self, tmp_path):
        script = tmp_path / "echo_hello.py"
        script.write_text("print('hello from hook')")
        hook_def = HookDefinition(
            name="test_hook",
            type="round_end",
            run=[f"python3 {script}"],
        )
        runner = SubprocessHookRunner(hook_defs=[hook_def])
        ctx = PluginContext(session_id="test")

        async def _run():
            await runner.fire("round_end", ctx)
            await asyncio.sleep(0.1)

        asyncio.run(_run())

        results = ctx.hook_data.get("_subprocess_results", [])
        assert len(results) == 1
        assert results[0]["hook"] == "test_hook"
        assert results[0]["exit_code"] == 0
        assert "hello from hook" in results[0]["stdout"]

    def test_stderr_captured(self, tmp_path):
        script = tmp_path / "err.py"
        script.write_text("import sys; sys.stderr.write('error msg')")
        hook_def = HookDefinition(
            name="err_hook",
            type="round_end",
            run=[f"python3 {script}"],
        )
        runner = SubprocessHookRunner(hook_defs=[hook_def])
        ctx = PluginContext(session_id="test")

        async def _run():
            await runner.fire("round_end", ctx)
            await asyncio.sleep(0.1)

        asyncio.run(_run())

        results = ctx.hook_data.get("_subprocess_results", [])
        assert len(results) == 1
        assert "error msg" in results[0]["stderr"]

    def test_nonzero_exit_code_captured(self, tmp_path):
        script = tmp_path / "fail.py"
        script.write_text("import sys; sys.exit(42)")
        hook_def = HookDefinition(
            name="fail_hook",
            type="round_end",
            run=[f"python3 {script}"],
        )
        runner = SubprocessHookRunner(hook_defs=[hook_def])
        ctx = PluginContext(session_id="test")

        async def _run():
            await runner.fire("round_end", ctx)
            await asyncio.sleep(0.1)

        asyncio.run(_run())

        results = ctx.hook_data.get("_subprocess_results", [])
        assert len(results) == 1
        assert results[0]["exit_code"] == 42
