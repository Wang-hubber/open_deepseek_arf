"""ResourceScheduler — resource-aware serialization within a wave."""

from __future__ import annotations

from arf.core.execution import Executable


class ResourceScheduler:
    """Detects resource conflicts and serializes conflicting executables.

    Within a wave from DependencyResolver, further splits into sub-groups
    where conflicting resource accesses are serialized:

    - Same resource, both reads    -> parallel (no conflict)
    - Same resource, any write     -> serialized (conflict)
    - Different resources           -> parallel (no conflict)
    """

    @staticmethod
    def schedule(executables: list[Executable]) -> list[list[Executable]]:
        """Partition executables into conflict-free groups using graph coloring.

        Each group contains executables that can safely run in parallel.
        Groups must run sequentially.

        Args:
            executables: List of executables to schedule.

        Returns:
            List of groups, where each group is a list of executables that
            can execute in parallel without resource conflicts.
        """
        if not executables:
            return []

        # Classify each executable's resource usage
        resource_writers: dict[str, list[Executable]] = {}
        resource_readers: dict[str, list[Executable]] = {}
        no_resource: list[Executable] = []

        for e in executables:
            if not e.resources:
                no_resource.append(e)
                continue
            for res in e.resources:
                if e.side_effect:
                    resource_writers.setdefault(res, []).append(e)
                else:
                    resource_readers.setdefault(res, []).append(e)

        # Build conflict graph (adjacency list by index)
        n = len(executables)
        conflicts: list[set[int]] = [set() for _ in range(n)]
        by_name = {e.name: i for i, e in enumerate(executables)}

        for res, writers in resource_writers.items():
            # A writer conflicts with everyone on the same resource
            all_on_res = writers + resource_readers.get(res, [])
            for w in writers:
                wi = by_name[w.name]
                for other in all_on_res:
                    oi = by_name[other.name]
                    if wi != oi:
                        conflicts[wi].add(oi)
                        conflicts[oi].add(wi)

        # Greedy graph coloring to partition into independent sets
        groups: list[list[Executable]] = []
        colors: dict[int, int] = {}

        for i in range(n):
            used_colors: set[int] = set()
            for neighbor in conflicts[i]:
                if neighbor in colors:
                    used_colors.add(colors[neighbor])
            color = 0
            while color in used_colors:
                color += 1
            colors[i] = color
            if color >= len(groups):
                groups.append([])
            groups[color].append(executables[i])

        return [g for g in groups if g]
