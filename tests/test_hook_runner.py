"""Tests for SubprocessHookRunner — fire, timeout, ordering, exit code 2 injection."""
import asyncio
import os

import pytest

from arf.core.config_base import HookDefinition


def _hook(name: str, event_type: str, cmd: str = "echo ok", env: dict | None = None, timeout: str = "30s"):
    return HookDefinition(
        name=name, type=event_type,
        run=[cmd], env=env or {}, timeout=timeout,
    )


class TestFireParallel:
    """All hooks for an event type run in parallel."""

    def test_fire_returns_all_results(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([
                _hook("a", "round_end", "echo a"),
                _hook("b", "round_end", "echo b"),
                _hook("c", "round_end", "echo c"),
            ])
            results = await runner.fire("round_end", {"session_id": "s1"})
            names = {r.hook_name for r in results}
            assert names == {"a", "b", "c"}

        asyncio.run(_test())

    def test_fire_only_matching_event_type(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([
                _hook("a", "round_end", "echo a"),
                _hook("b", "session_start", "echo b"),
            ])
            results = await runner.fire("round_end", {})
            assert len(results) == 1
            assert results[0].hook_name == "a"

        asyncio.run(_test())

    def test_fire_empty_returns_empty_list(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([])
            results = await runner.fire("round_end", {})
            assert results == []

        asyncio.run(_test())

    def test_successful_hook_has_zero_exit_code(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([_hook("ok", "round_end", "echo done")])
            results = await runner.fire("round_end", {})
            assert results[0].exit_code == 0
            assert "done" in results[0].stdout

        asyncio.run(_test())


class TestTimeout:
    """Hook timeout handling."""

    def test_timeout_kills_process(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([
                _hook("slow", "round_end", "sleep 60", timeout="0.1s"),
            ])
            results = await runner.fire("round_end", {})
            assert results[0].exit_code == -1
            assert "timeout" in results[0].stderr.lower()

        asyncio.run(_test())

    def test_timeout_does_not_block_other_hooks(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([
                _hook("slow", "round_end", "sleep 60", timeout="0.1s"),
                _hook("fast", "round_end", "echo fast"),
            ])
            results = await runner.fire("round_end", {})
            names = {r.hook_name for r in results}
            assert "fast" in names
            assert "slow" in names

        asyncio.run(_test())

    def test_default_timeout_is_30_seconds(self):
        from arf.hooks.runner import _pars_timeout

        h = HookDefinition(name="t", type="round_end", run=["echo x"], env={}, timeout="")
        assert _pars_timeout(h.timeout) == 30.0


class TestExitCode2Injection:
    """Exit code 2 triggers message injection via injected_message field."""

    def test_exit_code_2_sets_injected_message(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([
                _hook("injector", "round_end",
                      "python3 -c \"import sys; print('[Hook: injector] injected msg'); sys.exit(2)\""),
            ])
            results = await runner.fire("round_end", {})
            assert results[0].exit_code == 2
            assert results[0].injected_message is not None
            assert "injected msg" in results[0].injected_message

        asyncio.run(_test())

    def test_exit_code_1_no_injection(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([
                _hook("fail", "round_end",
                      "python3 -c \"import sys; print('error msg'); sys.exit(1)\""),
            ])
            results = await runner.fire("round_end", {})
            assert results[0].exit_code == 1
            assert results[0].injected_message is None

        asyncio.run(_test())


class TestFailedHookIsolation:
    """A failed hook does not prevent other hooks from running."""

    def test_failing_hook_does_not_block_others(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([
                _hook("fail", "round_end", "python3 -c \"import sys; sys.exit(1)\""),
                _hook("ok", "round_end", "echo still ok"),
            ])
            results = await runner.fire("round_end", {})
            names = {r.hook_name for r in results}
            assert "ok" in names
            assert "fail" in names

        asyncio.run(_test())

    def test_multi_cmd_stops_on_first_failure(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([
                HookDefinition(
                    name="multi", type="round_end",
                    run=[
                        "python3 -c \"import sys; sys.exit(1)\"",
                        "echo 'should not run'",
                    ],
                    env={}, timeout="30s",
                ),
            ])
            results = await runner.fire("round_end", {})
            assert results[0].exit_code == 1
            assert "should not run" not in results[0].stdout

        asyncio.run(_test())


class TestEnvVarSubstitution:
    """Environment variable template substitution."""

    def test_env_var_substitution(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([
                HookDefinition(
                    name="envtest", type="round_end",
                    run=["echo $ARF_TEST_VAL"],
                    env={"ARF_TEST_VAL": "$ARF_SESSION_ID"},
                    timeout="30s",
                ),
            ])
            results = await runner.fire("round_end", {"session_id": "my-session-123"})
            assert "my-session-123" in results[0].stdout

        asyncio.run(_test())


class TestSetOrder:
    """Hook ordering via set_order."""

    def test_set_order_respects_sequence(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([
                _hook("a", "round_start", "echo a"),
                _hook("b", "round_start", "echo b"),
                _hook("c", "round_start", "echo c"),
            ])
            runner.set_order("round_start", ["c", "a"])

            results = await runner.fire("round_start", {})
            names = [r.hook_name for r in results]
            assert names[0] == "c"
            assert names[1] == "a"
            assert names[2] == "b"

        asyncio.run(_test())

    def test_set_order_unknown_hooks_ignored(self):
        from arf.hooks.runner import SubprocessHookRunner

        async def _test():
            runner = SubprocessHookRunner([
                _hook("a", "round_start", "echo a"),
            ])
            runner.set_order("round_start", ["nonexistent", "a"])

            results = await runner.fire("round_start", {})
            assert results[0].hook_name == "a"

        asyncio.run(_test())


class TestGetDefinitions:
    """get_definitions returns all hooks."""

    def test_get_definitions_flattens_all_types(self):
        from arf.hooks.runner import SubprocessHookRunner

        runner = SubprocessHookRunner([
            _hook("a", "round_start", "echo a"),
            _hook("b", "round_end", "echo b"),
        ])
        defs = runner.get_definitions()
        assert len(defs) == 2


class TestParseTimeout:
    """Timeout string parsing."""

    def test_pars_seconds(self):
        from arf.hooks.runner import _pars_timeout
        assert _pars_timeout("30s") == 30.0
        assert _pars_timeout("0.5s") == 0.5

    def test_pars_minutes(self):
        from arf.hooks.runner import _pars_timeout
        assert _pars_timeout("5m") == 300.0

    def test_pars_hours(self):
        from arf.hooks.runner import _pars_timeout
        assert _pars_timeout("1h") == 3600.0

    def test_pars_default(self):
        from arf.hooks.runner import _pars_timeout
        assert _pars_timeout("") == 30.0
