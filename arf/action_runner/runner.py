"""ActionRunner — deterministic execution engine for hooks, tools, and model calls."""

from __future__ import annotations

import asyncio

from arf.action_runner.resolver import DependencyResolver
from arf.action_runner.rollback import RollbackManager
from arf.action_runner.retry import RetryExecutor
from arf.action_runner.scheduler import ResourceScheduler
from arf.core.execution import Executable, ExecuteResult, ExecutionError


class ActionRunner:
    """Orchestrates the 4-stage execution pipeline.

    Pipeline:
    1. DependencyResolver  -> topological sort into waves
    2. ResourceScheduler   -> within each wave, serialize resource conflicts
    3. RetryExecutor       -> execute each unit with retry policy
    4. RollbackManager     -> on failure, rollback unit + cancel downstream
    """

    @staticmethod
    async def execute(executables: list[Executable]) -> list[ExecuteResult]:
        """Execute a list of executables through the full pipeline.

        Args:
            executables: List of executables to execute.

        Returns:
            List of results in the same order as the input executables.
        """
        if not executables:
            return []

        waves = DependencyResolver.resolve(executables)
        all_results: dict[str, ExecuteResult] = {}
        failed_names: set[str] = set()

        for wave in waves:
            if not wave.executables:
                continue

            # Filter out already-cancelled executables
            active = [
                e
                for e in wave.executables
                if e.name not in failed_names
                and not any(d in failed_names for d in e.dependencies)
            ]

            if not active:
                continue

            # Schedule within wave: split into conflict-free groups
            groups = ResourceScheduler.schedule(active)

            for group in groups:
                # Execute group in parallel
                tasks = {
                    asyncio.ensure_future(RetryExecutor.execute(e)): e
                    for e in group
                }
                done, _ = await asyncio.wait(
                    tasks.keys(),
                    return_when=asyncio.ALL_COMPLETED,
                )

                for task in done:
                    result = task.result()
                    executable = tasks[task]
                    all_results[result.name] = result

                    if not result.success:
                        failed_names.add(result.name)
                        # Rollback + cancel downstream
                        all_remaining = [
                            e
                            for w in waves[waves.index(wave):]
                            for e in w.executables
                            if e.name not in all_results
                            and e.name not in failed_names
                        ]
                        await RollbackManager.handle(executable, all_remaining)
                        # Mark downstream as cancelled
                        for e in all_remaining:
                            if executable.name in e.dependencies:
                                failed_names.add(e.name)

        # Assemble final results preserving input order
        name_order = [e.name for e in executables]
        final: list[ExecuteResult] = []
        for name in name_order:
            if name in all_results:
                final.append(all_results[name])
            elif name in failed_names:
                final.append(
                    ExecuteResult(
                        name=name,
                        success=False,
                        error=ExecutionError(
                            kind="deterministic",
                            message="cancelled due to upstream failure",
                        ),
                    )
                )

        return final
