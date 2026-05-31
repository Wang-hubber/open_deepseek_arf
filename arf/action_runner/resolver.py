"""DependencyResolver — topological sort + wave grouping for Executables."""

from __future__ import annotations

from collections import deque

from arf.core.execution import Executable, Wave


class DependencyResolver:
    """Sorts executables into dependency-ordered waves for parallel execution.

    Each wave contains executables with no mutual dependencies. Waves execute
    sequentially; executables within a wave may execute in parallel.
    """

    @staticmethod
    def resolve(executables: list[Executable]) -> list[Wave]:
        """Topologically sort executables and group into waves.

        Uses Kahn's algorithm for topological sort, then assigns each
        executable to the wave immediately after its last dependency.

        Args:
            executables: List of executables to sort.

        Returns:
            List of waves, where each wave contains executables that can
            execute in parallel.

        Raises:
            ValueError: If a dependency is missing or a circular dependency
                is detected.
        """
        if not executables:
            return []

        name_to_exec: dict[str, Executable] = {e.name: e for e in executables}
        in_degree: dict[str, int] = {e.name: 0 for e in executables}
        dependents: dict[str, list[str]] = {e.name: [] for e in executables}

        for e in executables:
            for dep in e.dependencies:
                if dep not in name_to_exec:
                    raise ValueError(
                        f"Executable '{e.name}' depends on '{dep}' which is missing"
                    )
                in_degree[e.name] += 1
                dependents[dep].append(e.name)

        # Kahn's algorithm: process zero in-degree nodes
        queue: deque[str] = deque(
            name for name, deg in in_degree.items() if deg == 0
        )
        sorted_names: list[str] = []
        while queue:
            name = queue.popleft()
            sorted_names.append(name)
            for dep_name in dependents[name]:
                in_degree[dep_name] -= 1
                if in_degree[dep_name] == 0:
                    queue.append(dep_name)

        if len(sorted_names) != len(executables):
            remaining = set(name_to_exec.keys()) - set(sorted_names)
            raise ValueError(f"Circular dependency detected among: {remaining}")

        # Group sorted executables into waves
        waves: list[Wave] = []
        wave_map: dict[str, int] = {}
        for name in sorted_names:
            max_pred_wave = -1
            for dep in name_to_exec[name].dependencies:
                if dep in wave_map:
                    max_pred_wave = max(max_pred_wave, wave_map[dep])
            wave_idx = max_pred_wave + 1
            wave_map[name] = wave_idx
            if wave_idx >= len(waves):
                waves.append(Wave(executables=[]))
            waves[wave_idx].executables.append(name_to_exec[name])

        return waves
