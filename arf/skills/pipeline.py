"""SkillPipeline — enforce tool execution order within a skill.

When a skill declares a pipeline, the engine MUST enforce it:
- A tool step cannot execute until all depends_on tools have completed.
- The LLM's tool calls are validated against the pipeline before execution.
- Out-of-order calls are blocked with a clear error message.

This provides a strong guarantee for reproducible multi-step tasks.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("arf.skills.pipeline")


class SkillPipeline:
    """Enforces ordered tool execution for a skill's declared pipeline.

    Tracks completed steps and validates that each tool call respects
    its declared dependencies before execution.

    Usage in engine:
        pipeline = SkillPipeline(skill_config.pipeline)
        if not pipeline.can_execute("file_writer", completed_steps={"file_reader"}):
            return error  # block: file_reader must run first
        # execute tool...
        completed_steps.add("file_writer")
    """

    def __init__(self, steps: list[dict] | None = None):
        self._steps: dict[str, list[str]] = {}  # tool_name → [depends_on...]
        self._order: list[str] = []              # declared execution order

        for step in (steps or []):
            tool = step.get("tool", "")
            deps = step.get("depends_on", [])
            if tool:
                self._steps[tool] = list(deps)
                self._order.append(tool)

        self._validate()

    def _validate(self) -> None:
        """Check pipeline integrity: no circular deps, all deps exist."""
        for tool, deps in self._steps.items():
            for dep in deps:
                if dep not in self._steps:
                    raise ValueError(
                        f"Pipeline error: '{tool}' depends on '{dep}' "
                        f"but '{dep}' is not in the pipeline steps"
                    )
        # Detect circular dependencies
        visited: set[str] = set()
        visiting: set[str] = set()
        def dfs(t: str) -> bool:
            if t in visiting:
                cycle = " → ".join(visiting) + f" → {t}"
                raise ValueError(f"Pipeline error: circular dependency: {cycle}")
            if t in visited:
                return True
            visiting.add(t)
            for dep in self._steps.get(t, []):
                dfs(dep)
            visiting.discard(t)
            visited.add(t)
            return True
        for tool in self._steps:
            dfs(tool)

    @property
    def steps(self) -> dict[str, list[str]]:
        return dict(self._steps)

    @property
    def order(self) -> list[str]:
        return list(self._order)

    def is_empty(self) -> bool:
        return len(self._steps) == 0

    def can_execute(self, tool_name: str, completed_steps: set[str] | None = None) -> bool:
        """Check if tool_name can execute given the completed steps."""
        completed = completed_steps or set()
        if tool_name not in self._steps:
            # Tool not in pipeline — allow (LLM can use other tools freely)
            return True
        deps = self._steps[tool_name]
        missing = [d for d in deps if d not in completed]
        if missing:
            logger.warning(
                "Pipeline block: '%s' depends on %s but %s not completed",
                tool_name, deps, missing,
            )
            return False
        return True

    def next_steps(self, completed_steps: set[str] | None = None) -> list[str]:
        """Return which pipeline steps are ready to execute next."""
        completed = completed_steps or set()
        ready = []
        for tool in self._order:
            if tool in completed:
                continue
            if self.can_execute(tool, completed):
                ready.append(tool)
        return ready

    def is_complete(self, completed_steps: set[str] | None = None) -> bool:
        """Check if all pipeline steps have been completed."""
        completed = completed_steps or set()
        return all(t in completed for t in self._steps)

    def validation_error(self, tool_name: str, completed_steps: set[str] | None = None) -> str:
        """Return a human-readable error for why the tool can't execute."""
        completed = completed_steps or set()
        if tool_name not in self._steps:
            return ""
        deps = self._steps[tool_name]
        missing = [d for d in deps if d not in completed]
        ready = self.next_steps(completed)
        return (
            f"Pipeline violation: '{tool_name}' requires {missing} to complete first. "
            f"Ready to execute: {ready or '(none)'}"
        )
