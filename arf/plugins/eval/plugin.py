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
        self._data_dir = Path(cfg.get("data_dir", "./data"))
        self._eval_dir = Path(cfg.get("eval_dir", "./eval"))
        self._eval_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "eval"

    @property
    def hooks(self) -> dict[str, str]:
        return {}  # offline — not hook-mounted

    def set_data_dir(self, data_dir: str) -> None:
        """Override data directory (called by base.py)."""
        self._data_dir = Path(data_dir)

    def set_eval_dir(self, eval_dir: str) -> None:
        """Override eval output directory (called by base.py)."""
        self._eval_dir = Path(eval_dir)
        self._eval_dir.mkdir(parents=True, exist_ok=True)

    def annotate(
        self,
        session_id: str,
        round: int,
        rating: str,
        comment: str = "",
    ) -> None:
        """Write a user_annotation event to the session trace JSONL.

        Called by downstream apps to mark a round as good/bad during
        conversation. Side effect only — does not interrupt the session.
        """
        import json
        import time
        from datetime import datetime, timezone

        trace_dir = self._data_dir / session_id / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_file = trace_dir / f"{session_id}.jsonl"

        event = {
            "type": "user_annotation",
            "session_id": session_id,
            "round": round,
            "turn": 0,
            "timestamp": time.time(),
            "data": {
                "rating": rating,
                "comment": comment,
                "annotated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        with open(trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def on_hook(self, hook_name: str, context) -> None:
        pass  # no-op for offline plugin

    async def run_eval(self, trace_session_id: str,
                        model_config: dict[str, Any]) -> dict[str, Any]:
        """Replay a trace against a target model and compute metrics."""
        from arf.plugins.eval.runner import EvalRunner
        runner = EvalRunner(
            data_dir=str(self._data_dir),
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
        for d in self._data_dir.iterdir():
            trace_dir = d / "traces"
            if d.is_dir() and trace_dir.exists():
                for f in trace_dir.glob("*.jsonl"):
                    traces.append(f.stem)
        return sorted(traces)
