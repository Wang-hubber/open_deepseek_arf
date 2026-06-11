"""EvalRunner — execute benchmarks, collect metrics, produce reports."""
import json
import time
import uuid
from pathlib import Path

from arf.plugins.eval.models import (
    EvalBenchmark, EvalReport, EvalSummary, EvalConfig, JudgeModelConfig,
)
from arf.plugins.eval.exceptions import EvalError
from arf.plugins.eval.metrics import (
    SuccessRateMetric, ToolCallAccuracyMetric, ToolCallResultLLMMetric,
    TurnEfficiencyMetric,
    OutputQualityMetric, TrajectorySimilarityMetric,
)


class EvalRunner:
    """Run evaluation benchmarks against an agent.

    Usage:
        config = EvalConfig(benchmark_path="bm.json", trace_dir="./data/traces",
                             judge=JudgeModelConfig(model="gpt-4"))
        runner = EvalRunner(config)
        report = await runner.run_online(agent.chat)
    """

    def __init__(self, config: EvalConfig):
        config.validate()
        self._config = config
        self._benchmark: EvalBenchmark | None = None
        self._trace_dir = Path(config.trace_dir)

    # -- Public API -------------------------------------------------------

    async def run_online(self, chat_fn) -> EvalReport:
        """Run benchmark cases via live agent chat. Returns EvalReport.

        chat_fn: async def chat_fn(input: str, session_id: str) -> str
        """
        self._benchmark = EvalBenchmark.from_json(self._config.benchmark_path)
        return await self._run(chat_fn=chat_fn)

    async def run_offline(self) -> EvalReport:
        """Run benchmark cases against existing trace files. Returns EvalReport."""
        self._benchmark = EvalBenchmark.from_json(self._config.benchmark_path)
        return await self._run(chat_fn=None)

    # -- Internal ---------------------------------------------------------

    async def _run(self, chat_fn=None) -> EvalReport:
        from arf.plugins.trace.snapshot import EnvSnapshotBuilder

        benchmark = self._benchmark
        mode = "offline" if chat_fn is None else "online"

        # --- Snapshot hash ---
        try:
            _, current_hash = EnvSnapshotBuilder(
                "./arf/plugins"
            ).build()
        except Exception:
            current_hash = "unknown"

        # Hash check: warn if unchanged (testing config change effects)
        bm_hash = getattr(benchmark, 'config_hash', None) or current_hash
        if bm_hash == current_hash:
            print(f"\n  Config unchanged (hash={current_hash}). "
                  f"Behavior should match baseline.\n")

        # --- Build metrics ---
        prompts = self._config.prompts
        metrics = []
        me = self._config.metrics
        if me.get("success_rate"):
            metrics.append(SuccessRateMetric())
        if me.get("tool_call_accuracy"):
            metrics.append(ToolCallAccuracyMetric())
        if me.get("tool_call_result_llm"):
            metrics.append(ToolCallResultLLMMetric(
                prompt=prompts.get("tool_call_result_llm"),
            ))
        if me.get("turn_efficiency"):
            metrics.append(TurnEfficiencyMetric())
        if me.get("output_quality"):
            metrics.append(OutputQualityMetric(
                prompt=prompts.get("output_quality"),
            ))
        if me.get("trajectory_similarity"):
            metrics.append(TrajectorySimilarityMetric(
                prompt=prompts.get("trajectory_similarity"),
            ))

        judge = self._config.judge

        # --- Header ---
        judge_str = f"{judge.model}" if judge else "none"
        print(f"\n Eval Report: {benchmark.name}")
        print(f" {'=' * 50}")
        print(f" Mode: {mode}   Judge: {judge_str}   Hash: {current_hash}")
        print(f"\n Cases: {len(benchmark.cases)} to run\n")

        # --- Run cases ---
        per_case = []
        passed = 0

        for i, case in enumerate(benchmark.cases):
            case_start = time.time()
            sid = f"eval_{benchmark.name}_{case.id}"
            all_pass = True

            try:
                # -- Get actual trace --
                if chat_fn is not None:
                    # Online
                    await chat_fn(case.input, session_id=sid)
                    actual_trace = self._read_trace(sid)
                else:
                    # Offline
                    sid = self._config.trace_session_ids[i]
                    actual_trace = self._read_trace(sid)

                # -- Compute metrics --
                case_metrics = {}
                for m in metrics:
                    try:
                        result = await m.compute(actual_trace, case, judge)
                        case_metrics.update(result)
                    except Exception as exc:
                        case_metrics[f"{m.name}_error"] = str(exc)[:100]

                duration = time.time() - case_start
                trace_stats = self._extract_trace_stats(actual_trace)

                # Determine pass/fail
                sa = case_metrics.get("success_rate", 1.0)
                ta = case_metrics.get("tool_call_accuracy", 1.0)
                all_pass = sa > 0.0 and ta >= 0.5

                if all_pass:
                    passed += 1

                # --- Per-case output ---
                status = "OK" if all_pass else "FAIL"
                tk_in = trace_stats["tokens_in"]
                tk_out = trace_stats["tokens_out"]
                parts = [
                    f"turns={trace_stats['turns']}",
                    f"tok={tk_in}/{tk_out}",
                    f"tool_acc={ta:.2f}",
                ]
                te = case_metrics.get("turn_efficiency")
                if te is not None:
                    parts.append(f"turn_eff={te:.2f}")
                oq = case_metrics.get("output_quality")
                if oq is not None and isinstance(oq, (int, float)):
                    parts.append(f"quality={oq}/5")
                ts = case_metrics.get("trajectory_similarity")
                if ts is not None and isinstance(ts, (int, float)):
                    parts.append(f"traj_sim={ts}/5")
                parts.append(f"{duration:.1f}s")

                print(f"  [{status}] case_{i}: {', '.join(parts)}")
                if not all_pass:
                    print(f"         input: {case.input[:80]}")

            except Exception as exc:
                duration = time.time() - case_start
                case_metrics = {"error": str(exc)[:200]}
                all_pass = False
                trace_stats = {"turns": 0, "tokens_in": 0, "tokens_out": 0, "tool_calls": 0}
                print(f"  [ERR] case_{i}: {exc}")

            per_case.append({
                "case_id": case.id,
                "passed": all_pass,
                "metrics": case_metrics,
                "duration_seconds": duration,
                "session_id": sid,
                "turns": trace_stats["turns"],
                "tokens_in": trace_stats["tokens_in"],
                "tokens_out": trace_stats["tokens_out"],
                "tool_calls": trace_stats["tool_calls"],
            })

        # --- Summary ---
        total = len(benchmark.cases)
        summary = EvalSummary(
            total=total, passed=passed, failed=total - passed,
            pass_rate=passed / total if total else 0.0,
        )
        self._populate_summary(summary, per_case)

        print(f"\n {'-' * 50}")
        print(f" Summary: {passed}/{total} passed ({summary.pass_rate:.1%})")
        print(f"   Avg turns:         {summary.avg_turns:.1f}")
        print(f"   Avg duration:      {summary.avg_duration_seconds:.1f}s")
        print(f"   Total duration:    {summary.total_duration_seconds:.1f}s")
        print(f"   Total tokens:      {summary.total_tokens_in} in / {summary.total_tokens_out} out")
        print(f"   Avg tool calls:    {summary.avg_tool_calls:.1f}")
        print(f"   Tool accuracy:     {summary.tool_call_accuracy:.2f}")
        print(f"   Turn efficiency:   {summary.turn_efficiency:.2f}")
        if summary.output_quality is not None:
            print(f"   Output quality:    {summary.output_quality:.1f}/5 (LLM)")
        if summary.trajectory_similarity is not None:
            print(f"   Trajectory sim:    {summary.trajectory_similarity:.1f}/5 (LLM)")

        # Show failures
        failures = [pc for pc in per_case if not pc["passed"]]
        if failures:
            print(f"\n {len(failures)} failed case(s):")
            for f in failures:
                ms = f.get("metrics", {})
                reasons = [k for k, v in ms.items()
                          if isinstance(v, (int, float)) and v < 0.5]
                print(f"   case {f['case_id']}: {reasons}")

        report = EvalReport(
            run_id=str(uuid.uuid4()),
            benchmark_name=benchmark.name,
            agent_config_hash=current_hash,
            timestamp=time.time(),
            judge_model=judge_str,
            metrics_enabled=[m.name for m in metrics],
            mode=mode,
            snapshot_hash=current_hash,
            summary=summary,
            per_case=per_case,
        )

        if self._config.output_path:
            report.to_json(self._config.output_path)
            print(f"\n Report saved to {self._config.output_path}")

        return report

    def _read_trace(self, session_id: str) -> list[dict]:
        """Read trace events from a JSONL file."""
        trace_file = self._trace_dir / f"{session_id}.jsonl"
        if not trace_file.exists():
            return []
        events = []
        with open(trace_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return events

    @staticmethod
    def _extract_trace_stats(trace: list[dict]) -> dict:
        """Extract turn count, token usage, and tool call count from trace events."""
        turns: set[int] = set()
        tokens_in = 0
        tokens_out = 0
        tool_calls = 0
        for e in trace:
            t = e.get("turn", 0)
            if t > 0:
                turns.add(t)
            if e.get("type") == "model_call_end":
                usage = e.get("data", {}).get("usage", {})
                tokens_in += usage.get("prompt_tokens", 0)
                tokens_out += usage.get("completion_tokens", 0)
                tool_calls += len(e.get("data", {}).get("tool_calls", []))
        return {
            "turns": len(turns),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tool_calls": tool_calls,
        }

    @staticmethod
    def _populate_summary(summary: EvalSummary, per_case: list[dict]) -> None:
        """Aggregate per-case metrics into summary averages."""
        metric_keys = [
            "tool_call_accuracy", "turn_efficiency", "success_rate",
            "output_quality", "trajectory_similarity",
        ]
        for key in metric_keys:
            vals = []
            for pc in per_case:
                v = pc.get("metrics", {}).get(key)
                if v is not None and isinstance(v, (int, float)):
                    vals.append(v)
            if vals:
                setattr(summary, key, sum(vals) / len(vals))

        # -- Aggregate trace-level stats --
        turns_vals = [pc.get("turns", 0) for pc in per_case]
        if turns_vals:
            summary.avg_turns = sum(turns_vals) / len(turns_vals)

        duration_vals = [pc.get("duration_seconds", 0.0) for pc in per_case]
        if duration_vals:
            summary.avg_duration_seconds = sum(duration_vals) / len(duration_vals)
            summary.total_duration_seconds = sum(duration_vals)

        tool_call_vals = [pc.get("tool_calls", 0) for pc in per_case]
        if tool_call_vals:
            summary.avg_tool_calls = sum(tool_call_vals) / len(tool_call_vals)

        summary.total_tokens_in = sum(pc.get("tokens_in", 0) for pc in per_case)
        summary.total_tokens_out = sum(pc.get("tokens_out", 0) for pc in per_case)
