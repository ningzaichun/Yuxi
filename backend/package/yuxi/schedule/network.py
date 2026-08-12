"""Dependency indexes shared by Schedule audit rules."""

from __future__ import annotations

from collections import defaultdict

from yuxi.schedule.domain.models import ScheduleDependency


class DependencyNetwork:
    """Build graph indexes once so individual rules stay linear and readable."""

    def __init__(self, task_ids: set[str], dependencies: tuple[ScheduleDependency, ...]) -> None:
        self.incoming: dict[str, list[ScheduleDependency]] = {task_id: [] for task_id in task_ids}
        self.outgoing: dict[str, list[ScheduleDependency]] = {task_id: [] for task_id in task_ids}
        self.adjacency: dict[str, set[str]] = {task_id: set() for task_id in task_ids}
        self.relation_groups: dict[tuple[str, str, str, int], list[ScheduleDependency]] = defaultdict(list)
        self._cached_cyclic_components: tuple[tuple[str, ...], ...] | None = None

        for dependency in dependencies:
            self.incoming[dependency.successor_task_id].append(dependency)
            self.outgoing[dependency.predecessor_task_id].append(dependency)
            self.adjacency[dependency.predecessor_task_id].add(dependency.successor_task_id)
            key = (
                dependency.predecessor_task_id,
                dependency.successor_task_id,
                dependency.relation_type,
                dependency.lag_minutes,
            )
            self.relation_groups[key].append(dependency)

    def cyclic_components(self) -> tuple[tuple[str, ...], ...]:
        """Return deterministic strongly connected components that form cycles."""
        if self._cached_cyclic_components is not None:
            return self._cached_cyclic_components

        # Iterative Kosaraju avoids Python recursion limits for the 5,000-task boundary.
        visited: set[str] = set()
        finish_order: list[str] = []
        for root in sorted(self.adjacency):
            if root in visited:
                continue
            stack: list[tuple[str, bool]] = [(root, False)]
            while stack:
                task_id, expanded = stack.pop()
                if expanded:
                    finish_order.append(task_id)
                    continue
                if task_id in visited:
                    continue
                visited.add(task_id)
                stack.append((task_id, True))
                stack.extend(
                    (successor_id, False)
                    for successor_id in sorted(self.adjacency[task_id], reverse=True)
                    if successor_id not in visited
                )

        reverse: dict[str, set[str]] = {task_id: set() for task_id in self.adjacency}
        for predecessor_id, successors in self.adjacency.items():
            for successor_id in successors:
                reverse[successor_id].add(predecessor_id)

        assigned: set[str] = set()
        components: list[tuple[str, ...]] = []
        for root in reversed(finish_order):
            if root in assigned:
                continue
            component: list[str] = []
            stack = [(root, False)]
            while stack:
                task_id, _ = stack.pop()
                if task_id in assigned:
                    continue
                assigned.add(task_id)
                component.append(task_id)
                stack.extend((item, False) for item in sorted(reverse[task_id], reverse=True))
            normalized = tuple(sorted(component))
            if len(normalized) > 1 or root in self.adjacency[root]:
                components.append(normalized)

        self._cached_cyclic_components = tuple(sorted(components))
        return self._cached_cyclic_components
