"""Promotion gate — entry point for permission evaluation."""

from __future__ import annotations

from typing import Any

from arf.core.execution import Decision, Executable
from arf.promotion.strategies import AutoStrategy, AskStrategy, PlanStrategy


class Promotion:
    """Permission gate that wraps engine's interaction with executables.

    Promotion.evaluate() is called before ActionRunner.execute() for each
    executable. It returns allow/deny/ask — Engine decides how to handle each.
    """

    def __init__(self, strategy: str = "ask", **kwargs: Any) -> None:
        strategy_map = {
            "auto": AutoStrategy,
            "ask": AskStrategy,
            "plan": PlanStrategy,
        }
        strategy_cls = strategy_map.get(strategy, AskStrategy)
        self._strategy = strategy_cls(**kwargs)

    def evaluate(self, executable: Executable, **context: Any) -> Decision:
        return self._strategy.evaluate(executable, **context)
