"""EvalRunner — execute benchmarks, collect metrics, produce reports."""
import json
import time
import uuid
from pathlib import Path
import os

from arf.plugins.eval.models import (
    EvalBenchmark, EvalReport, EvalSummary, EvalConfig, JudgeModelConfig,
)
from arf.plugins.eval.exceptions import EvalError, EvalJudgeError
from arf.plugins.eval.metrics import (
    SuccessRateMetric, ToolCallAccuracyMetric, ToolCallResultLLMMetric,
    TurnEfficiencyMetric,
    OutputQualityMetric, TrajectorySimilarityMetric,
    OutputContainsMetric,
    ExecutionAccuracyMetric, ReasoningSimilarityMetric,
)


class _JudgeAdapter:
    """Harness-backed chat_complete — same interface as ModelAdapter
    but every call goes through the ARF harness (trace, hooks, errors)."""

    def __init__(self, harness, system_prompt: str = "") -> None:
        self._harness = harness
        self._system_prompt = system_prompt

    async def chat_complete(self, messages, tools=None):
        """Run *messages* through the judge harness, return object with .content."""
        ctx_msgs = []
        user_msg = ""
        for m in messages:
            if m.get("role") == "system":
                ctx_msgs.append(m)
            elif m.get("role") == "user":
                user_msg = m.get("content", "")
        sid = f"judge_{uuid.uuid4().hex[:8]}"
        content = ""
        async for event in self._harness.run(
            user_msg, session_id=sid,
            context_messages=ctx_msgs or None,
        ):
            if getattr(event, "type", "") == "model_call_end":
                content = event.data.get("content", "")
        return _FakeMsg(content)


class _FakeMsg:
    """Minimal adapter-like response object."""
    def __init__(self, content: str) -> None:
        self.content = content


