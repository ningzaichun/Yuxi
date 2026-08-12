from __future__ import annotations

import json
from pathlib import Path

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "package"
    / "yuxi"
    / "schedule"
    / "contracts"
    / "schemas"
    / "canonical_schedule_v2_2.schema.json"
)


def test_committed_schema_matches_generated_contract() -> None:
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert committed == CanonicalScheduleV22.model_json_schema()
