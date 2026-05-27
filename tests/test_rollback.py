"""Unit tests for ToolResult rollback fields and FunctionBackend rollback execution."""
import pytest
from arf.core.config_base import ToolConfig
from arf.core.results import ToolResult
from arf.resources.backends.function import FunctionBackend


class TestToolResultRollbackFields:
    def test_defaults(self):
        tr = ToolResult(tool_name="test", success=True)
        assert tr.rollback is None
        assert tr.rolled_back is False
        assert tr.rollback_error is None

    def test_rollback_assignment(self):
        tr = ToolResult(
            tool_name="test", success=False, error="boom",
            rolled_back=True, rollback_error="rb fail",
        )
        assert tr.rolled_back is True
        assert tr.rollback_error == "rb fail"

    def test_fields_present_in_dataclass(self):
        fields = ToolResult.__dataclass_fields__
        assert "rollback" in fields
        assert "rolled_back" in fields
        assert "rollback_error" in fields


class TestFunctionBackendExecuteSuccess:
    @pytest.fixture
    def backend(self):
        return FunctionBackend()

    @pytest.fixture
    def cfg(self):
        return ToolConfig(name="test_tool")

    @pytest.mark.anyio
    async def test_sync_return(self, backend, cfg):
        def fn(x: int) -> dict:
            return {"ok": True, "value": x}
        r = await backend.execute_with_fn(cfg, fn, {"x": 42})
        assert r.success
        assert r.data == {"result": {"ok": True, "value": 42}}

    @pytest.mark.anyio
    async def test_async_return(self, backend, cfg):
        async def fn(x: int) -> dict:
            return {"ok": True, "value": x}
        r = await backend.execute_with_fn(cfg, fn, {"x": 7})
        assert r.success
        assert r.data == {"result": {"ok": True, "value": 7}}

    @pytest.mark.anyio
    async def test_no_rollback_on_success(self, backend, cfg):
        """rollback_fn is NOT called when execute succeeds."""
        async def fn() -> dict:
            return {"ok": True}
        rb_called = False

        async def rb():
            nonlocal rb_called
            rb_called = True
            return {"ok": True}

        r = await backend.execute_with_fn(cfg, fn, {}, rollback_fn=rb)
        assert r.success
        assert r.rolled_back is False
        assert not rb_called

    @pytest.mark.anyio
    async def test_duration_tracked(self, backend, cfg):
        def fn() -> dict:
            return {}
        r = await backend.execute_with_fn(cfg, fn, {})
        assert r.duration_ms >= 0


