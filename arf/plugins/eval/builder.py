"""BenchmarkBuilder — create EvalBenchmark from trace sessions."""
import json
import time
from pathlib import Path

from arf.plugins.eval.exceptions import EvalError
from arf.plugins.eval.models import EvalCase, EvalBenchmark


class BenchmarkBuilder:
    """Build EvalBenchmark datasets from recorded trajectories.

    Takes a TracePlugin instance and reads session trace files to
    construct rich EvalCases with expected_tools, expected_output_contains,
    and max_turns. A frozen trace snapshot is written alongside the benchmark
    so later session activity doesn't corrupt the golden reference.
    """

    def __init__(self, trace_plugin):
        self._trace = trace_plugin

    def build(self, session_id: str, name: str, *,
              benchmark_dir: str = "benchmarks",
              annotate_mode: bool = True) -> EvalBenchmark:
        events = self._trace.read_trace(session_id)
        if not events:
            raise EvalError(f"Session '{session_id}' not found in trace store")

        bm_dir = Path(benchmark_dir)
        bm_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = bm_dir / f"{name}.trace.jsonl"
        with open(snapshot_path, "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

        user_indices = [
            i for i, e in enumerate(events) if e.get("type") == "user_input"
        ]
        if not user_indices:
            raise EvalError(f"No user messages found in session '{session_id}'")

        # Collect user_annotation events by target round
        annotations_by_round: dict[int, list[dict]] = {}
        for e in events:
            if e.get("type") == "user_annotation":
                r = e.get("data", {}).get("round", 0)
                annotations_by_round.setdefault(r, []).append(e)

        cases: list[EvalCase] = []
        for i, ui in enumerate(user_indices):
            start = ui
            end = user_indices[i + 1] if i + 1 < len(user_indices) else len(events)
            case_events = events[start:end]

            source_round = i  # derive from user_input index, matching engine's 0-based interaction_round

            tool_names = self._collect_tool_names(case_events)
            turns_with_events = {e.get("turn") or 0 for e in case_events
                                 if (e.get("turn") or 0) > 0}

            # Feedback: latest user_annotation for this round
            feedback = None
            round_annotations = annotations_by_round.get(source_round, [])
            if round_annotations:
                latest = max(round_annotations, key=lambda e: e.get("timestamp", 0))
                data = latest.get("data", {})
                feedback = {
                    "rating": data.get("feedback", ""),
                    "reason": data.get("reason", ""),
                    "annotated_at": data.get("annotated_at", ""),
                }

            original_output = self._collect_final_output(case_events)
            original_tool_calls = self._collect_tool_calls(case_events)

            if annotate_mode:
                expected_output = [
                    "[待标注] 关键词列表，如: '文件已创建', '操作成功'  — 以 [待标注] 开头的条目在评测时自动忽略",
                ]
                expected_execution = [
                    "[待标注] 工具名列表，如: 'write_file', 'search_content'  — 以 [待标注] 开头的条目在评测时自动忽略",
                ]
            else:
                expected_output = []
                expected_execution = tool_names

            # Context: prior rounds' conversation for case isolation
            prior_events = events[0:ui] if i > 0 else []
            context_messages = self._build_context_messages(prior_events)

            cases.append(EvalCase(
                id=f"case_{i}",
                input=events[ui].get("data", {}).get("content", ""),
                session_id=session_id,
                source_round=source_round,
                original_output=original_output,
                original_tool_calls=original_tool_calls,
                context_messages=context_messages,
                expected_execution=expected_execution,
                expected_output_contains=expected_output,
                max_turns=len(turns_with_events) if turns_with_events else None,
                feedback=feedback,
            ))

        bm = EvalBenchmark(
            name=name,
            source_session=session_id,
            created_at=time.time(),
            cases=cases,
            trace_snapshot_path=str(snapshot_path),
        )
        bm.to_json(str(bm_dir / f"{name}.benchmark.json"))
        return bm

    def build_from_annotations(self, session_id: str, name: str, *,
                                benchmark_dir: str = "benchmarks") -> EvalBenchmark:
        """Build EvalBenchmark from annotated rounds only.

        Scans the session trace for user_annotation events and extracts
        only the annotated rounds as bare EvalCases (expected fields empty).
        A frozen trace snapshot and benchmark JSON are written alongside.
        """
        events = self._trace.read_trace(session_id)
        if not events:
            raise EvalError(f"Session '{session_id}' not found in trace store")

        bm_dir = Path(benchmark_dir)
        bm_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = bm_dir / f"{name}.trace.jsonl"
        with open(snapshot_path, "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

        # Collect annotations by round
        annotations_by_round: dict[int, list[dict]] = {}
        for e in events:
            if e.get("type") == "user_annotation":
                r = e.get("data", {}).get("round", e.get("round", 0))
                annotations_by_round.setdefault(r, []).append(e)

        if not annotations_by_round:
            bm = EvalBenchmark(
                name=name,
                source_session=session_id,
                created_at=time.time(),
                cases=[],
                trace_snapshot_path=str(snapshot_path),
            )
            bm.to_json(str(bm_dir / f"{name}.benchmark.json"))
            return bm

        # Find user_input events and their round indices
        user_inputs = [
            (i, e) for i, e in enumerate(events) if e.get("type") == "user_input"
        ]
        if not user_inputs:
            raise EvalError(f"No user messages found in session '{session_id}'")

        cases: list[EvalCase] = []
        for round_idx, (ui_pos, ui_event) in enumerate(user_inputs):
            if round_idx not in annotations_by_round:
                continue  # skip unannotated rounds

            # Event range for this round
            next_ui = next(
                (pos for pos, _ in user_inputs if pos > ui_pos), len(events)
            )
            round_events = events[ui_pos:next_ui]

            # Context: prior rounds' conversation
            prior_events = events[0:ui_pos] if round_idx > 0 else []
            context_messages = self._build_context_messages(prior_events)

            # Latest annotation for this round
            round_annotations = annotations_by_round[round_idx]
            latest = max(round_annotations, key=lambda e: e.get("timestamp", 0))
            data = latest.get("data", {})
            feedback = {
                "rating": data.get("rating", ""),
                "comment": data.get("comment", data.get("reason", "")),
                "annotated_at": data.get("annotated_at", ""),
            }

            cases.append(EvalCase(
                id=f"case_{round_idx}",
                input=ui_event.get("data", {}).get("content", ""),
                session_id=session_id,
                source_round=round_idx,
                original_output=self._collect_final_output(round_events),
                original_tool_calls=self._collect_tool_calls(round_events),
                context_messages=context_messages,
                expected_execution=[
                    "[待标注] 工具名列表，如: 'write_file', 'search_content'  — 以 [待标注] 开头的条目在评测时自动忽略",
                ],
                expected_output_contains=[
                    "[待标注] 关键词列表，如: '文件已创建', '操作成功'  — 以 [待标注] 开头的条目在评测时自动忽略",
                ],
                max_turns=None,
                feedback=feedback,
            ))

        bm = EvalBenchmark(
            name=name,
            source_session=session_id,
            created_at=time.time(),
            cases=cases,
            trace_snapshot_path=str(snapshot_path),
        )
        bm.to_json(str(bm_dir / f"{name}.benchmark.json"))
        return bm

    @staticmethod
    def _collect_tool_names(events):
        """Extract ordered tool names from tool_call_start or model_call_end events."""
        names: list[str] = []
        for e in events:
            if e.get("type") == "tool_call_start":
                data = e.get("data", {})
                name = data.get("name") or data.get("tool_name", "")
                if name and name not in names:
                    names.append(name)
            elif e.get("type") == "model_call_end":
                for tc in e.get("data", {}).get("tool_calls", []):
                    name = tc.get("name", "")
                    if name and name not in names:
                        names.append(name)
        return names

    @staticmethod
    def _collect_final_output(events):
        """Return the last non-empty model text output from the round."""
        text = ""
        for e in events:
            if e.get("type") == "model_call_end":
                content = e.get("data", {}).get("content", "")
                if content:
                    text = content
        return text

    @staticmethod
    def _collect_tool_calls(events):
        """Return paired tool call records (start + end) from the round.

        Each record: {name, arguments, success, result, error, turn}.
        For annotation reference only — not used by metrics for scoring.
        """
        starts: list[dict] = []
        ends: list[dict] = []
        for e in events:
            if e.get("type") == "tool_call_start":
                data = e.get("data", {})
                starts.append({
                    "name": data.get("name") or data.get("tool_name", ""),
                    "arguments": BenchmarkBuilder._parse_arguments(
                        data.get("arguments", "{}")
                    ),
                    "turn": e.get("turn"),
                })
            elif e.get("type") == "tool_call_end":
                data = e.get("data", {})
                ends.append({
                    "success": data.get("success", True),
                    "result": data.get("result", ""),
                    "error": data.get("error", ""),
                })

        records: list[dict] = []
        for i, start in enumerate(starts):
            end = ends[i] if i < len(ends) else {}
            records.append({
                "name": start["name"],
                "arguments": start["arguments"],
                "success": end.get("success", True),
                "result": end.get("result", ""),
                "error": end.get("error", ""),
                "turn": start["turn"],
            })
        return records

    @staticmethod
    def _parse_arguments(arguments):
        """Parse tool_call_start arguments, which may be a JSON string or dict."""
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                return {"_raw": arguments}
        return {}

    @staticmethod
    def _build_context_messages(prior_events):
        """Build context_messages from prior rounds' trace events.

        Converts trace events into simplified {role, content} messages so
        each case can be run independently without relying on prior session
        state. User and assistant messages are preserved verbatim; tool
        calls and results are summarized as readable user messages.
        """
        messages: list[dict] = []
        for e in prior_events:
            typ = e.get("type", "")
            data = e.get("data", {})

            if typ == "user_input":
                content = data.get("content", "")
                if content:
                    messages.append({"role": "user", "content": content})

            elif typ == "model_call_end":
                tool_calls = data.get("tool_calls", [])
                content = data.get("content", "")
                if tool_calls:
                    names = [tc.get("name", "?") for tc in tool_calls]
                    messages.append({
                        "role": "assistant",
                        "content": f"[调用工具: {', '.join(names)}]",
                    })
                if content:
                    messages.append({"role": "assistant", "content": content})

            elif typ == "tool_call_end":
                name = data.get("tool_name") or data.get("name", "?")
                success = data.get("success", True)
                error = data.get("error", "")
                result = data.get("result", "")
                if not success or error:
                    messages.append({
                        "role": "user",
                        "content": f"[工具 {name} 错误: {error or result}]",
                    })
                else:
                    summary = str(result)[:500]
                    messages.append({
                        "role": "user",
                        "content": f"[工具 {name} 返回: {summary}]",
                    })

        return messages

