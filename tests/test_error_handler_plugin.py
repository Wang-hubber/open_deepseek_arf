import pytest
from arf.plugins.error_handler.plugin import ErrorHandlerPlugin
from arf.core.plugin_context import PluginContext


def _make_ctx(exc, hook_name="dispatch", step="call_model"):
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
    assert decision["action"] == "retry"
    assert decision["params"]["delay"] > 0


@pytest.mark.anyio
async def test_error_handler_context_overflow_fallback():
    plugin = ErrorHandlerPlugin()
    ctx = _make_ctx(RuntimeError("context is too large for this model"))

    await plugin.on_hook("error", ctx)

    decision = ctx.hook_data["_recovery_decision"]
    assert decision["action"] == "fallback"
    assert decision["params"]["compact"] is True


@pytest.mark.anyio
async def test_error_handler_default_abort():
    plugin = ErrorHandlerPlugin()
    ctx = _make_ctx(ValueError("something unexpected"))

    await plugin.on_hook("error", ctx)

    decision = ctx.hook_data["_recovery_decision"]
    assert decision["action"] == "abort"


@pytest.mark.anyio
async def test_transport_retry_exhaustion():
    plugin = ErrorHandlerPlugin(max_transport_retry=2)
    ctx = _make_ctx(TimeoutError("connection timed out"))
    ctx.state["_recovery"] = {"transport_attempts": 2, "compact_attempts": 0, "continuation_attempts": 0}

    await plugin.on_hook("error", ctx)

    decision = ctx.hook_data["_recovery_decision"]
    assert decision["action"] == "abort"  # exhausted
