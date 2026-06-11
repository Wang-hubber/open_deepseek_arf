import pytest
from arf.plugins.eval.exceptions import EvalError


class TestEvalError:
    def test_basic(self):
        with pytest.raises(EvalError, match="Session 'foo' not found"):
            raise EvalError("Session 'foo' not found")

    def test_is_exception(self):
        err = EvalError("msg")
        assert isinstance(err, Exception)
