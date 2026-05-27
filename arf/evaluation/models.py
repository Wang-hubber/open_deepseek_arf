"""Eval data models with JSON serialization."""
import json
from dataclasses import dataclass, field


@dataclass
class EvalCase:
    id: str
    input: str
    expected_tools: list[str] | None = None
    expected_output_contains: list[str] | None = None
    max_turns: int | None = None


@dataclass
class EvalBenchmark:
    name: str
    source_session: str | None = None
    created_at: float = 0.0
    cases: list[EvalCase] = field(default_factory=list)

    def to_json(self, path: str) -> None:
        data = {
            "name": self.name,
            "source_session": self.source_session,
            "created_at": self.created_at,
            "cases": [
                {
                    "id": c.id,
                    "input": c.input,
                    **({"expected_tools": c.expected_tools} if c.expected_tools else {}),
                    **({"expected_output_contains": c.expected_output_contains} if c.expected_output_contains else {}),
                    **({"max_turns": c.max_turns} if c.max_turns is not None else {}),
                }
                for c in self.cases
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, path: str) -> "EvalBenchmark":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            name=data["name"],
            source_session=data.get("source_session"),
            created_at=data.get("created_at", 0.0),
            cases=[
                EvalCase(
                    id=c["id"],
                    input=c["input"],
                    expected_tools=c.get("expected_tools"),
                    expected_output_contains=c.get("expected_output_contains"),
                    max_turns=c.get("max_turns"),
                )
                for c in data.get("cases", [])
            ],
        )
