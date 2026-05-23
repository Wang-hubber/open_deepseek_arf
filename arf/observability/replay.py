"""FileReplayController — Record sessions to JSON, replay deterministically."""
import json
from pathlib import Path
from arf.core.events import AgentEvent
from arf.core.protocols.replay import ReplayTrace, TurnRecord


class FileReplayController:
    def __init__(self, traces_dir: str | Path = "./traces") -> None:
        self._dir = Path(traces_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._recording: ReplayTrace | None = None

    async def start_recording(self, session_id: str) -> None:
        self._recording = ReplayTrace(session_id=session_id, agent_config_hash="", arf_version="1.0")

    async def record_model_output(self, session_id: str, turn: int, model_name: str, output: str) -> None:
        if self._recording:
            self._recording.turns.append(TurnRecord(turn=turn, model_name=model_name, model_output=output))

    async def record_tool_result(self, session_id: str, turn: int, tool_name: str, params: dict, result: dict) -> None:
        if self._recording and self._recording.turns:
            self._recording.turns[-1].tool_calls.append(
                {"tool_name": tool_name, "params": params, "result": result, "timestamp": 0})

    async def stop_recording(self) -> ReplayTrace:
        trace = self._recording
        if trace:
            path = self._dir / f"{trace.session_id}.json"
            path.write_text(json.dumps({"session_id": trace.session_id, "ar_version": trace.arf_version, "turns": [
                {"turn": t.turn, "model_name": t.model_name, "model_output": t.model_output, "tool_calls": t.tool_calls}
                for t in trace.turns
            ]}, indent=2, default=str), encoding="utf-8")
        self._recording = None
        return trace

    async def replay(self, trace: ReplayTrace, *, start_turn: int = 0, breakpoints: list[int] | None = None):
        for turn in trace.turns:
            if turn.turn < start_turn:
                continue
            if breakpoints and turn.turn in breakpoints:
                input(f"[BP] Turn {turn.turn}. Enter to continue...")
            yield AgentEvent(type="model_call_end", data={"output": turn.model_output, "model": turn.model_name}, turn=turn.turn)
            for tc in turn.tool_calls:
                yield AgentEvent(type="tool_call_result", data={"tool_name": tc["tool_name"], "result": tc["result"]}, turn=turn.turn)
