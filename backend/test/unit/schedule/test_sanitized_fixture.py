from __future__ import annotations

import json
from pathlib import Path

from scripts.sanitize_schedule_fixture import sanitize_snapshot

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
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


def test_committed_fixture_is_stable_under_deterministic_sanitizer() -> None:
    committed = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert committed == sanitize_snapshot(committed)


def test_fixture_removes_business_names_guids_notes_and_file_identity() -> None:
    sanitized = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sanitized_strings = set(_all_strings(sanitized))
    assert sanitized["snapshot_id"] == "snapshot:sanitized-v2.2"
    assert sanitized["project"]["name"] == "脱敏示例项目"
    assert sanitized["source"]["file_name"] == "sanitized-schedule.mpp"
    assert all(task["name"].startswith("脱敏任务-") for task in sanitized["tasks"])
    assert all(not task["notes"] for task in sanitized["tasks"])
    assert all(resource["name"].startswith("脱敏资源-") for resource in sanitized["resources"])
    assert all(not resource["notes"] for resource in sanitized["resources"])
    assert all("北横泾" not in value for value in sanitized_strings)