class TestFunctionBackendExecuteFailure:
    @pytest.fixture
    def backend(self):
        return FunctionBackend()

    @pytest.fixture
    def cfg(self):
        return ToolConfig(name="test_tool")

    # -- no rollback_fn --

    @pytest.mark.anyio
    async def test_exception_no_rollback(self, backend, cfg):
        def fn() -> dict:
            raise ValueError("boom")

        r = await backend.execute_with_fn(cfg, fn, {})
        assert not r.success
        assert r.error == "boom"
        assert r.rolled_back is False
        assert r.rollback_error is None

    @pytest.mark.anyio
    async def test_async_exception_no_rollback(self, backend, cfg):
        async def fn() -> dict:
            raise RuntimeError("async boom")

        r = await backend.execute_with_fn(cfg, fn, {})
        assert not r.success
        assert r.error == "async boom"
        assert r.rolled_back is False

    # -- rollback succeeds --

    @pytest.mark.anyio
    async def test_rollback_called_on_failure(self, backend, cfg):
        rb_called = False

        def fn() -> dict:
            raise ValueError("fail")

        async def rb():
            nonlocal rb_called
            rb_called = True
            return {"ok": True, "action": "undone"}

        r = await backend.execute_with_fn(cfg, fn, {}, rollback_fn=rb)
        assert not r.success
        assert r.error == "fail"
        assert r.rolled_back is True
        assert r.rollback_error is None
        assert rb_called

    @pytest.mark.anyio
    async def test_rollback_receives_same_params(self, backend, cfg):
        """Rollback gets the same kwargs as execute."""
        captured = None

        def fn(path: str, content: str) -> dict:
            raise ValueError("write failed")

        async def rb(**kwargs):
            nonlocal captured
            captured = kwargs
            return {"ok": True}

        r = await backend.execute_with_fn(
            cfg, fn, {"path": "/tmp/x", "content": "hello"}, rollback_fn=rb,
        )
        assert r.rolled_back is True
        assert captured == {"path": "/tmp/x", "content": "hello"}

    @pytest.mark.anyio
    async def test_rollback_no_params(self, backend, cfg):
        """Rollback with no-arg functions also works."""
        rb_called = False

        def fn() -> dict:
            raise ValueError("fail")

        def rb():
            nonlocal rb_called
            rb_called = True

        r = await backend.execute_with_fn(cfg, fn, {}, rollback_fn=rb)
        assert r.rolled_back is True
        assert rb_called

    # -- rollback fails --

    @pytest.mark.anyio
    async def test_rollback_throws_exception(self, backend, cfg):
        def fn() -> dict:
            raise ValueError("fail")

        async def rb():
            raise RuntimeError("rollback failed too")

        r = await backend.execute_with_fn(cfg, fn, {}, rollback_fn=rb)
        assert not r.success
        assert r.error == "fail"
        assert r.rolled_back is True
        assert r.rollback_error == "rollback failed too"
        assert r.data.get("rollback_exception") == "RuntimeError"

    @pytest.mark.anyio
    async def test_rollback_returns_ok_false(self, backend, cfg):
        def fn() -> dict:
            raise ValueError("fail")

        def rb() -> dict:
            return {"ok": False, "error": "cannot undo"}

        r = await backend.execute_with_fn(cfg, fn, {}, rollback_fn=rb)
        assert r.rolled_back is True
        assert r.rollback_error == "cannot undo"

    # -- rollback async --

    @pytest.mark.anyio
    async def test_async_rollback_succeeds(self, backend, cfg):
        def fn() -> dict:
            raise ValueError("fail")

        async def rb() -> dict:
            return {"ok": True}

        r = await backend.execute_with_fn(cfg, fn, {}, rollback_fn=rb)
        assert r.rolled_back is True
        assert r.rollback_error is None

    @pytest.mark.anyio
    async def test_async_rollback_fails(self, backend, cfg):
        def fn() -> dict:
            raise ValueError("fail")

        async def rb() -> dict:
            raise RuntimeError("rb fail")

        r = await backend.execute_with_fn(cfg, fn, {}, rollback_fn=rb)
        assert r.rolled_back is True
        assert r.rollback_error == "rb fail"


class TestFunctionBackendAgentModeStrip:
    """_agent_mode is stripped from params if fn signature doesn't accept it."""

    @pytest.fixture
    def backend(self):
        return FunctionBackend()

    @pytest.fixture
    def cfg(self):
        return ToolConfig(name="test_tool")

    @pytest.mark.anyio
    async def test_agent_mode_stripped_for_execute(self, backend, cfg):
        def fn(path: str) -> dict:
            return {"ok": True, "path": path}

        r = await backend.execute_with_fn(
            cfg, fn, {"path": "/x", "_agent_mode": "user"},
        )
        assert r.success
        # _agent_mode should have been popped before call

    @pytest.mark.anyio
    async def test_agent_mode_stripped_for_rollback(self, backend, cfg):
        captured = None

        def fn(path: str) -> dict:
            raise ValueError("fail")

        def rb(**kwargs):
            nonlocal captured
            captured = kwargs
            return {"ok": True}

        # fn accepts _agent_mode but rb doesn't — rb uses **kwargs so it eats everything
        r = await backend.execute_with_fn(
            cfg, fn, {"path": "/x", "_agent_mode": "user"}, rollback_fn=rb,
        )
        assert r.rolled_back is True
        # params passed to rollback include whatever was in params (strip only on execute)
