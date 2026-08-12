from __future__ import annotations

import json
from pathlib import Path

from scripts.sanitize_schedule_fixture import sanitize_snapshot

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SOURCE_PATH = REPOSITORY_ROOT / "schedule_snapshot_v2.2.json"
FIXTURE_PATH = REPOSITORY_ROOT / "backend" / "test" / "data" / "schedule" / "schedule_v2_2_sanitized.json"


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)


def test_committed_fixture_matches_deterministic_sanitizer() -> None:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    committed = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert committed == sanitize_snapshot(source)


def test_fixture_removes_business_names_guids_notes_and_file_identity() -> None:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    sanitized = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sanitized_strings = set(_all_strings(sanitized))
    sensitive_values = {
        source["project"]["project_id"],
        source["project"]["name"],
        source["source"]["file_name"],
        source["source"]["sha256"],
        *(task["name"] for task in source["tasks"]),
        *(task["source_guid"] for task in source["tasks"]),
        *(task["notes"] for task in source["tasks"] if task["notes"]),
        *(resource["name"] for resource in source["resources"]),
        *(resource["source_guid"] for resource in source["resources"]),
        *(resource["notes"] for resource in source["resources"] if resource["notes"]),
    }

    assert sensitive_values.isdisjoint(sanitized_strings)
    assert sanitized["statistics"] == source["statistics"]
