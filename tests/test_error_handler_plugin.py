import pytest
from arf.plugins.error_handler.plugin import ErrorHandlerPlugin
from arf.core.plugin_context import PluginContext


def _make_ctx(exc, hook_name="action", step="call_model"):
    return PluginContext(
        session_id="test",
        state={},
        current_step=step,
        hook_data={"exception": exc, "hook_name": hook_name},
    )


@pytest.mark.anyio
async def test_error_handler_transport_retry():
    plugin = ErrorHandlerPlugin()
    ctx = _make_ctx(TimeoutError("connection timed out"))

    await plugin.on_hook("error", ctx)

    decision = ctx.hook_data["_recovery_decision"]
    assert decision["recovery"] == "retry_turn"
    assert "action" not in decision
    assert decision["params"]["delay"] > 0


@pytest.mark.anyio
async def test_error_handler_context_overflow_fallback():
    plugin = ErrorHandlerPlugin()
    ctx = _make_ctx(RuntimeError("context is too large for this model"))

    await plugin.on_hook("error", ctx)

    decision = ctx.hook_data["_recovery_decision"]
    assert decision["recovery"] == "persist_state"
    assert "action" not in decision
    assert decision["params"]["compact"] is True


@pytest.mark.anyio
async def test_error_handler_unknown_error_no_decision():
    """Unknown errors leave _recovery_decision unset — engine re-raises."""
    plugin = ErrorHandlerPlugin()
    ctx = _make_ctx(ValueError("something unexpected"))

    await plugin.on_hook("error", ctx)

    assert "_recovery_decision" not in ctx.hook_data


@pytest.mark.anyio
async def test_transport_retry_exhaustion():
    plugin = ErrorHandlerPlugin(max_transport_retry=2)
    ctx = _make_ctx(TimeoutError("connection timed out"))
    ctx.state["_recovery"] = {"transport_attempts": 2, "compact_attempts": 0, "continuation_attempts": 0}

    await plugin.on_hook("error", ctx)

    assert "_recovery_decision" not in ctx.hook_data  # exhausted → no recovery set


@pytest.mark.anyio
async def test_error_handler_execute_tools_phase_retry_with_inject():
    """execute_tools phase errors → retry with inject_tool_error recovery."""
    plugin = ErrorHandlerPlugin()
    ctx = _make_ctx(ValueError("malformed params"), hook_name="error")
    ctx.hook_data["_error_phase"] = "execute_tools"
    ctx.state = {}

    await plugin.on_hook("error", ctx)

    decision = ctx.hook_data["_recovery_decision"]
    assert "action" not in decision
    assert decision["recovery"] == "inject_tool_error"
    assert decision["params"]["error"] == "malformed params"


@pytest.mark.anyio
async def test_error_handler_guard_denial_skip_with_noop():
    """Guard denial → skip with noop recovery (model sees tool_result)."""
    from arf.plugins.tool_guard.plugin import PermissionDenied
    plugin = ErrorHandlerPlugin()
    ctx = _make_ctx(PermissionDenied("blocked by guard"), hook_name="error")
    ctx.state = {}

    await plugin.on_hook("error", ctx)

    decision = ctx.hook_data["_recovery_decision"]
    assert "action" not in decision
    assert decision["recovery"] == "noop"


@pytest.mark.anyio
async def test_error_handler_message_contract_repair():
    """MessageContract violation → fallback with persist_state + repair."""
    plugin = ErrorHandlerPlugin()
    ctx = _make_ctx(
        RuntimeError("message contract violation for tool results"),
        hook_name="error",
    )
    ctx.state = {}

    await plugin.on_hook("error", ctx)

    decision = ctx.hook_data["_recovery_decision"]
    assert "action" not in decision
    assert decision["recovery"] == "persist_state"
    assert decision["params"]["repair_messages"] is True
