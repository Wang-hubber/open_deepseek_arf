"""EvalPlugin — offline evaluation using recorded traces.

Not hook-mounted. Eval is an offline process: replay traces, compare outputs,
calculate metrics. Wraps existing evaluation infrastructure.
"""
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("arf.plugins.eval")


class EvalPlugin:
    """Offline evaluation — not mounted on any hook.

    Reads recorded traces, replays them against a target model/config,
    and computes diff metrics. Invoked explicitly, not by lifecycle hooks.
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self._trace_dir = Path(cfg.get("trace_dir", "./data/traces"))
        self._eval_dir = Path(cfg.get("eval_dir", "./eval"))
        self._eval_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "eval"

    @property
    def hooks(self) -> dict[str, str]:
        return {}  # offline — not hook-mounted

    def set_data_dir(self, data_dir: str) -> None:
        """Override trace read directory (called by base.py)."""
        self._trace_dir = Path(data_dir) / "traces"

    def set_trace_dir(self, trace_dir: str) -> None:
        """Override trace read directory (called by base.py)."""
        self._trace_dir = Path(trace_dir)

    def set_eval_dir(self, eval_dir: str) -> None:
        """Override eval output directory (called by base.py)."""
        self._eval_dir = Path(eval_dir)
        self._eval_dir.mkdir(parents=True, exist_ok=True)

    async def on_hook(self, hook_name: str, context) -> None:
        pass  # no-op for offline plugin

    async def run_eval(self, trace_session_id: str,
                        model_config: dict[str, Any]) -> dict[str, Any]:
        """Replay a trace against a target model and compute metrics."""
        from arf.plugins.eval.runner import EvalRunner
        runner = EvalRunner(
            trace_dir=str(self._trace_dir),
            eval_dir=str(self._eval_dir),
        )
        result = await runner.run(
            trace_id=trace_session_id,
            model_config=model_config,
        )
        return result

    def list_traces(self) -> list[str]:
        """List available trace sessions for evaluation."""
        traces = []
        for f in self._trace_dir.glob("*.jsonl"):
            traces.append(f.stem)
        return sorted(traces)
