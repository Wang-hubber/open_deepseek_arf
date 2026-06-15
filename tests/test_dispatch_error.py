"""Tests for _dispatch_error — the unified error dispatch method."""
import pytest
from arf.engine.control_plane import ControlPlane, SessionAbortedError
from arf.engine.checkpoint import InMemoryStateStore
from arf.testing import InMemoryToolExecutor
from arf.core.plugin_context import PluginContext


def _make_ctx():
    return PluginContext(
        session_id="test",
        state={},
        current_step="test",
        hook_data={},
    )


class TestDispatchError:
    @pytest.mark.anyio
    async def test_known_error_with_recovery_executes_recovery_and_returns_false(self):
        """When hook returns a recovery, _dispatch_error executes it and returns False."""
        cp = ControlPlane(
            state_store=InMemoryStateStore(),
            tool_executor=InMemoryToolExecutor({}),
        )
        cp._recovery_handlers = {"noop": _noop_handler}
        cp._handle_error = _make_handle_error_returns({"recovery": "noop"})

        result = await cp._dispatch_error(ValueError("known"), {}, _make_ctx())

        assert result is False

    @pytest.mark.anyio
    async def test_decision_without_recovery_returns_false(self):
        """Empty decision (no recovery key) — still known, returns False."""
        cp = ControlPlane(
            state_store=InMemoryStateStore(),
            tool_executor=InMemoryToolExecutor({}),
        )
        cp._handle_error = _make_handle_error_returns({})

        result = await cp._dispatch_error(ValueError("known"), {}, _make_ctx())

        assert result is False

    @pytest.mark.anyio
    async def test_handle_error_raises_session_aborted_returns_true(self):
        """When _handle_error raises SessionAbortedError, returns True (break)."""
        cp = ControlPlane(
            state_store=InMemoryStateStore(),
            tool_executor=InMemoryToolExecutor({}),
        )
        cp._handle_error = _make_handle_error_raises(
            SessionAbortedError("aborted by hook")
        )

        result = await cp._dispatch_error(ValueError("bad"), {}, _make_ctx())

        assert result is True

    @pytest.mark.anyio
    async def test_handle_error_raises_returns_true(self):
        """When _handle_error raises, _dispatch_error returns True (break)."""
        cp = ControlPlane(
            state_store=InMemoryStateStore(),
            tool_executor=InMemoryToolExecutor({}),
        )
        cp._handle_error = _make_handle_error_raises(ValueError("unknown"))

        result = await cp._dispatch_error(ValueError("bad"), {}, _make_ctx())

        assert result is True

    @pytest.mark.anyio
    async def test_recovery_handler_receives_state_ctx_params(self):
        """Recovery handler is called with (state, ctx, params)."""
        captured = {}
        async def capture_handler(state, ctx, params):
            captured["state"] = state
            captured["ctx"] = ctx
            captured["params"] = params

        cp = ControlPlane(
            state_store=InMemoryStateStore(),
            tool_executor=InMemoryToolExecutor({}),
        )
        cp._recovery_handlers = {"test_handler": capture_handler}
        cp._handle_error = _make_handle_error_returns({
            "recovery": "test_handler",
            "params": {"key": "val"},
        })

        state = {"some": "state"}
        ctx = _make_ctx()

        await cp._dispatch_error(ValueError("x"), state, ctx)

        assert captured["state"] is state
        assert captured["ctx"] is ctx
        assert captured["params"] == {"key": "val"}


# -- test helpers --

async def _noop_handler(state, ctx, params):
    pass


def _make_handle_error_returns(decision: dict):
    async def _handler(exc, ctx):
        return decision
    return _handler


def _make_handle_error_raises(exc: Exception):
    async def _handler(inner_exc, ctx):
        raise exc
    return _handler
