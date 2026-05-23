"""DefaultEvalRunner — run agent against dataset, compute metrics."""
import time
import uuid
from arf.core.protocols.evaluation import EvalDataset, EvalReport, EvalSummary


class DefaultEvalRunner:
    async def run(self, agent, dataset: EvalDataset, metrics: list, *, baseline: EvalReport | None = None, max_parallel: int = 1) -> EvalReport:
        per_case = []
        passed = 0
        for case in dataset.cases:
            start = time.time()
            try:
                response = await agent.chat(case.input)
                duration = time.time() - start
                case_result = {"case_id": case.id, "passed": True, "turns": 1, "tool_calls": [],
                               "duration_seconds": duration, "trace": {"turns": []}, "metrics": {}, "error": None}
                for m in metrics:
                    case_result["metrics"].update(await m.compute(case_result["trace"], case))
                per_case.append(case_result)
                passed += 1
            except Exception as e:
                per_case.append({"case_id": case.id, "passed": False, "turns": 0, "tool_calls": [],
                                 "duration_seconds": time.time() - start, "trace": {"turns": []}, "metrics": {}, "error": str(e)})
        summary = EvalSummary(
            total=len(dataset.cases), passed=passed, failed=len(dataset.cases) - passed,
            pass_rate=passed / len(dataset.cases) if dataset.cases else 0.0,
            avg_turns=sum(c["turns"] for c in per_case) / len(per_case) if per_case else 0.0,
            avg_duration_seconds=sum(c["duration_seconds"] for c in per_case) / len(per_case) if per_case else 0.0,
        )
        return EvalReport(run_id=str(uuid.uuid4()), dataset_name=dataset.name, agent_config_hash="",
                          timestamp=time.time(), summary=summary, per_case=per_case)
