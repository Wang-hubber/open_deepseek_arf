"""HarnessConfig — Pydantic model for harness.yaml."""
from __future__ import annotations
from pydantic import BaseModel


class ToolSource(BaseModel):
    type: str                        # "directory" | "mcp" | "kernel"
    path: str = ""                   # for directory
    url: str = ""                    # for mcp
    names: list[str] = []            # for kernel


class HarnessConfig(BaseModel):
    plugins: list[str] = []
    tools: list[ToolSource] = []
    max_turns: int = 50
    tool_timeout: float = 60.0

    @classmethod
    def from_yaml(cls, path: str) -> HarnessConfig:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)
