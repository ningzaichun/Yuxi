"""Deterministic Schedule payload used by the M4.5 performance baseline."""

from __future__ import annotations

import copy
import json
from pathlib import Path

FIXTURE_PATH = Path(__file__).resolve().parent / "test" / "data" / "schedule" / "schedule_v2_2_sanitized.json"


def build_schedule_benchmark_payload(*, task_count: int = 1_000, dependency_count: int = 5_000) -> dict:
    """Build a reproducible, valid DAG without committing a multi-megabyte fixture."""
    if task_count < 2:
        raise ValueError("task_count must be at least 2")
    maximum_dependencies = task_count * (task_count - 1) // 2
    if dependency_count < 0 or dependency_count > maximum_dependencies:
        raise ValueError("dependency_count exceeds the number of unique DAG edges")

    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    task_template = next(task for task in payload["tasks"] if task["task_type"] == "activity")
    tasks = []
    for index in range(task_count):
        task = copy.deepcopy(task_template)
        task_id = f"task:benchmark:{index:04d}"
        task.update(
            {
                "task_id": task_id,
                "source_id": index + 1,
                "source_unique_id": index + 1,
                "source_guid": f"00000000-0000-4000-8000-{index + 1:012d}",
                "parent_task_id": None,
                "wbs": str(index + 1),
                "outline_level": 1,
                "name": f"Benchmark Task {index + 1}",
                "task_type": "activity",
                "duration_minutes": 480,
                "source_work_minutes": 0,
                "baseline_0": {"exists": False, "start": None, "finish": None},
            }
        )
        tasks.append(task)

    # Enumerating edges by increasing distance produces stable, unique i -> j
    # relationships. Every edge points forward, so the generated graph is a DAG.
    dependencies = []
    for distance in range(1, task_count):
        for predecessor_index in range(task_count - distance):
            successor_index = predecessor_index + distance
            dependencies.append(
                {
                    "dependency_id": f"dependency:benchmark:{len(dependencies):05d}",
                    "predecessor_task_id": tasks[predecessor_index]["task_id"],
                    "successor_task_id": tasks[successor_index]["task_id"],
                    "type": "SS",
                    "source_type_code": 3,
                    "lag_minutes": 0,
                    "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
                }
            )
            if len(dependencies) == dependency_count:
                break
        if len(dependencies) == dependency_count:
            break

    payload["snapshot_id"] = f"benchmark-{task_count}-{dependency_count}"
    payload["source"]["file_name"] = "schedule-benchmark.synthetic.json"
    payload["project"]["project_id"] = f"project:benchmark:{task_count}:{dependency_count}"
    payload["project"]["name"] = "Schedule M4.5 Benchmark"
    payload["project"]["source_file_name"] = payload["source"]["file_name"]
    payload["tasks"] = tasks
    payload["dependencies"] = dependencies
    payload["statistics"].update(
        {
            "tasks": task_count,
            "summary_tasks": 0,
            "leaf_tasks": task_count,
            "milestones": 0,
            "dependencies": dependency_count,
            "dependency_types": {"FS": 0, "SS": dependency_count, "FF": 0, "SF": 0},
            "positive_lag_dependencies": 0,
            "negative_lag_dependencies": 0,
            "open_start_tasks": 1,
            "open_finish_tasks": 1,
            "summary_task_dependencies": 0,
            "source_schedule_dependency_violations": 0,
        }
    )
    payload["capabilities"]["cpm_recalculation"] = {"allowed": True, "reasons": []}
    return payload