class EvalRunner:
    """Run evaluation benchmarks against an agent.

    Usage:
        config = EvalConfig(benchmark_path="bm.json", data_dir="./data",
                             judge=JudgeModelConfig(model="gpt-4"))
        runner = EvalRunner(config)
        report = await runner.run_online(agent)
    """

    def __init__(self, config: EvalConfig, agent_config=None):
        self._config = config
        self._benchmark: EvalBenchmark | None = None
        self._data_dir = Path(config.data_dir)
        # Resolve judge_model from agent_config before validate()
        if agent_config is not None and config.judge_model is None:
            resolved = agent_config.get_plugin_model_config("eval")
            if resolved is not None:
                config.judge_model = resolved
        # Auto-create default judge when judge_model is resolved but
        # judge (system_prompt wrapper) is still None
        if config.judge_model is not None and config.judge is None:
            config.judge = JudgeModelConfig()
        config.validate()
        # Build judge harness if judge_model is set — uses the full ARF
        # pipeline so every judge call produces a session trace.
        self._judge_harness = None
        self._judge_system_prompt = ""
        if config.judge_model is not None:
            jm = config.judge_model
            if config.judge and config.judge.system_prompt:
                self._judge_system_prompt = config.judge.system_prompt
            self._judge_harness = self._build_judge_harness(jm)
            self._judge_adapter = _JudgeAdapter(self._judge_harness,
                                                 self._judge_system_prompt)
        else:
            self._judge_adapter = None

    def _build_judge_harness(self, judge_model):
        """Build a minimal ARF harness for judge LLM calls."""
        from arf.plugins.eval.annotator import _build_plugin_harness
        return _build_plugin_harness(judge_model, data_dir=str(self._data_dir))

    # -- Public API -------------------------------------------------------

    async def run_online(self, agent, *,
                          system_prompt: str = "",
                          tools: str = "") -> EvalReport:
        """Run benchmark cases via live agent. Returns EvalReport.

        agent: BaseAgent instance (provides .run() and ._primitive_agent.input())
        system_prompt: agent's system prompt, for no-reference judge context
        tools: available tools listing, for no-reference judge context
        """
        self._benchmark = EvalBenchmark.from_json(self._config.benchmark_path)
        return await self._run(agent=agent,
                               system_prompt=system_prompt, tools=tools)

    async def run_offline(self, *, system_prompt: str = "",
                          tools: str = "") -> EvalReport:
        """Run benchmark cases against existing trace files. Returns EvalReport."""
        self._benchmark = EvalBenchmark.from_json(self._config.benchmark_path)
        return await self._run(agent=None,
                               system_prompt=system_prompt, tools=tools)

    # -- Internal ---------------------------------------------------------

    async def _run(self, agent=None, *,
                    system_prompt: str = "", tools: str = "") -> EvalReport:
        benchmark = self._benchmark

        # Auto-extract system prompt from the agent under test so the judge
        # has full context (role, constraints) when scoring outputs.
        if not system_prompt and agent is not None:
            system_prompt = getattr(
                getattr(agent, "_harness", None), "_system_prompt_text", "",
            ) or ""

        import hashlib, json
        mode = "offline" if agent is None else "online"

        # Hash the agent configuration — same benchmark + different agent
        # yields different hashes, enabling meaningful cross-run comparison.
        if agent is not None and hasattr(agent, "config"):
            agent_cfg = agent.config.model_dump(exclude_none=True)
            agent_key = json.dumps({
                "system_prompt": agent_cfg.get("system_prompt"),
                "models": agent_cfg.get("models"),
                "model_defs": agent_cfg.get("model_defs"),
                "plugins": agent_cfg.get("plugins"),
                "plugins_config": agent_cfg.get("plugins_config"),
                "tools": agent_cfg.get("tools"),
                "skills": agent_cfg.get("skills"),
            }, sort_keys=True, default=str)
            current_hash = hashlib.sha256(agent_key.encode()).hexdigest()[:12]
        elif agent is not None:
            current_hash = "unknown"
        else:
            current_hash = "offline"

        print(f"\n  Agent config hash: {current_hash}")

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
                prompt_free=prompts.get("output_quality_free"),
                system_prompt=system_prompt,
                tools=tools,
            ))
        if me.get("trajectory_similarity"):
            metrics.append(TrajectorySimilarityMetric(
                prompt=prompts.get("trajectory_similarity"),
                prompt_free=prompts.get("trajectory_similarity_free"),
                system_prompt=system_prompt,
                tools=tools,
            ))
        if me.get("output_contains", True):
            metrics.append(OutputContainsMetric())
        if me.get("execution_accuracy", True):
            metrics.append(ExecutionAccuracyMetric())
        if me.get("reasoning_similarity"):
            metrics.append(ReasoningSimilarityMetric(
                prompt=prompts.get("reasoning_similarity"),
            ))

        # Wire trace_dir and trace_snapshot_path to metrics that need them
        snapshot_path = getattr(benchmark, "trace_snapshot_path", None)
        if snapshot_path and not Path(snapshot_path).is_absolute():
            # Resolve relative to benchmark JSON directory
            bm_dir = Path(self._config.benchmark_path).parent
            snapshot_path = str(bm_dir / snapshot_path)
        for m in metrics:
            if hasattr(m, "set_trace_dir"):
                m.set_trace_dir(str(self._data_dir))
            if snapshot_path and hasattr(m, "set_trace_snapshot_path"):
                m.set_trace_snapshot_path(snapshot_path)

        judge = self._config.judge
        judge_adapter = self._judge_adapter

        # --- Header ---
        judge_model = self._config.judge_model
        judge_str = judge_model.model if judge_model else "none"
        print(f"\n Eval Report: {benchmark.name}")
        print(f" {'=' * 50}")
        print(f" Mode: {mode}   Judge: {judge_str}   Hash: {current_hash}")
        print(f"\n Cases: {len(benchmark.cases)} to run\n")

        # --- Run cases ---
        per_case = []
        passed = 0
        _run_sid_suffix = uuid.uuid4().hex[:8]
        agent_snapshot = {}

        for i, case in enumerate(benchmark.cases):
            case_start = time.time()
            # Each case gets a unique session_id — no cross-case state leakage
            sid = f"eval_{benchmark.name}_{case.id}_{_run_sid_suffix}"
            all_pass = True

            try:
                # -- Execution with context injection --
                if agent is not None:
                    await agent.run(
                        user_message=case.input,
                        session_id=sid,
                        context_messages=case.context_messages or None,
                    )
                    actual_trace = self._read_trace(sid)
                else:
                    # Offline
                    sid = self._config.trace_session_ids[i]
                    actual_trace = self._read_trace(sid)

                # -- Extract agent_snapshot from trace --
                agent_snapshot = self._extract_snapshot(actual_trace) or agent_snapshot

                # -- Compute metrics --
                case_metrics = {}
                for m in metrics:
                    try:
                        if m.requires_llm and judge_adapter is not None:
                            result = await m.compute(actual_trace, case, judge, judge_adapter)
                        else:
                            result = await m.compute(actual_trace, case, judge)
                        case_metrics.update(result)
                    except EvalJudgeError:
                        raise
                    except Exception as exc:
                        case_metrics[f"{m.name}_error"] = str(exc)[:100]

                # Compute weighted_score
                ws = self._compute_weighted_score(case_metrics, self._config.scoring_weights)
                case_metrics["weighted_score"] = ws

                duration = time.time() - case_start
                trace_stats = self._extract_trace_stats(actual_trace)
                evidence = self._extract_evidence(actual_trace)

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
                oq_reason = case_metrics.get("reason", "")
                if oq_reason:
                    # Show first sentence of judge reasoning
                    reason_short = oq_reason.split(".")[0][:120]
                    parts.append(f"reason=\"{reason_short}\"")
                ts = case_metrics.get("trajectory_similarity")
                if ts is not None and isinstance(ts, (int, float)):
                    parts.append(f"traj_sim={ts}/5")
                ea = case_metrics.get("execution_accuracy")
                if ea is not None and isinstance(ea, (int, float)):
                    parts.append(f"exec_acc={ea:.2f}")
                rs = case_metrics.get("reasoning_similarity")
                if rs is not None and isinstance(rs, (int, float)):
                    parts.append(f"reason_sim={rs}/5")
                parts.append(f"{duration:.1f}s")

                print(f"  [{status}] case_{i}: {', '.join(parts)}")
                if not all_pass:
                    print(f"         input: {case.input[:80]}")

            except EvalJudgeError:
                print(f"\n  [ABORT] Judge API failure at case_{i}, aborting run")
                raise
            except Exception as exc:
                duration = time.time() - case_start
                case_metrics = {"error": str(exc)[:200]}
                all_pass = False
                trace_stats = {"turns": 0, "tokens_in": 0, "tokens_out": 0, "tool_calls": 0}
                evidence = {"error": str(exc)[:500]}
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
                "evidence": evidence,
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
        print(f"   Execution accuracy: {summary.execution_accuracy:.2f}")
        if summary.reasoning_similarity is not None:
            print(f"   Reasoning sim:     {summary.reasoning_similarity:.1f}/5 (LLM)")

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
            agent_snapshot=agent_snapshot,
        )

        if self._config.output_path:
            report.to_json(self._config.output_path)
            print(f"\n Report saved to {self._config.output_path}")

        # Auto-archive version (content-addressable storage)
        agent_config = getattr(agent, "config", None) if agent is not None else None
        if self._config.auto_version and agent_config is not None:
            from arf.plugins.eval.version import EvalVersionManager
            vm = EvalVersionManager(self._config.benchmark_path)
            version_hash = vm.save(report, agent_config)
            print(f" Version archived: {version_hash[:12]} at eval/{report.benchmark_name}/{version_hash[:12]}.../")

        return report

    def _read_trace(self, session_id: str) -> list[dict]:
        """Read trace events from a session-scoped JSONL file."""
        trace_file = self._data_dir / session_id / "traces" / f"{session_id}.jsonl"
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
    def _extract_evidence(trace: list[dict]) -> dict:
        """Extract state-level evidence from trace for per-case reporting.

        Returns a dict with final_output, tool_calls, and error — the key
        signals needed to understand why a case passed or failed without
        opening the raw trace file.
        """
        final_output = ""
        tool_calls: list[dict] = []
        starts: list[dict] = []
        ends: list[dict] = []
        error_detail = ""

        for e in trace:
            typ = e.get("type", "")
            data = e.get("data", {})

            if typ == "model_call_end":
                content = data.get("content", "")
                # DeepSeek thinking mode may emit reasoning but empty content
                if content:
                    final_output = content
                elif data.get("reasoning_content"):
                    final_output = f"[reasoning] {data['reasoning_content']}"

            elif typ == "tool_call_start":
                starts.append({
                    "name": data.get("name") or data.get("tool_name", ""),
                    "arguments": data.get("arguments", {}),
                })

            elif typ == "tool_call_end":
                ends.append({
                    "success": data.get("success", True),
                    "result": data.get("result", ""),
                    "error": data.get("error", ""),
                })

            elif typ == "error":
                error_detail = data.get("detail", str(e))

        # Pair starts and ends by position
        for i, start in enumerate(starts):
            end = ends[i] if i < len(ends) else {}
            tool_calls.append({
                "name": start["name"],
                "arguments": start["arguments"],
                "success": end.get("success", True),
                "result": end.get("result", ""),
                "error": end.get("error", ""),
            })

        return {
            "final_output": final_output,
            "tool_calls": tool_calls,
            "error": error_detail,
        }

    @staticmethod
    def _extract_trace_stats(trace: list[dict]) -> dict:
        """Extract turn count, token usage, and tool call count from trace events."""
        turns: set[int] = set()
        tokens_in = 0
        tokens_out = 0
        tool_calls = 0
        for e in trace:
            t = e.get("turn") or 0
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
    def _extract_snapshot(trace: list[dict]) -> dict:
        """Extract agent config snapshot from snapshot_created event."""
        for e in trace:
            if e.get("type") == "snapshot_created":
                data = e.get("data", {})
                return {"hash": data.get("hash", ""), "config": data.get("config", {})}
        return {}

    @staticmethod
    def _compute_weighted_score(metrics: dict, weights: dict[str, float]) -> float:
        """Compute weighted score from metrics dict, normalizing LLM [1,5] metrics to [0,1].

        Missing metrics (None or not present) are excluded; weights rebalance proportionally.
        """
        llm_metric_names = {"output_quality", "trajectory_similarity", "reasoning_similarity"}
        available_weight = 0.0
        weighted_sum = 0.0

        for key, weight in weights.items():
            val = metrics.get(key)
            if val is None or not isinstance(val, (int, float)):
                continue
            score = val / 5.0 if key in llm_metric_names else val
            weighted_sum += score * weight
            available_weight += weight

        if available_weight == 0.0:
            return 0.0
        return weighted_sum / available_weight

    @staticmethod
    def _populate_summary(summary: EvalSummary, per_case: list[dict]) -> None:
        """Aggregate per-case metrics into summary averages."""
        metric_keys = [
            "tool_call_accuracy", "turn_efficiency", "success_rate",
            "output_quality", "trajectory_similarity",
            "output_contains",
            "execution_accuracy", "reasoning_similarity",
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

        # Aggregate weighted_score
        ws_vals = [pc.get("metrics", {}).get("weighted_score", 0.0) for pc in per_case]
        if ws_vals:
            summary.weighted_score = sum(ws_vals) / len(ws_vals)
