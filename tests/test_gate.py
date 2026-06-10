"""Tests for GateChecker — execution termination conditions."""

import pytest
from arf.engine.gate import GateChecker


class TestGateChecker:
    def test_not_exceeded_when_below_max_turns(self):
        gate = GateChecker(max_turns=50)
        assert not gate.is_exceeded(current_turn=10)

    def test_exceeded_when_at_max_turns(self):
        gate = GateChecker(max_turns=50)
        assert gate.is_exceeded(current_turn=50)

    def test_exceeded_when_above_max_turns(self):
        gate = GateChecker(max_turns=5)
        assert gate.is_exceeded(current_turn=10)

    def test_reason_returns_max_turns_when_exceeded(self):
        gate = GateChecker(max_turns=3)
        gate.is_exceeded(current_turn=3)
        assert gate.reason == "max_turns"

    def test_reason_empty_when_not_exceeded(self):
        gate = GateChecker(max_turns=50)
        gate.is_exceeded(current_turn=10)
        assert gate.reason == ""

    def test_none_parameter_never_exceeded(self):
        gate = GateChecker(max_turns=50, max_tokens=None, max_time_seconds=None)
        # Only max_turns matters, others are None → skipped
        assert not gate.is_exceeded(current_turn=10)
        assert gate.is_exceeded(current_turn=50)

    def test_max_turns_defaults_to_50(self):
        gate = GateChecker()
        assert not gate.is_exceeded(current_turn=49)
        assert gate.is_exceeded(current_turn=50)
