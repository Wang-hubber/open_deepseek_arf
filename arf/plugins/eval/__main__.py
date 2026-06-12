"""Eval CLI — python -m arf.plugins.eval run <args>."""
import argparse


def _parse_metrics(metrics_str: str) -> dict[str, bool]:
    all_metrics = {
        "tool_call_accuracy": False,
        "turn_efficiency": False,
        "success_rate": False,
        "output_quality": False,
        "trajectory_similarity": False,
    }
    for name in metrics_str.split(","):
        name = name.strip()
        if name in all_metrics:
            all_metrics[name] = True
    return all_metrics


def main():
    parser = argparse.ArgumentParser(description="ARF Eval Runner")
    parser.add_argument("--benchmark", required=True, help="Path to EvalBenchmark JSON")
    parser.add_argument("--trace-dir", default="./data/traces", help="Trace directory")
    parser.add_argument("--mode", default="online", choices=["online", "offline"])
    parser.add_argument("--traces", default="", help="Comma-separated session IDs (offline)")
    parser.add_argument("--judge-api-base", default="https://api.openai.com/v1")
    parser.add_argument("--judge-api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--judge-model", default="gpt-4")
    parser.add_argument("--metrics", default="tool_call_accuracy,turn_efficiency,success_rate",
                        help="Comma-separated metric names")
    parser.add_argument("--output", default=None, help="Output JSON path")
    parser.add_argument("--timeout", type=float, default=300.0)

    args = parser.parse_args()

    metrics = _parse_metrics(args.metrics)
    has_llm = metrics.get("output_quality") or metrics.get("trajectory_similarity")

    from arf.plugins.eval.models import EvalConfig, JudgeModelConfig
    from arf.core.model_registry import ResolvedModelConfig

    judge = None
    judge_model = None
    if has_llm:
        judge = JudgeModelConfig()
        judge_model = ResolvedModelConfig(
            model=args.judge_model,
            api_base=args.judge_api_base,
            api_key_env=args.judge_api_key_env,
            kwargs={"temperature": 0.0, "max_tokens": 2000},
        )

    trace_ids = []
    if args.traces:
        trace_ids = [s.strip() for s in args.traces.split(",") if s.strip()]

    config = EvalConfig(
        benchmark_path=args.benchmark,
        trace_dir=args.trace_dir,
        mode=args.mode,
        trace_session_ids=trace_ids,
        judge=judge,
        judge_model=judge_model,
        metrics=metrics,
        output_path=args.output,
        timeout_per_case=args.timeout,
    )

    import asyncio
    from arf.plugins.eval import EvalRunner

    runner = EvalRunner(config)

    if args.mode == "offline":
        report = asyncio.run(runner.run_offline())
        print(f"\nReport ID: {report.run_id}")
    else:
        print("Online mode: use Python API — await runner.run_online(agent.chat)")


if __name__ == "__main__":
    main()
