from __future__ import annotations

from yuxi.schedule.preflight import preflight_schedule_input


def _codes_and_refs(document: dict) -> list[tuple[str, str | None, tuple[str, ...]]]:
    return [
        (issue.code, issue.object_ref, issue.object_refs)
        for issue in preflight_schedule_input(document)
    ]


def test_preflight_reports_duplicate_ids_in_stable_order() -> None:
    document = {
        "schema_version": "canonical_schedule_v2.7",
        "tasks": [
            {"task_id": "task:1"},
            {"task_id": "task:1"},
            {"task_id": "task:2"},
        ],
        "calendars": [{"calendar_id": "calendar:1"}, {"calendar_id": "calendar:1"}],
        "dependencies": [
            {
                "dependency_id": "dependency:1",
                "predecessor_task_id": "task:1",
                "successor_task_id": "task:2",
            },
            {
                "dependency_id": "dependency:1",
                "predecessor_task_id": "task:1",
                "successor_task_id": "task:2",
            },
        ],
        "resources": [{"resource_id": "resource:1"}, {"resource_id": "resource:1"}],
        "assignments": [
            {
                "assignment_id": "assignment:1",
                "task_id": "task:1",
                "resource_id": "resource:1",
            },
            {
                "assignment_id": "assignment:1",
                "task_id": "task:1",
                "resource_id": "resource:1",
            },
        ],
    }

    assert _codes_and_refs(document) == [
        ("ASSIGNMENT_ID_DUPLICATE", "assignment:1", ()),
        ("CALENDAR_ID_DUPLICATE", "calendar:1", ()),
        ("DEPENDENCY_ID_DUPLICATE", "dependency:1", ()),
        ("RESOURCE_ID_DUPLICATE", "resource:1", ()),
        ("TASK_ID_DUPLICATE", "task:1", ()),
    ]


def test_preflight_reports_calendar_references_and_inheritance_cycle() -> None:
    document = {
        "project": {"default_calendar_id": "calendar:missing-project"},
        "tasks": [],
        "calendars": [
            {"calendar_id": "calendar:a", "parent_calendar_id": "calendar:b"},
            {"calendar_id": "calendar:b", "parent_calendar_id": "calendar:a"},
            {"calendar_id": "calendar:c", "parent_calendar_id": "calendar:missing-parent"},
        ],
        "dependencies": [],
    }

    assert _codes_and_refs(document) == [
        ("CALENDAR_INHERITANCE_CYCLE", None, ("calendar:a", "calendar:b")),
        ("CALENDAR_PARENT_NOT_FOUND", "calendar:c", ()),
        ("PROJECT_CALENDAR_NOT_FOUND", "calendar:missing-project", ()),
    ]


def test_preflight_reports_task_parent_cycle_and_assignment_task_reference() -> None:
    document = {
        "schema_version": "canonical_schedule_v2.7",
        "tasks": [
            {"task_id": "task:a", "parent_task_id": "task:b"},
            {"task_id": "task:b", "parent_task_id": "task:a"},
        ],
        "calendars": [],
        "dependencies": [],
        "resources": [{"resource_id": "resource:1"}],
        "assignments": [
            {
                "assignment_id": "assignment:missing-task",
                "task_id": "task:missing",
                "resource_id": "resource:1",
            }
        ],
    }

    assert _codes_and_refs(document) == [
        ("ASSIGNMENT_TASK_NOT_FOUND", "assignment:missing-task", ()),
        ("TASK_PARENT_CYCLE", None, ("task:a", "task:b")),
    ]


def test_preflight_preserves_opaque_v22_assignment_semantics() -> None:
    document = {
        "schema_version": "canonical_schedule_v2.2",
        "tasks": [{"task_id": "task:1"}],
        "calendars": [],
        "dependencies": [],
        "resources": [],
        "assignments": [
            {
                "assignment_id": "assignment:opaque",
                "task_id": "source-specific-task",
                "resource_id": "source-specific-resource",
            }
        ],
    }

    assert preflight_schedule_input(document) == ()
